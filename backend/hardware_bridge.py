"""CLI bridge forwarding Raspberry Pi/ESP32 sensor events to Vertew."""

from __future__ import annotations

import argparse
import os
import signal
import threading

import httpx

from sensors import Esp32SerialAdapter, RaspberryPiDistanceAdapter, SensorAdapter, SensorEvent


class SensorBridge:
    def __init__(
        self,
        adapter: SensorAdapter,
        backend_url: str,
        store_id: str,
        token: str | None = None,
    ) -> None:
        self.adapter = adapter
        self.endpoint = f"{backend_url.rstrip('/')}/api/stores/{store_id}/sensor/events"
        self.headers = {"X-Sensor-Token": token} if token else {}
        self.client = httpx.Client(timeout=5)
        self.stopped = threading.Event()

    def run(self) -> None:
        self.adapter.start(self.forward)
        self.stopped.wait()

    def forward(self, event: SensorEvent) -> None:
        try:
            response = self.client.post(
                self.endpoint,
                json={"event": event.event, "distance": event.distance},
                headers=self.headers,
            )
            response.raise_for_status()
            print(f"forwarded {event.event} distance={event.distance}", flush=True)
        except httpx.HTTPError as exc:
            print(f"sensor forward failed: {exc}", flush=True)

    def stop(self) -> None:
        self.adapter.stop()
        self.client.close()
        self.stopped.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vertew hardware sensor bridge")
    parser.add_argument("--adapter", choices=("serial", "gpio"), required=True)
    parser.add_argument("--backend", default=os.environ.get("BACKEND_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--store", default=os.environ.get("STORE_ID", "demo"))
    parser.add_argument("--token", default=os.environ.get("SENSOR_SHARED_SECRET") or None)
    parser.add_argument("--port", default=os.environ.get("ESP32_SERIAL_PORT", "/dev/ttyUSB0"))
    parser.add_argument("--baudrate", type=int, default=int(os.environ.get("ESP32_BAUDRATE", "115200")))
    parser.add_argument("--trigger-pin", type=int, default=int(os.environ.get("SENSOR_TRIGGER_PIN", "23")))
    parser.add_argument("--echo-pin", type=int, default=int(os.environ.get("SENSOR_ECHO_PIN", "24")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    adapter: SensorAdapter
    if args.adapter == "serial":
        adapter = Esp32SerialAdapter(args.port, args.baudrate)
    else:
        adapter = RaspberryPiDistanceAdapter(args.trigger_pin, args.echo_pin)
    bridge = SensorBridge(adapter, args.backend, args.store, args.token)
    signal.signal(signal.SIGINT, lambda *_: bridge.stop())
    signal.signal(signal.SIGTERM, lambda *_: bridge.stop())
    bridge.run()


if __name__ == "__main__":
    main()
