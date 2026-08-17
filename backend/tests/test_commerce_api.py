from fastapi.testclient import TestClient

import commerce_api
from db import DataStore
from main import app


def _client(tmp_path):
    store = DataStore(str(tmp_path / "commerce.db"))
    store.seed_demo_data()
    app.dependency_overrides[commerce_api.get_data_store] = lambda: store
    return TestClient(app), store


def _close(store):
    app.dependency_overrides.pop(commerce_api.get_data_store, None)
    store.close()


def test_menu_contains_multilingual_demo_products(tmp_path):
    client, store = _client(tmp_path)
    try:
        response = client.get("/api/stores/demo/menu")
        assert response.status_code == 200
        products = response.json()["products"]
        assert [product["id"] for product in products] == [
            "watermelon",
            "mango",
            "banana",
            "apple",
        ]
        mango = products[1]
        assert mango["name"] == {"en": "Mango", "ko": "망고", "ms": "Mangga"}
        assert mango["price_minor"] == 500
        assert mango["currency"] == "MYR"

        media = client.get("/api/stores/demo/media")
        assert media.status_code == 200
        assets = media.json()["assets"]
        assert assets["customer_detected"]["label"] == "Greeting Video"
        assert assets["customer_close"]["label"] == "Menu Introduction Video"
        assert assets["customer_detected"]["url"].endswith("vertew-intro.mp4")
    finally:
        _close(store)


def test_session_language_priority(tmp_path):
    client, store = _client(tmp_path)
    try:
        created = client.post("/api/sessions", json={"store_id": "demo"})
        assert created.status_code == 201
        session_id = created.json()["id"]
        assert created.json()["language"] == "en"
        assert created.json()["language_source"] == "default"

        detected = client.patch(
            f"/api/sessions/{session_id}/language",
            json={"language": "ko", "language_source": "auto_detected"},
        )
        assert detected.json()["language"] == "ko"

        selected = client.patch(
            f"/api/sessions/{session_id}/language",
            json={"language": "en", "language_source": "user_selected"},
        )
        assert selected.json()["language"] == "en"

        ignored = client.patch(
            f"/api/sessions/{session_id}/language",
            json={"language": "ms", "language_source": "auto_detected"},
        )
        assert ignored.json()["language"] == "en"
        assert ignored.json()["language_source"] == "user_selected"
    finally:
        _close(store)


def test_order_creation_totals_and_status_transitions(tmp_path):
    client, store = _client(tmp_path)
    try:
        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        response = client.post(
            "/api/orders",
            json={
                "store_id": "demo",
                "session_id": session["id"],
                "items": [
                    {"product_id": "mango", "quantity": 1},
                    {"product_id": "apple", "quantity": 2},
                ],
                "customer_language": "ko",
                "order_source": "qr",
            },
        )
        assert response.status_code == 201
        order = response.json()
        assert order["order_number"] == 1
        assert order["status"] == "PENDING"
        assert order["total_minor"] == 1200

        invalid = client.patch(
            f"/api/orders/{order['id']}/status", json={"status": "READY"}
        )
        assert invalid.status_code == 409

        for status in ("ACCEPTED", "PREPARING", "READY", "COMPLETED"):
            changed = client.patch(
                f"/api/orders/{order['id']}/status", json={"status": status}
            )
            assert changed.status_code == 200
            assert changed.json()["status"] == status

        listing = client.get("/api/stores/demo/orders?status=COMPLETED")
        assert [item["id"] for item in listing.json()["orders"]] == [order["id"]]
    finally:
        _close(store)


def test_order_rejects_unknown_product(tmp_path):
    client, store = _client(tmp_path)
    try:
        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        response = client.post(
            "/api/orders",
            json={
                "store_id": "demo",
                "session_id": session["id"],
                "items": [{"product_id": "dragonfruit", "quantity": 1}],
                "customer_language": "en",
            },
        )
        assert response.status_code == 404
    finally:
        _close(store)


def test_ready_status_broadcasts_to_hologram_session(tmp_path):
    client, store = _client(tmp_path)
    try:
        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        order = client.post(
            "/api/orders",
            json={
                "store_id": "demo",
                "session_id": session["id"],
                "items": [{"product_id": "mango", "quantity": 1}],
                "customer_language": "ko",
            },
        ).json()
        with client.websocket_connect(
            f"/ws/store/demo?client=hologram&session_id={session['id']}"
        ) as socket:
            assert socket.receive_json()["type"] == "connected"
            for status in ("ACCEPTED", "PREPARING"):
                client.patch(f"/api/orders/{order['id']}/status", json={"status": status})
                socket.receive_json()
            client.patch(f"/api/orders/{order['id']}/status", json={"status": "READY"})
            event = socket.receive_json()
            assert event["type"] == "order_ready"
            assert event["session_id"] == session["id"]
            assert event["payload"]["order_number"] == order["order_number"]
            assert event["payload"]["customer_language"] == "ko"
    finally:
        _close(store)
