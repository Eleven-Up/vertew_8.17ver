"""REST bridge used by the laptop demo and future hardware gateway."""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from realtime import manager
from sensors import SensorEvent

router = APIRouter(prefix="/api/stores/{store_id}/debug", tags=["debug"])


class DebugEventRequest(BaseModel):
    event: str
    distance: float | None = Field(default=None, ge=0)
    session_id: str | None = None


def debug_enabled() -> bool:
    return os.environ.get("VERTEW_DEBUG_MODE", "true").lower() in {"1", "true", "yes", "on"}


@router.post("/events")
async def emit_debug_event(store_id: str, body: DebugEventRequest) -> dict:
    if not debug_enabled():
        raise HTTPException(403, "Debug mode is disabled")
    try:
        event = SensorEvent(body.event, body.distance)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return await manager.broadcast(
        store_id, event.event, event.payload(), session_id=body.session_id
    )
