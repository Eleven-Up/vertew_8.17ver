"""Data_Store repository: SQLite schema and persistence with retry semantics.

This module owns all SQLite access for the Conversation_Server. It provides a
single-row ``store_info`` table (upserted) and an insert-only ``conversation_turn``
log, satisfying the persistence requirements (Req 8.1, 8.2, 8.5).

Write operations route through a single low-level boundary (:meth:`DataStore._write`)
so retry logic is centralized and the boundary can be mocked to inject failures in
tests (Req 8.3). Conversation-turn writes are attempted at most twice (initial plus
at most one retry) before the failure is reported. Committed records are never
modified: ``conversation_turn`` is insert-only and ``store_info`` is a single-row
upsert.

At startup, :meth:`load_store_info` loads the persisted store info. If that load
fails, :attr:`load_failed` is set and :attr:`turns_blocked` becomes ``True``, which
the server uses to block new Conversation_Turns until the information can be loaded
(Req 8.4).
"""

from __future__ import annotations

import sqlite3
import json
import uuid
from datetime import datetime, timezone

from models import (
    ConversationTurn,
    CustomerSession,
    LanguageSource,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    QAEntry,
    StoreInfo,
)

# Maximum number of write attempts: the initial attempt plus at most one retry
# (Req 8.3).
_MAX_WRITE_ATTEMPTS = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS store_info (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    store_name  TEXT NOT NULL,
    products    TEXT NOT NULL,
    persona     TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_turn (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_text  TEXT NOT NULL,
    character_text TEXT NOT NULL,
    completed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stores (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    currency    TEXT NOT NULL DEFAULT 'MYR',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id              TEXT NOT NULL,
    store_id        TEXT NOT NULL,
    name_json       TEXT NOT NULL,
    description_json TEXT NOT NULL,
    price_minor     INTEGER NOT NULL CHECK (price_minor >= 0),
    currency        TEXT NOT NULL,
    available       INTEGER NOT NULL DEFAULT 1,
    image           TEXT NOT NULL,
    spice_level     INTEGER NOT NULL DEFAULT 0 CHECK (spice_level BETWEEN 0 AND 3),
    ingredients_json TEXT NOT NULL DEFAULT '{}',
    allergens_json  TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (store_id, id),
    FOREIGN KEY (store_id) REFERENCES stores(id)
);

CREATE TABLE IF NOT EXISTS customer_sessions (
    id              TEXT PRIMARY KEY,
    store_id        TEXT NOT NULL,
    language        TEXT NOT NULL,
    language_source TEXT NOT NULL,
    order_id        TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id)
);

CREATE TABLE IF NOT EXISTS orders (
    id                TEXT PRIMARY KEY,
    store_id          TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    order_number      INTEGER NOT NULL,
    status            TEXT NOT NULL,
    customer_language TEXT NOT NULL,
    order_source      TEXT NOT NULL,
    total_minor       INTEGER NOT NULL,
    currency          TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (store_id, order_number),
    FOREIGN KEY (store_id) REFERENCES stores(id),
    FOREIGN KEY (session_id) REFERENCES customer_sessions(id)
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id          TEXT NOT NULL,
    product_id        TEXT NOT NULL,
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    product_name_json TEXT NOT NULL,
    unit_price_minor  INTEGER NOT NULL,
    PRIMARY KEY (order_id, product_id),
    FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE IF NOT EXISTS session_drafts (
    session_id  TEXT PRIMARY KEY,
    items_json  TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES customer_sessions(id)
);

CREATE TABLE IF NOT EXISTS media_assets (
    store_id    TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    media_type  TEXT NOT NULL,
    url         TEXT NOT NULL,
    label       TEXT NOT NULL,
    PRIMARY KEY (store_id, event_type),
    FOREIGN KEY (store_id) REFERENCES stores(id)
);

CREATE TABLE IF NOT EXISTS qa_entries (
    id           TEXT PRIMARY KEY,
    store_id     TEXT NOT NULL,
    question     TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    answer_json  TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'general',
    status       TEXT NOT NULL DEFAULT 'approved',
    source       TEXT NOT NULL DEFAULT 'curated',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id)
);
"""

# Demo catalog. Each entry is:
#   (id, name{lang}, description{lang}, price_minor, image,
#    spice_level, ingredients{lang}, allergens(tuple))
# spice_level is 0..3 (0 = not spicy); allergens are canonical lowercase English
# tags (empty = none declared). These structured fields are what the assistant
# reads to answer "what is in this?" / "how spicy is it?" questions.
DEMO_PRODUCTS = (
    ("watermelon", {"en": "Watermelon", "ko": "수박", "ms": "Tembikai"}, {"en": "Cool and refreshing watermelon", "ko": "시원하고 상쾌한 수박", "ms": "Tembikai yang sejuk dan menyegarkan"}, 400, "/images/watermelon.png", 0, {"en": "Fresh-cut watermelon, nothing added", "ko": "갓 자른 수박, 첨가물 없음", "ms": "Tembikai potong segar, tanpa tambahan"}, ()),
    ("mango", {"en": "Mango", "ko": "망고", "ms": "Mangga"}, {"en": "Sweet and fresh mango", "ko": "달고 신선한 망고", "ms": "Mangga manis dan segar"}, 500, "/images/mango.png", 0, {"en": "Fresh mango", "ko": "신선한 망고", "ms": "Mangga segar"}, ()),
    ("banana", {"en": "Banana", "ko": "바나나", "ms": "Pisang"}, {"en": "Soft and naturally sweet banana", "ko": "부드럽고 자연스럽게 달콤한 바나나", "ms": "Pisang lembut dan manis semula jadi"}, 300, "/images/banana.png", 0, {"en": "Fresh banana", "ko": "신선한 바나나", "ms": "Pisang segar"}, ()),
    ("apple", {"en": "Apple", "ko": "사과", "ms": "Epal"}, {"en": "Crisp and juicy apple", "ko": "아삭하고 과즙이 풍부한 사과", "ms": "Epal rangup dan berjus"}, 350, "/images/apple.png", 0, {"en": "Fresh-cut apple", "ko": "갓 자른 사과", "ms": "Epal potong segar"}, ()),
)

# Demo FAQ/Q&A knowledge base. Each entry is (id, question, answer{lang}, category).
# These are curated answers the assistant prefers over free generation; the vendor
# edits them and approves learned ones. Values here are placeholders for the demo.
DEMO_QA = (
    ("qa_payment", "How can I pay / how do I order?", {
        "en": "Just scan the QR code with your phone to order and pay — no app needed.",
        "ko": "휴대폰으로 QR 코드를 스캔하면 주문과 결제가 돼요. 앱 설치는 필요 없어요.",
        "ms": "Imbas kod QR dengan telefon anda untuk pesan dan bayar — tanpa aplikasi.",
    }, "payment"),
    ("qa_hours", "What time are you open?", {
        "en": "We are here every evening from 6pm until midnight.",
        "ko": "매일 저녁 6시부터 자정까지 영업해요.",
        "ms": "Kami buka setiap petang dari jam 6 hingga tengah malam.",
    }, "hours"),
    ("qa_halal", "Is the food halal?", {
        "en": "We sell only fresh-cut fruit with nothing added, so it suits a halal diet.",
        "ko": "저희는 첨가물 없이 갓 자른 과일만 팔아서 할랄 식단에도 괜찮아요.",
        "ms": "Kami hanya menjual buah potong segar tanpa tambahan, jadi sesuai untuk diet halal.",
    }, "diet"),
    ("qa_location", "Where will you be tomorrow?", {
        "en": "We move around the night market — check our sign or ask the owner for tomorrow's spot.",
        "ko": "야시장 안에서 자리를 옮겨요. 내일 위치는 간판을 보시거나 사장님께 여쭤봐 주세요.",
        "ms": "Kami berpindah di sekitar pasar malam — lihat papan tanda kami atau tanya tuan kedai untuk lokasi esok.",
    }, "location"),
)


class StorageError(RuntimeError):
    """Raised when a persistence operation fails after exhausting its retries."""


class DataStore:
    """SQLite-backed repository for store info and the conversation log.

    The constructor opens (creating if necessary) the database at ``db_path`` and
    initializes the schema. Pass ``":memory:"`` for an ephemeral in-memory store.

    Attributes:
        load_failed: ``True`` when the most recent startup load of store info
            failed. Cleared on a subsequent successful load.
        turns_blocked: ``True`` while new Conversation_Turns must be blocked
            because the startup load failed (Req 8.4).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # check_same_thread=False keeps the connection usable from FastAPI worker
        # threads; access is otherwise serialized through this repository.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.load_failed = False
        self.turns_blocked = False
        self._init_schema()

    def _init_schema(self) -> None:
        """Create the store_info and conversation_turn tables if absent."""
        self._conn.executescript(_SCHEMA)
        self._migrate_product_menu_columns()
        self._conn.commit()

    def _migrate_product_menu_columns(self) -> None:
        """Add the structured menu-knowledge columns to a pre-existing products
        table (spice_level, ingredients_json, allergens_json).

        ``CREATE TABLE IF NOT EXISTS`` leaves an already-created ``products`` table
        untouched, so a database seeded before these columns existed would be
        missing them. Each ``ADD COLUMN`` is attempted independently and the
        "duplicate column name" error is swallowed, making the migration a no-op on
        an up-to-date schema and safe to run on every startup.
        """
        migrations = (
            "ALTER TABLE products ADD COLUMN spice_level INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE products ADD COLUMN ingredients_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE products ADD COLUMN allergens_json TEXT NOT NULL DEFAULT '[]'",
        )
        for statement in migrations:
            try:
                self._conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def seed_demo_data(self) -> None:
        now = _to_iso(datetime.now(timezone.utc))
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO stores (id, name, currency, created_at) VALUES (?, ?, ?, ?)",
                ("demo", "Vertew Fresh Fruits", "MYR", now),
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO products "
                "(id, store_id, name_json, description_json, price_minor, currency, available, image, "
                "spice_level, ingredients_json, allergens_json) "
                "VALUES (?, 'demo', ?, ?, ?, 'MYR', 1, ?, ?, ?, ?)",
                [
                    (
                        product_id,
                        json.dumps(name, ensure_ascii=False),
                        json.dumps(description, ensure_ascii=False),
                        price,
                        image,
                        spice_level,
                        json.dumps(ingredients, ensure_ascii=False),
                        json.dumps(list(allergens), ensure_ascii=False),
                    )
                    for product_id, name, description, price, image, spice_level, ingredients, allergens in DEMO_PRODUCTS
                ],
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO qa_entries "
                "(id, store_id, question, aliases_json, answer_json, category, status, source, created_at, updated_at) "
                "VALUES (?, 'demo', ?, '[]', ?, ?, 'approved', 'curated', ?, ?)",
                [
                    (
                        qa_id,
                        question,
                        json.dumps(answer, ensure_ascii=False),
                        category,
                        now,
                        now,
                    )
                    for qa_id, question, answer, category in DEMO_QA
                ],
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO media_assets "
                "(store_id, event_type, media_type, url, label) VALUES ('demo', ?, ?, ?, ?)",
                [
                    ("idle", "image", "/assets/character/processed/reference.png", "Vertew Character"),
                    ("customer_detected", "video", "/assets/official/vertew-intro.mp4", "Greeting Video"),
                    ("customer_close", "video", "/assets/official/vertew-intro.mp4", "Menu Introduction Video"),
                ],
            )

    def get_media_config(self, store_id: str) -> dict[str, dict[str, str]]:
        rows = self._conn.execute(
            "SELECT event_type, media_type, url, label FROM media_assets WHERE store_id = ?",
            (store_id,),
        ).fetchall()
        return {
            row["event_type"]: {
                "type": row["media_type"],
                "url": row["url"],
                "label": row["label"],
            }
            for row in rows
        }

    def list_products(self, store_id: str) -> list[Product]:
        rows = self._conn.execute(
            "SELECT * FROM products WHERE store_id = ? ORDER BY rowid", (store_id,)
        ).fetchall()
        return [self._product_from_row(row) for row in rows]

    def list_qa(self, store_id: str, status: str | None = "approved") -> list[QAEntry]:
        """Return QA entries for a store, filtered by ``status`` (``None`` = all)."""
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM qa_entries WHERE store_id = ? ORDER BY rowid",
                (store_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM qa_entries WHERE store_id = ? AND status = ? ORDER BY rowid",
                (store_id, status),
            ).fetchall()
        return [self._qa_from_row(row) for row in rows]

    def add_pending_qa(
        self,
        store_id: str,
        question: str,
        answer: dict[str, str],
        *,
        source: str = "generated",
    ) -> QAEntry:
        """Insert a pending QA entry (generated or owner-provided) for later vendor
        approval, and return it. Pending entries do not ground answers until
        approved via :meth:`approve_qa`."""
        now = _to_iso(datetime.now(timezone.utc))
        qa_id = f"qa_{uuid.uuid4().hex[:12]}"
        with self._conn:
            self._conn.execute(
                "INSERT INTO qa_entries "
                "(id, store_id, question, aliases_json, answer_json, category, status, source, created_at, updated_at) "
                "VALUES (?, ?, ?, '[]', ?, 'general', 'pending', ?, ?, ?)",
                (qa_id, store_id, question, json.dumps(answer, ensure_ascii=False), source, now, now),
            )
        return QAEntry(qa_id, store_id, question, answer, "general", "pending", source, ())

    def approve_qa(self, qa_id: str) -> bool:
        """Approve a pending QA entry so it grounds future answers. Returns ``True``
        when a row was updated, ``False`` when no such id exists."""
        now = _to_iso(datetime.now(timezone.utc))
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE qa_entries SET status = 'approved', updated_at = ? WHERE id = ?",
                (now, qa_id),
            )
        return cursor.rowcount > 0

    def archive_qa(self, qa_id: str) -> bool:
        """Archive (reject) a QA entry so it is neither used nor listed as pending.
        Returns ``True`` when a row was updated, ``False`` when no such id exists."""
        now = _to_iso(datetime.now(timezone.utc))
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE qa_entries SET status = 'archived', updated_at = ? WHERE id = ?",
                (now, qa_id),
            )
        return cursor.rowcount > 0

    def get_qa_entry(self, qa_id: str) -> QAEntry | None:
        """Return a single QA entry by id, or ``None`` when it does not exist."""
        row = self._conn.execute(
            "SELECT * FROM qa_entries WHERE id = ?", (qa_id,)
        ).fetchone()
        return self._qa_from_row(row) if row else None

    def update_qa(
        self,
        qa_id: str,
        *,
        question: str | None = None,
        answer: dict[str, str] | None = None,
    ) -> bool:
        """Update a QA entry's question and/or per-language answer. Returns ``True``
        when a row was updated."""
        assignments: list[str] = []
        params: list[object] = []
        if question is not None:
            assignments.append("question = ?")
            params.append(question)
        if answer is not None:
            assignments.append("answer_json = ?")
            params.append(json.dumps(answer, ensure_ascii=False))
        if not assignments:
            return False
        assignments.append("updated_at = ?")
        params.append(_to_iso(datetime.now(timezone.utc)))
        params.append(qa_id)
        with self._conn:
            cursor = self._conn.execute(
                f"UPDATE qa_entries SET {', '.join(assignments)} WHERE id = ?",
                params,
            )
        return cursor.rowcount > 0

    def update_product(
        self,
        store_id: str,
        product_id: str,
        *,
        spice_level: int,
        ingredients: dict[str, str],
        allergens: tuple[str, ...],
        available: bool,
    ) -> Product | None:
        """Update a product's structured menu-knowledge fields (spice level,
        ingredients, allergens) and availability. Returns the updated
        :class:`Product`, or ``None`` when the product does not exist."""
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE products SET spice_level = ?, ingredients_json = ?, "
                "allergens_json = ?, available = ? WHERE store_id = ? AND id = ?",
                (
                    max(0, min(3, spice_level)),
                    json.dumps(ingredients, ensure_ascii=False),
                    json.dumps(list(allergens), ensure_ascii=False),
                    1 if available else 0,
                    store_id,
                    product_id,
                ),
            )
        if cursor.rowcount == 0:
            return None
        row = self._conn.execute(
            "SELECT * FROM products WHERE store_id = ? AND id = ?",
            (store_id, product_id),
        ).fetchone()
        return self._product_from_row(row) if row else None

    def get_session(self, session_id: str) -> CustomerSession | None:
        row = self._conn.execute(
            "SELECT * FROM customer_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return self._session_from_row(row) if row else None

    def create_session(self, store_id: str) -> CustomerSession:
        if not self._store_exists(store_id):
            raise KeyError("store_not_found")
        now = datetime.now(timezone.utc)
        session_id = uuid.uuid4().hex
        self._write_with_retry(
            "INSERT INTO customer_sessions "
            "(id, store_id, language, language_source, order_id, created_at, updated_at) "
            "VALUES (?, ?, 'en', 'default', NULL, ?, ?)",
            (session_id, store_id, _to_iso(now), _to_iso(now)),
        )
        return self.get_session(session_id)  # type: ignore[return-value]

    def update_session_language(
        self,
        session_id: str,
        language: str,
        source: LanguageSource,
        confidence: float | None = None,
    ) -> CustomerSession | None:
        current = self.get_session(session_id)
        if current is None:
            return None
        priority = {LanguageSource.DEFAULT: 0, LanguageSource.AUTO_DETECTED: 1, LanguageSource.USER_SELECTED: 2}
        if source == LanguageSource.AUTO_DETECTED and confidence is not None and confidence < 0.8:
            return current
        if priority[source] < priority[current.language_source]:
            return current
        self._write_with_retry(
            "UPDATE customer_sessions SET language = ?, language_source = ?, updated_at = ? WHERE id = ?",
            (language, source.value, _to_iso(datetime.now(timezone.utc)), session_id),
        )
        return self.get_session(session_id)

    # ------------------------------------------------------------------
    # Draft order (pre-payment scratch state, scoped to one customer session).
    #
    # Populated incrementally as the Conversation_Server recognizes ordered
    # items from natural speech (merge_draft_items) and edited directly by the
    # customer's payment-page +/-/remove buttons (set_draft_items, an absolute
    # replace rather than a delta). Cleared once checkout_draft turns it into a
    # real, vendor-visible Order.
    # ------------------------------------------------------------------
    def get_draft_items(self, session_id: str) -> dict[str, int]:
        row = self._conn.execute(
            "SELECT items_json FROM session_drafts WHERE session_id = ?", (session_id,)
        ).fetchone()
        return json.loads(row["items_json"]) if row else {}

    def set_draft_items(self, session_id: str, items: dict[str, int]) -> dict[str, int]:
        """Replace the draft with exactly these quantities (an absolute set, not
        a delta) -- used by the payment page's button edits. Non-positive
        quantities are dropped rather than stored as zero/negative rows."""
        cleaned = {product_id: quantity for product_id, quantity in items.items() if quantity > 0}
        self._write_with_retry(
            "INSERT INTO session_drafts (session_id, items_json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET items_json = excluded.items_json, "
            "updated_at = excluded.updated_at",
            (session_id, json.dumps(cleaned), _to_iso(datetime.now(timezone.utc))),
        )
        return cleaned

    def merge_draft_items(self, session_id: str, deltas: list[tuple[str, int]]) -> dict[str, int]:
        """Add quantities on top of the current draft -- used when the
        conversation recognizes newly-ordered items from natural speech (an
        incremental delta, unlike the payment page's absolute set_draft_items)."""
        current = self.get_draft_items(session_id)
        for product_id, quantity in deltas:
            current[product_id] = max(0, current.get(product_id, 0) + quantity)
        return self.set_draft_items(session_id, current)

    def clear_draft(self, session_id: str) -> None:
        self._write_with_retry("DELETE FROM session_drafts WHERE session_id = ?", (session_id,))

    def checkout_draft(
        self, store_id: str, session_id: str, customer_language: str, order_source: str = "qr"
    ) -> Order:
        """Turn the session's current draft into a real, vendor-visible Order
        (reusing create_order's validation/order-numbering) and clear the draft.

        Raises ``ValueError("draft_empty")`` when there is nothing to check out.
        """
        items = self.get_draft_items(session_id)
        if not items:
            raise ValueError("draft_empty")
        order = self.create_order(
            store_id, session_id, list(items.items()), customer_language, order_source
        )
        self.clear_draft(session_id)
        return order

    def create_order(
        self,
        store_id: str,
        session_id: str,
        requested_items: list[tuple[str, int]],
        customer_language: str,
        order_source: str,
    ) -> Order:
        session = self.get_session(session_id)
        if session is None or session.store_id != store_id:
            raise KeyError("session_not_found")
        product_map = {product.id: product for product in self.list_products(store_id)}
        quantities: dict[str, int] = {}
        for product_id, quantity in requested_items:
            quantities[product_id] = quantities.get(product_id, 0) + quantity
        items: list[OrderItem] = []
        for product_id, quantity in quantities.items():
            product = product_map.get(product_id)
            if product is None or not product.available:
                raise KeyError(f"product_not_found:{product_id}")
            items.append(OrderItem(product_id, quantity, product.name, product.price_minor))

        now = datetime.now(timezone.utc)
        order_id = f"order_{uuid.uuid4().hex}"
        total = sum(item.unit_price_minor * item.quantity for item in items)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            next_number = self._conn.execute(
                "SELECT COALESCE(MAX(order_number), 0) + 1 FROM orders WHERE store_id = ?",
                (store_id,),
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO orders (id, store_id, session_id, order_number, status, customer_language, order_source, total_minor, currency, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?, 'MYR', ?, ?)",
                (order_id, store_id, session_id, next_number, customer_language, order_source, total, _to_iso(now), _to_iso(now)),
            )
            self._conn.executemany(
                "INSERT INTO order_items (order_id, product_id, quantity, product_name_json, unit_price_minor) VALUES (?, ?, ?, ?, ?)",
                [(order_id, item.product_id, item.quantity, json.dumps(item.product_name, ensure_ascii=False), item.unit_price_minor) for item in items],
            )
            self._conn.execute(
                "UPDATE customer_sessions SET order_id = ?, updated_at = ? WHERE id = ?",
                (order_id, _to_iso(now), session_id),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return self.get_order(order_id)  # type: ignore[return-value]

    def get_order(self, order_id: str) -> Order | None:
        row = self._conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return self._order_from_row(row) if row else None

    def list_orders(self, store_id: str) -> list[Order]:
        rows = self._conn.execute(
            "SELECT * FROM orders WHERE store_id = ? ORDER BY order_number DESC", (store_id,)
        ).fetchall()
        return [self._order_from_row(row) for row in rows]

    def update_order_status(self, order_id: str, status: OrderStatus) -> Order | None:
        current = self.get_order(order_id)
        if current is None:
            return None
        allowed = {
            OrderStatus.PENDING: {OrderStatus.ACCEPTED, OrderStatus.REJECTED},
            OrderStatus.ACCEPTED: {OrderStatus.PREPARING},
            OrderStatus.PREPARING: {OrderStatus.READY},
            OrderStatus.READY: {OrderStatus.COMPLETED},
        }
        if status == current.status:
            return current
        if status not in allowed.get(current.status, set()):
            raise ValueError(f"invalid_transition:{current.status.value}:{status.value}")
        self._write_with_retry(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, _to_iso(datetime.now(timezone.utc)), order_id),
        )
        return self.get_order(order_id)

    def _store_exists(self, store_id: str) -> bool:
        return self._conn.execute("SELECT 1 FROM stores WHERE id = ?", (store_id,)).fetchone() is not None

    @staticmethod
    def _product_from_row(row: sqlite3.Row) -> Product:
        columns = row.keys()
        spice_level = int(row["spice_level"]) if "spice_level" in columns else 0
        ingredients = json.loads(row["ingredients_json"]) if "ingredients_json" in columns else {}
        allergens = tuple(json.loads(row["allergens_json"])) if "allergens_json" in columns else ()
        return Product(
            row["id"],
            row["store_id"],
            json.loads(row["name_json"]),
            json.loads(row["description_json"]),
            row["price_minor"],
            row["currency"],
            bool(row["available"]),
            row["image"],
            spice_level=spice_level,
            ingredients=ingredients,
            allergens=allergens,
        )

    @staticmethod
    def _qa_from_row(row: sqlite3.Row) -> QAEntry:
        return QAEntry(
            row["id"],
            row["store_id"],
            row["question"],
            json.loads(row["answer_json"]),
            row["category"],
            row["status"],
            row["source"],
            tuple(json.loads(row["aliases_json"])),
        )

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> CustomerSession:
        return CustomerSession(row["id"], row["store_id"], row["language"], LanguageSource(row["language_source"]), row["order_id"], datetime.fromisoformat(row["created_at"]), datetime.fromisoformat(row["updated_at"]))

    def _order_from_row(self, row: sqlite3.Row) -> Order:
        item_rows = self._conn.execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY rowid", (row["id"],)).fetchall()
        items = tuple(OrderItem(item["product_id"], item["quantity"], json.loads(item["product_name_json"]), item["unit_price_minor"]) for item in item_rows)
        return Order(row["id"], row["store_id"], row["session_id"], row["order_number"], OrderStatus(row["status"]), row["customer_language"], row["order_source"], row["total_minor"], row["currency"], items, datetime.fromisoformat(row["created_at"]), datetime.fromisoformat(row["updated_at"]))

    # ------------------------------------------------------------------
    # Low-level write boundary
    # ------------------------------------------------------------------
    def _write(self, sql: str, params: tuple) -> None:
        """Execute a single write statement and commit.

        This is the sole low-level write boundary. Centralizing it keeps retry
        logic in one place and lets tests inject failures by patching this method
        (Req 8.3). Each call is atomic: it commits on success and rolls back on
        failure so a failed attempt leaves no partial state behind.
        """
        try:
            self._conn.execute(sql, params)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _write_with_retry(self, sql: str, params: tuple) -> None:
        """Run :meth:`_write`, retrying at most once on failure (Req 8.3).

        Attempts the write up to :data:`_MAX_WRITE_ATTEMPTS` times. If every
        attempt fails, raises :class:`StorageError` wrapping the last error.
        Previously committed records are unaffected because each attempt either
        commits fully or rolls back.
        """
        last_error: Exception | None = None
        for _ in range(_MAX_WRITE_ATTEMPTS):
            try:
                self._write(sql, params)
                return
            except Exception as exc:  # noqa: BLE001 - re-raised as StorageError
                last_error = exc
        raise StorageError(
            f"write failed after {_MAX_WRITE_ATTEMPTS} attempts"
        ) from last_error

    # ------------------------------------------------------------------
    # Conversation turns (insert-only)
    # ------------------------------------------------------------------
    def record_turn(
        self,
        customer_text: str,
        character_text: str,
        completed_at: datetime,
    ) -> None:
        """Append a completed Conversation_Turn to the log (Req 8.1).

        Inserts the customer transcript, character response text, and completion
        timestamp. The write is attempted at most twice (initial plus one retry);
        on persistent failure a :class:`StorageError` is raised and prior
        committed records remain unchanged (Req 8.3).
        """
        self._write_with_retry(
            "INSERT INTO conversation_turn "
            "(customer_text, character_text, completed_at) VALUES (?, ?, ?)",
            (customer_text, character_text, _to_iso(completed_at)),
        )

    def load_turns(self) -> list[ConversationTurn]:
        """Return all recorded Conversation_Turns in chronological (insert) order."""
        rows = self._conn.execute(
            "SELECT customer_text, character_text, completed_at "
            "FROM conversation_turn ORDER BY id ASC"
        ).fetchall()
        return [
            ConversationTurn(
                customer_text=row["customer_text"],
                character_text=row["character_text"],
                completed_at=datetime.fromisoformat(row["completed_at"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Store info (single-row upsert)
    # ------------------------------------------------------------------
    def upsert_store_info(self, store_info: StoreInfo) -> None:
        """Insert or update the single store_info row (id = 1).

        Stamps ``updated_at`` with the current UTC time. Retries at most once on
        write failure (Req 8.3). Because the row is keyed on the fixed id 1, this
        replaces the previous values rather than accumulating rows.
        """
        self._write_with_retry(
            "INSERT INTO store_info (id, store_name, products, persona, updated_at) "
            "VALUES (1, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "store_name = excluded.store_name, "
            "products = excluded.products, "
            "persona = excluded.persona, "
            "updated_at = excluded.updated_at",
            (
                store_info.store_name,
                store_info.products,
                store_info.persona,
                _to_iso(datetime.now(timezone.utc)),
            ),
        )

    def load_store_info(self) -> StoreInfo | None:
        """Load the persisted store info at startup (Req 8.2).

        Returns the stored :class:`StoreInfo`, or ``None`` when no store info has
        been saved yet. On a successful load, clears the blocking flags. If the
        load raises, sets :attr:`load_failed` and :attr:`turns_blocked` so the
        server blocks new turns until a subsequent load succeeds (Req 8.4), then
        re-raises the error so the caller can surface it.
        """
        try:
            row = self._conn.execute(
                "SELECT store_name, products, persona FROM store_info WHERE id = 1"
            ).fetchone()
        except Exception:
            self.load_failed = True
            self.turns_blocked = True
            raise

        self.load_failed = False
        self.turns_blocked = False
        if row is None:
            return None
        return StoreInfo(
            store_name=row["store_name"],
            products=row["products"],
            persona=row["persona"],
        )

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()


def _to_iso(value: datetime) -> str:
    """Serialize a datetime to an ISO-8601 string for TEXT storage."""
    return value.isoformat()
