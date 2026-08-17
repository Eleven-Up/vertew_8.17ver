"""Store-scoped WebSocket connections and event broadcasting."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

ClientRole = Literal["hologram", "customer", "vendor", "debug"]


class StoreConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, store_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[store_id].append(websocket)

    def disconnect(self, store_id: str, websocket: WebSocket) -> None:
        connections = self._connections.get(store_id)
        if not connections:
            return
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self._connections.pop(store_id, None)

    async def broadcast(
        self,
        store_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        event = jsonable_encoder(
            build_event(store_id, event_type, payload, session_id=session_id)
        )
        stale: list[WebSocket] = []
        for websocket in tuple(self._connections.get(store_id, ())):
            try:
                await websocket.send_json(event)
            except Exception:  # disconnected clients are cleaned up after the pass
                stale.append(websocket)
        for websocket in stale:
            self.disconnect(store_id, websocket)
        return event

    def connection_count(self, store_id: str) -> int:
        return len(self._connections.get(store_id, ()))


def build_event(
    store_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    return {
        "type": event_type,
        "store_id": store_id,
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


manager = StoreConnectionManager()
