import pytest
import threading
from fastapi.testclient import TestClient

from main import app
from sensors import DistanceStateMachine, Esp32SerialAdapter, MockSensorAdapter, SensorEvent


def test_mock_sensor_adapter_emits_normalized_event():
    received = []
    adapter = MockSensorAdapter()
    adapter.start(received.append)
    adapter.emit(SensorEvent("customer_detected", 180))
    assert received == [SensorEvent("customer_detected", 180)]
    assert received[0].payload() == {"distance": 180}


@pytest.mark.parametrize("event", ["customer_detected", "customer_close", "keyword_detected", "show_qr"])
def test_debug_api_accepts_supported_events(event):
    response = TestClient(app).post(
        "/api/stores/demo/debug/events",
        json={"event": event, "distance": 70, "session_id": "demo-session"},
    )
    assert response.status_code == 200
    assert response.json()["type"] == event


def test_debug_api_rejects_unknown_event():
    response = TestClient(app).post(
        "/api/stores/demo/debug/events", json={"event": "open_cash_drawer"}
    )
    assert response.status_code == 422


def test_sensor_event_rejects_negative_distance():
    with pytest.raises(ValueError, match="distance_must_be_non_negative"):
        SensorEvent("customer_close", -1)


def test_distance_state_machine_emits_only_threshold_transitions():
    machine = DistanceStateMachine()
    assert machine.update(300) is None
    assert machine.update(180) == SensorEvent("customer_detected", 180)
    assert machine.update(170) is None
    assert machine.update(70) == SensorEvent("customer_close", 70)
    assert machine.update(60) is None
    assert machine.update(300) is None
    assert machine.update(190) == SensorEvent("customer_detected", 190)


def test_esp32_serial_adapter_parses_json_and_ignores_bad_lines():
    class FakeSerial:
        def __init__(self, *_args, **_kwargs):
            self.lines = iter([b"not-json\n", b'{"event":"customer_close","distance":70}\n'])
            self.closed = False

        def readline(self):
            return next(self.lines, b"")

        def close(self):
            self.closed = True

    received = []
    ready = threading.Event()
    adapter = Esp32SerialAdapter("fake", serial_factory=FakeSerial)
    adapter.start(lambda event: (received.append(event), ready.set()))
    assert ready.wait(timeout=1)
    adapter.stop()
    assert received == [SensorEvent("customer_close", 70)]


def test_sensor_api_shared_secret(monkeypatch):
    monkeypatch.setenv("SENSOR_SHARED_SECRET", "test-secret")
    client = TestClient(app)
    denied = client.post(
        "/api/stores/demo/sensor/events", json={"event": "customer_detected"}
    )
    allowed = client.post(
        "/api/stores/demo/sensor/events",
        json={"event": "customer_detected", "distance": 180},
        headers={"X-Sensor-Token": "test-secret"},
    )
    assert denied.status_code == 401
    assert allowed.status_code == 200
