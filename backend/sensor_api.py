"""Production sensor event ingest endpoint for Pi/ESP32 gateways."""

import hmac
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from realtime import manager
from sensors import SensorEvent

router = APIRouter(prefix="/api/stores/{store_id}/sensor", tags=["sensor"])


class SensorEventRequest(BaseModel):
    event: str
    distance: float | None = Field(default=None, ge=0)
    session_id: str | None = None


@router.post("/events")
async def ingest_sensor_event(
    store_id: str,
    body: SensorEventRequest,
    x_sensor_token: str | None = Header(default=None),
) -> dict:
    expected = os.environ.get("SENSOR_SHARED_SECRET")
    if expected and (x_sensor_token is None or not hmac.compare_digest(expected, x_sensor_token)):
        raise HTTPException(401, "Invalid sensor token")
    try:
        event = SensorEvent(body.event, body.distance)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return await manager.broadcast(
        store_id, event.event, event.payload(), session_id=body.session_id
    )
