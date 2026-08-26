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
            "nasi_lemak",
            "tteokbokki",
            "nasi_goreng",
            "beef_noodle_soup",
            "hainan_chicken_rice",
        ]
        tteokbokki = products[1]
        assert tteokbokki["name"] == {"en": "Tteokbokki", "ko": "떡볶이", "ms": "Tteokbokki"}
        assert tteokbokki["price_minor"] == 600
        assert tteokbokki["currency"] == "MYR"
        assert tteokbokki["origin"] == {"en": "Seoul, South Korea", "ko": "대한민국 서울", "ms": "Seoul, Korea Selatan"}

        media = client.get("/api/stores/demo/media")
        assert media.status_code == 200
        assets = media.json()["assets"]
        assert assets["customer_detected"]["label"] == "Greeting Video"
        assert assets["customer_close"]["label"] == "Menu Introduction Video"
        assert assets["customer_detected"]["url"].endswith("vertew-intro.mp4")
    finally:
        _close(store)


def test_session_language_follows_latest_confident_detection(tmp_path):
    """A confident auto-detection always wins, even over an earlier explicit
    selection -- so the session (UI, TTS, reply language) follows whatever
    language the customer is actually speaking right now, not a stale choice
    made earlier on the order page. Only a low-confidence auto-detection is
    ignored, to avoid drifting on noisy/ambiguous transcripts."""
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

        # A later, confident auto-detection now overrides the earlier explicit
        # selection -- speaking Malay after picking "English" switches back.
        redetected = client.patch(
            f"/api/sessions/{session_id}/language",
            json={"language": "ms", "language_source": "auto_detected", "confidence": 0.95},
        )
        assert redetected.json()["language"] == "ms"
        assert redetected.json()["language_source"] == "auto_detected"

        # A low-confidence auto-detection is still ignored (noise guard).
        ignored = client.patch(
            f"/api/sessions/{session_id}/language",
            json={"language": "en", "language_source": "auto_detected", "confidence": 0.5},
        )
        assert ignored.json()["language"] == "ms"
        assert ignored.json()["language_source"] == "auto_detected"
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
                    {"product_id": "nasi_goreng", "quantity": 1},
                    {"product_id": "tteokbokki", "quantity": 2},
                ],
                "customer_language": "ko",
                "order_source": "qr",
            },
        )
        assert response.status_code == 201
        order = response.json()
        assert order["order_number"] == 1
        assert order["status"] == "PENDING"
        assert order["total_minor"] == 2000

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
                "items": [{"product_id": "laksa", "quantity": 1}],
                "customer_language": "en",
            },
        )
        assert response.status_code == 404
    finally:
        _close(store)


def test_order_item_note_is_recorded_and_returned(tmp_path):
    client, store = _client(tmp_path)
    try:
        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        response = client.post(
            "/api/orders",
            json={
                "store_id": "demo",
                "session_id": session["id"],
                "items": [{"product_id": "nasi_goreng", "quantity": 1, "note": "extra spicy please"}],
                "customer_language": "en",
            },
        )
        assert response.status_code == 201
        order = response.json()
        assert order["items"][0]["note"] == "extra spicy please"

        fetched = client.get(f"/api/orders/{order['id']}").json()
        assert fetched["items"][0]["note"] == "extra spicy please"
    finally:
        _close(store)


def test_draft_checkout_carries_item_note_into_the_order(tmp_path):
    client, store = _client(tmp_path)
    try:
        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        session_id = session["id"]
        client.patch(
            f"/api/sessions/{session_id}/draft",
            json={"items": [{"product_id": "nasi_lemak", "quantity": 2, "note": "extra sambal"}]},
        )
        draft = client.get(f"/api/sessions/{session_id}/draft").json()
        assert draft["items"][0]["note"] == "extra sambal"

        order = client.post(f"/api/sessions/{session_id}/draft/checkout").json()
        assert order["items"][0]["note"] == "extra sambal"
    finally:
        _close(store)


def test_stock_reaches_zero_marks_product_unavailable(tmp_path):
    client, store = _client(tmp_path)
    try:
        created = client.post(
            "/api/stores/demo/products",
            json={"name_en": "Popiah", "price_minor": 600, "stock_count": 2},
        )
        assert created.status_code == 201
        product = created.json()
        assert product["id"] == "popiah"
        assert product["available"] is True

        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        client.post(
            "/api/orders",
            json={
                "store_id": "demo",
                "session_id": session["id"],
                "items": [{"product_id": "popiah", "quantity": 2}],
                "customer_language": "en",
            },
        )

        menu = client.get("/api/stores/demo/menu").json()["products"]
        popiah = next(item for item in menu if item["id"] == "popiah")
        assert popiah["stock_count"] == 0
        assert popiah["available"] is False

        rejected = client.post(
            "/api/orders",
            json={
                "store_id": "demo",
                "session_id": session["id"],
                "items": [{"product_id": "popiah", "quantity": 1}],
                "customer_language": "en",
            },
        )
        assert rejected.status_code == 404
    finally:
        _close(store)


def test_vendor_can_edit_and_delete_a_menu_item(tmp_path):
    client, store = _client(tmp_path)
    try:
        created = client.post(
            "/api/stores/demo/products",
            json={"name_en": "Rojak", "price_minor": 450, "stock_count": 10},
        ).json()

        updated = client.patch(
            f"/api/stores/demo/products/{created['id']}",
            json={"name_en": "Rojak", "price_minor": 500, "stock_count": 5, "available": True},
        )
        assert updated.status_code == 200
        assert updated.json()["price_minor"] == 500
        assert updated.json()["stock_count"] == 5

        deleted = client.delete(f"/api/stores/demo/products/{created['id']}")
        assert deleted.status_code == 204

        missing = client.patch(
            f"/api/stores/demo/products/{created['id']}",
            json={"name_en": "Rojak", "price_minor": 500, "stock_count": 5},
        )
        assert missing.status_code == 404
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
                "items": [{"product_id": "nasi_goreng", "quantity": 1}],
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
