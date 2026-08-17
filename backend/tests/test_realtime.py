import json
from datetime import datetime, timezone

import pytest

from realtime import StoreConnectionManager, build_event


class FakeWebSocket:
    def __init__(self, *, fail=False):
        self.accepted = False
        self.fail = fail
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        if self.fail:
            raise RuntimeError("disconnected")
        self.messages.append(message)


@pytest.mark.asyncio
async def test_store_event_is_broadcast_to_every_connected_client():
    manager = StoreConnectionManager()
    hologram = FakeWebSocket()
    vendor = FakeWebSocket()
    await manager.connect("demo", hologram)
    await manager.connect("demo", vendor)

    event = await manager.broadcast(
        "demo", "customer_detected", {"distance": 180}, session_id="abc123"
    )

    assert hologram.accepted and vendor.accepted
    assert hologram.messages == [event]
    assert vendor.messages == [event]
    assert event["type"] == "customer_detected"
    assert event["store_id"] == "demo"
    assert event["session_id"] == "abc123"
    assert event["payload"] == {"distance": 180}
    assert "timestamp" in event


@pytest.mark.asyncio
async def test_store_events_do_not_leak_to_another_store():
    manager = StoreConnectionManager()
    demo = FakeWebSocket()
    another = FakeWebSocket()
    await manager.connect("demo", demo)
    await manager.connect("another", another)

    await manager.broadcast("demo", "new_order", {"order_number": 1})

    assert len(demo.messages) == 1
    assert another.messages == []


@pytest.mark.asyncio
async def test_failed_socket_is_removed_without_blocking_other_clients():
    manager = StoreConnectionManager()
    stale = FakeWebSocket(fail=True)
    active = FakeWebSocket()
    await manager.connect("demo", stale)
    await manager.connect("demo", active)

    await manager.broadcast("demo", "order_ready", {"order_number": 12})

    assert manager.connection_count("demo") == 1
    assert active.messages[0]["type"] == "order_ready"


def test_event_envelope_has_stable_shape():
    event = build_event("demo", "language_changed", {"language": "ko"})
    assert set(event) == {"type", "store_id", "session_id", "timestamp", "payload"}
    assert event["session_id"] is None


@pytest.mark.asyncio
async def test_broadcast_encodes_order_datetime_for_websocket_json():
    manager = StoreConnectionManager()
    socket = FakeWebSocket()
    await manager.connect("demo", socket)

    event = await manager.broadcast(
        "demo", "new_order", {"created_at": datetime.now(timezone.utc)}
    )

    assert isinstance(event["payload"]["created_at"], str)
    json.dumps(event)
