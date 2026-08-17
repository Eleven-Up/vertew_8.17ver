"""Replaceable sensor boundary for Mock, Raspberry Pi, and ESP32 inputs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import threading
import time
from typing import Callable, Protocol

SENSOR_EVENT_TYPES = frozenset(
    {"customer_detected", "customer_close", "keyword_detected", "show_qr"}
)


@dataclass(frozen=True)
class SensorEvent:
    event: str
    distance: float | None = None

    def __post_init__(self) -> None:
        if self.event not in SENSOR_EVENT_TYPES:
            raise ValueError(f"unsupported_sensor_event:{self.event}")
        if self.distance is not None and self.distance < 0:
            raise ValueError("distance_must_be_non_negative")

    def payload(self) -> dict[str, float]:
        return {} if self.distance is None else {"distance": self.distance}


class SensorAdapter(Protocol):
    """Hardware adapters emit normalized events without knowing FastAPI or UI."""

    def start(self, on_event: Callable[[SensorEvent], None]) -> None: ...
    def stop(self) -> None: ...


class MockSensorAdapter:
    def __init__(self) -> None:
        self._on_event: Callable[[SensorEvent], None] | None = None

    def start(self, on_event: Callable[[SensorEvent], None]) -> None:
        self._on_event = on_event

    def emit(self, event: SensorEvent) -> None:
        if self._on_event is None:
            raise RuntimeError("sensor_adapter_not_started")
        self._on_event(event)

    def stop(self) -> None:
        self._on_event = None


class DistanceStateMachine:
    """Converts noisy distance samples into debounced approach events."""

    def __init__(self, detected_cm: float = 200, close_cm: float = 80, reset_cm: float = 250) -> None:
        if not 0 < close_cm < detected_cm < reset_cm:
            raise ValueError("distance_thresholds_must_be_ordered")
        self.detected_cm = detected_cm
        self.close_cm = close_cm
        self.reset_cm = reset_cm
        self._state = "absent"

    def update(self, distance_cm: float) -> SensorEvent | None:
        if distance_cm < 0:
            raise ValueError("distance_must_be_non_negative")
        if distance_cm > self.reset_cm:
            self._state = "absent"
            return None
        if distance_cm <= self.close_cm and self._state != "close":
            self._state = "close"
            return SensorEvent("customer_close", distance_cm)
        if distance_cm <= self.detected_cm and self._state == "absent":
            self._state = "detected"
            return SensorEvent("customer_detected", distance_cm)
        return None


class Esp32SerialAdapter:
    """Reads one JSON SensorEvent per line from an ESP32 serial connection."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        serial_factory=None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self._serial_factory = serial_factory
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, on_event: Callable[[SensorEvent], None]) -> None:
        if self._thread and self._thread.is_alive():
            return
        factory = self._serial_factory
        if factory is None:
            try:
                import serial  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("pyserial is required for the ESP32 adapter") from exc
            factory = serial.Serial
        self._serial = factory(self.port, self.baudrate, timeout=1)
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, args=(on_event,), daemon=True)
        self._thread.start()

    def _read_loop(self, on_event: Callable[[SensorEvent], None]) -> None:
        while not self._stop.is_set():
            raw = self._serial.readline()
            if not raw:
                continue
            try:
                data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                on_event(SensorEvent(str(data["event"]), data.get("distance")))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue

    def stop(self) -> None:
        self._stop.set()
        if self._serial is not None:
            self._serial.close()
        if self._thread is not None:
            self._thread.join(timeout=2)


class RaspberryPiDistanceAdapter:
    """Polls a distance reader; the default reader uses gpiozero DistanceSensor."""

    def __init__(
        self,
        trigger_pin: int = 23,
        echo_pin: int = 24,
        poll_seconds: float = 0.2,
        distance_reader: Callable[[], float] | None = None,
    ) -> None:
        self.trigger_pin = trigger_pin
        self.echo_pin = echo_pin
        self.poll_seconds = poll_seconds
        self._reader = distance_reader
        self._device = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._machine = DistanceStateMachine()

    def start(self, on_event: Callable[[SensorEvent], None]) -> None:
        if self._reader is None:
            try:
                from gpiozero import DistanceSensor  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("gpiozero is required for the Raspberry Pi adapter") from exc
            self._device = DistanceSensor(echo=self.echo_pin, trigger=self.trigger_pin, max_distance=4)
            self._reader = lambda: float(self._device.distance) * 100
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll_loop, args=(on_event,), daemon=True)
        self._thread.start()

    def _poll_loop(self, on_event: Callable[[SensorEvent], None]) -> None:
        assert self._reader is not None
        while not self._stop.is_set():
            try:
                event = self._machine.update(float(self._reader()))
                if event is not None:
                    on_event(event)
            except (ValueError, OSError):
                pass
            time.sleep(self.poll_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._device is not None:
            self._device.close()
