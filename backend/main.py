"""Conversation_Server entry point (FastAPI).

Wires the pure conversation core (Task 8.1) into a runnable local server: a
localhost WebSocket for the per-turn conversation loop and the ``/admin`` HTTP
routes, plus the statically served Kiosk_UI. The browser talks only to this
localhost server (Req 9.1); the server's only outbound internet calls are the
Speech_Module's STT request and the single Gemini Flash call per turn (Req 9.2).

Startup wiring (see design.md, "Conversation_Server"):

- Construct the :class:`~db.DataStore` from ``VERTEW_DB_PATH`` (backend/.env.example).
- Load persisted store info (Req 8.2). If the load fails, the store sets
  :attr:`~db.DataStore.turns_blocked` and we keep serving so the Admin_Interface
  stays reachable, but the WebSocket refuses new turns until a load succeeds
  (Req 8.4).
- Register the store with the admin routes via :func:`admin.set_data_store`.
- Seed an in-memory :class:`~conversation.ConversationSession` from the persisted
  turn log so recent history survives a restart (Req 3.2).

The app object is importable as ``main.app`` for testing.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Load backend/.env (Gemini API key, DB path) before anything reads os.environ.
# Without this the GEMINI_API_KEY in backend/.env would never reach the LLM client
# and every turn would fall back to "temporarily unavailable".
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:  # python-dotenv missing -> rely on real environment variables
    pass

import admin
import llm
from conversation import ConversationSession, handle_transcript
from db import DataStore

# Environment variable holding the SQLite database path (see backend/.env.example).
DB_PATH_ENV = "VERTEW_DB_PATH"
# Sensible default used when VERTEW_DB_PATH is not configured.
DEFAULT_DB_PATH = "vertew.db"

# Directory holding the Kiosk_UI/static assets (index.html, admin.html, js/).
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class _ServerState:
    """Holds the process-wide DataStore and ConversationSession.

    Populated on startup and read by the WebSocket handler. Kept on a small holder
    (rather than module globals scattered around) so tests can inspect or replace
    the wiring through ``main.state``.
    """

    data_store: DataStore | None = None
    session: ConversationSession | None = None


state = _ServerState()


def _db_path() -> str:
    """Return the configured SQLite path, falling back to :data:`DEFAULT_DB_PATH`."""
    return os.environ.get(DB_PATH_ENV) or DEFAULT_DB_PATH


def _startup() -> None:
    """Construct and wire the DataStore + ConversationSession (Req 8.2, 8.4)."""
    data_store = DataStore(_db_path())

    # Load persisted store info at startup (Req 8.2). On failure the store flips
    # turns_blocked (Req 8.4); we swallow the error here so the server still comes
    # up and the Admin_Interface remains reachable to re-enter/save the info.
    try:
        data_store.load_store_info()
    except Exception:  # noqa: BLE001 - failure is reflected by turns_blocked
        pass

    # Make the same store the admin routes read from and write to (Req 7.6).
    admin.set_data_store(data_store)

    # Seed recent-turn history from the persisted log so prompts carry context
    # across restarts (Req 3.2). If the log can't be read, start empty.
    try:
        session = ConversationSession.from_store(data_store)
    except Exception:  # noqa: BLE001 - degrade to an empty history rather than crash
        session = ConversationSession()

    state.data_store = data_store
    state.session = session


def _shutdown() -> None:
    """Release the SQLite connection held by the DataStore."""
    if state.data_store is not None:
        state.data_store.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """FastAPI lifespan: wire dependencies on startup, release them on shutdown."""
    _startup()
    try:
        yield
    finally:
        _shutdown()


app = FastAPI(title="Vertew Conversation_Server", lifespan=lifespan)

# Mount the Admin_Interface routes so /admin works (Req 7.x).
app.include_router(admin.router)


def _extract_transcript(message: object) -> str | None:
    """Pull the customer transcript out of an inbound WebSocket message.

    Accepts either a JSON object carrying a ``transcript`` (or ``text``) field or a
    bare string, returning the transcript text or ``None`` when none is present.
    """
    if isinstance(message, dict):
        value = message.get("transcript")
        if value is None:
            value = message.get("text")
        return value if isinstance(value, str) else None
    if isinstance(message, str):
        return message
    return None


def _response_payload(response) -> dict:
    """Shape a CharacterResponse into the wire JSON the Kiosk_UI consumes."""
    return {
        "text": response.text,
        "emotion": response.emotion,
        "gesture": response.gesture,
    }


@app.websocket("/ws")
async def conversation_ws(websocket: WebSocket) -> None:
    """Localhost WebSocket carrying the conversation loop (Req 9.1).

    Each inbound message is a customer transcript; the server runs one
    Conversation_Turn via :func:`handle_transcript` and pushes back the resulting
    character response as ``{text, emotion, gesture}``. When startup store-info
    loading failed, new turns are blocked (Req 8.4) and the server replies with the
    temporarily-unavailable fallback (flagged with ``error: true``) instead of
    processing the turn.
    """
    await websocket.accept()
    try:
        while True:
            message = await websocket.receive_json()
            transcript = _extract_transcript(message)

            # Ignore empty/whitespace-only or malformed messages (the Speech_Module
            # gates non-empty transcripts upstream; this is a defensive backstop).
            if transcript is None or not transcript.strip():
                continue

            data_store = state.data_store
            session = state.session
            if data_store is None or session is None:
                # Server not fully wired (should not happen once lifespan ran).
                payload = _response_payload(llm.FALLBACK_UNAVAILABLE)
                payload["error"] = True
                await websocket.send_json(payload)
                continue

            # Startup load failed -> block new turns until a load succeeds (Req 8.4).
            if data_store.turns_blocked:
                payload = _response_payload(llm.FALLBACK_UNAVAILABLE)
                payload["error"] = True
                await websocket.send_json(payload)
                continue

            response = await handle_transcript(
                transcript,
                data_store=data_store,
                session=session,
            )
            await websocket.send_json(_response_payload(response))
    except WebSocketDisconnect:
        # Client closed the socket; nothing to clean up beyond the connection.
        return


# ---------------------------------------------------------------------------
# Static Kiosk_UI assets.
#
# Serve the frontend so the browser loads everything from localhost (Req 9.1).
# Registered AFTER the /admin and /ws routes so those take precedence; the static
# mount at "/" then serves index.html, admin.html, and js/ for everything else.
# ---------------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    """Serve the Kiosk_UI entry page (frontend/index.html)."""
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
