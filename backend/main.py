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

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
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
import ai_api
import commerce_api
import debug_api
import llm
import sensor_api
import stt_api
from conversation import ConversationSession, handle_transcript
from db import DataStore
from realtime import ClientRole, build_event, manager

# Environment variable holding the SQLite database path (see backend/.env.example).
DB_PATH_ENV = "VERTEW_DB_PATH"
# Sensible default used when VERTEW_DB_PATH is not configured.
DEFAULT_DB_PATH = "vertew.db"

# Directory holding the Kiosk_UI/static assets (index.html, admin.html, js/).
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
CUSTOMER_DIST_DIR = Path(__file__).resolve().parent.parent / "apps" / "customer" / "dist"
VENDOR_DIST_DIR = Path(__file__).resolve().parent.parent / "apps" / "vendor" / "dist"


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
    commerce_api.set_data_store(data_store)
    data_store.seed_demo_data()

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
app.include_router(commerce_api.router)
app.include_router(ai_api.router)
app.include_router(debug_api.router)
app.include_router(sensor_api.router)
app.include_router(stt_api.router)


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
    session_id = websocket.query_params.get("session_id")
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

            customer_session = data_store.get_session(session_id) if session_id else None
            response = await handle_transcript(
                transcript,
                data_store=data_store,
                session=session,
                language=customer_session.language if customer_session else "en",
                store_id=customer_session.store_id if customer_session else "demo",
                local_on_unavailable=True,
            )
            # Human escalation: the assistant asked to fetch the vendor (question not
            # covered by curated Q&A / menu, or a safety-sensitive allergy/health
            # question). Notify the vendor dashboard over the store event bus; the
            # customer still gets the "I'll call the owner" reply.
            if getattr(response, "action", "answer") == "call_owner":
                await manager.broadcast(
                    customer_session.store_id if customer_session else "demo",
                    "call_vendor",
                    {
                        "question": transcript,
                        "language": customer_session.language if customer_session else "en",
                    },
                    session_id=session_id,
                )
            await websocket.send_json(_response_payload(response))
    except WebSocketDisconnect:
        # Client closed the socket; nothing to clean up beyond the connection.
        return


DEBUG_EVENT_TYPES = frozenset(
    {"customer_detected", "customer_close", "keyword_detected", "show_qr"}
)


@app.websocket("/ws/store/{store_id}")
async def store_events_ws(
    websocket: WebSocket,
    store_id: str,
    client: ClientRole = Query(default="debug"),
    session_id: str | None = Query(default=None),
) -> None:
    """Store event bus shared by hologram, customer, vendor, and debug clients."""
    await manager.connect(store_id, websocket)
    await websocket.send_json(
        build_event(store_id, "connected", {"client": client}, session_id=session_id)
    )
    try:
        while True:
            message = await websocket.receive_json()
            event_type = message.get("type") if isinstance(message, dict) else None
            if client != "debug" or event_type not in DEBUG_EVENT_TYPES:
                await websocket.send_json(
                    build_event(
                        store_id,
                        "error",
                        {"detail": "Event is not allowed from this client"},
                        session_id=session_id,
                    )
                )
                continue
            payload = message.get("payload")
            await manager.broadcast(
                store_id,
                event_type,
                payload if isinstance(payload, dict) else {},
                session_id=session_id,
            )
    except WebSocketDisconnect:
        manager.disconnect(store_id, websocket)


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


if CUSTOMER_DIST_DIR.is_dir():
    customer_assets = CUSTOMER_DIST_DIR / "assets"
    if customer_assets.is_dir():
        app.mount(
            "/order/assets",
            StaticFiles(directory=str(customer_assets)),
            name="customer-ordering-assets",
        )

    @app.get("/order")
    @app.get("/order/{full_path:path}")
    def customer_ordering_web(full_path: str = "") -> FileResponse:
        """Serve the React ordering SPA, including session-aware nested URLs."""
        return FileResponse(CUSTOMER_DIST_DIR / "index.html")


if VENDOR_DIST_DIR.is_dir():
    vendor_assets = VENDOR_DIST_DIR / "assets"
    if vendor_assets.is_dir():
        app.mount(
            "/vendor/assets",
            StaticFiles(directory=str(vendor_assets)),
            name="vendor-dashboard-assets",
        )

    @app.get("/vendor")
    @app.get("/vendor/{full_path:path}")
    def vendor_dashboard(full_path: str = "") -> FileResponse:
        """Serve the responsive React vendor dashboard."""
        return FileResponse(VENDOR_DIST_DIR / "index.html")


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
