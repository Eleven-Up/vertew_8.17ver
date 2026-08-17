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

CREATE TABLE IF NOT EXISTS media_assets (
    store_id    TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    media_type  TEXT NOT NULL,
    url         TEXT NOT NULL,
    label       TEXT NOT NULL,
    PRIMARY KEY (store_id, event_type),
    FOREIGN KEY (store_id) REFERENCES stores(id)
);
"""

DEMO_PRODUCTS = (
    ("watermelon", {"en": "Watermelon", "ko": "수박", "ms": "Tembikai"}, {"en": "Cool and refreshing watermelon", "ko": "시원하고 상쾌한 수박", "ms": "Tembikai yang sejuk dan menyegarkan"}, 400, "/images/watermelon.png"),
    ("mango", {"en": "Mango", "ko": "망고", "ms": "Mangga"}, {"en": "Sweet and fresh mango", "ko": "달고 신선한 망고", "ms": "Mangga manis dan segar"}, 500, "/images/mango.png"),
    ("banana", {"en": "Banana", "ko": "바나나", "ms": "Pisang"}, {"en": "Soft and naturally sweet banana", "ko": "부드럽고 자연스럽게 달콤한 바나나", "ms": "Pisang lembut dan manis semula jadi"}, 300, "/images/banana.png"),
    ("apple", {"en": "Apple", "ko": "사과", "ms": "Epal"}, {"en": "Crisp and juicy apple", "ko": "아삭하고 과즙이 풍부한 사과", "ms": "Epal rangup dan berjus"}, 350, "/images/apple.png"),
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
        self._conn.commit()

    def seed_demo_data(self) -> None:
        now = _to_iso(datetime.now(timezone.utc))
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO stores (id, name, currency, created_at) VALUES (?, ?, ?, ?)",
                ("demo", "Vertew Fresh Fruits", "MYR", now),
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO products "
                "(id, store_id, name_json, description_json, price_minor, currency, available, image) "
                "VALUES (?, 'demo', ?, ?, ?, 'MYR', 1, ?)",
                [(product_id, json.dumps(name, ensure_ascii=False), json.dumps(description, ensure_ascii=False), price, image) for product_id, name, description, price, image in DEMO_PRODUCTS],
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
        return Product(row["id"], row["store_id"], json.loads(row["name_json"]), json.loads(row["description_json"]), row["price_minor"], row["currency"], bool(row["available"]), row["image"])

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
