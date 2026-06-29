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
from datetime import datetime, timezone

from models import ConversationTurn, StoreInfo

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
"""


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
        self.load_failed = False
        self.turns_blocked = False
        self._init_schema()

    def _init_schema(self) -> None:
        """Create the store_info and conversation_turn tables if absent."""
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

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
