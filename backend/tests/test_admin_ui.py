"""Tests for the vendor admin UI: product editing and Q&A review/approval."""

from fastapi.testclient import TestClient

import admin
from db import DataStore
from main import app


def _client(tmp_path):
    store = DataStore(str(tmp_path / "admin.db"))
    store.seed_demo_data()
    app.dependency_overrides[admin.get_data_store] = lambda: store
    return TestClient(app), store


def _close(store):
    app.dependency_overrides.pop(admin.get_data_store, None)
    store.close()


# --- navigation -------------------------------------------------------------

def test_store_info_page_has_nav_links(tmp_path):
    client, store = _client(tmp_path)
    try:
        html = client.get("/admin").text
        assert "/admin/products" in html
        assert "/admin/qa" in html
    finally:
        _close(store)


# --- product editing --------------------------------------------------------

def test_products_page_lists_products(tmp_path):
    client, store = _client(tmp_path)
    try:
        html = client.get("/admin/products").text
        assert "Mango" in html
        assert "Spice level" in html
    finally:
        _close(store)


def test_edit_product_persists_menu_fields(tmp_path):
    client, store = _client(tmp_path)
    try:
        resp = client.post(
            "/admin/products/mango",
            data={
                "spice_level": "3",
                "ing_en": "fresh mango, chili-lime salt",
                "ing_ko": "신선한 망고, 칠리라임 소금",
                "ing_ms": "",
                "allergens": "nuts, Dairy",
                "available": "on",
            },
        )
        assert resp.status_code == 200
        assert "Saved" in resp.text

        mango = {p.id: p for p in store.list_products("demo")}["mango"]
        assert mango.spice_level == 3
        assert mango.ingredients["en"] == "fresh mango, chili-lime salt"
        assert mango.ingredients["ko"].startswith("신선한 망고")
        assert "ms" not in mango.ingredients  # empty field dropped
        assert mango.allergens == ("nuts", "dairy")  # normalized lowercase
        assert mango.available is True
    finally:
        _close(store)


def test_edit_product_unchecked_available_sets_false(tmp_path):
    client, store = _client(tmp_path)
    try:
        client.post(
            "/admin/products/apple",
            data={"spice_level": "0", "allergens": ""},  # no 'available' -> unchecked
        )
        apple = {p.id: p for p in store.list_products("demo")}["apple"]
        assert apple.available is False
    finally:
        _close(store)


def test_edit_unknown_product_returns_404(tmp_path):
    client, store = _client(tmp_path)
    try:
        resp = client.post("/admin/products/nope", data={"spice_level": "0"})
        assert resp.status_code == 404
    finally:
        _close(store)


# --- Q&A review -------------------------------------------------------------

def test_qa_page_shows_pending_entry(tmp_path):
    client, store = _client(tmp_path)
    try:
        store.add_pending_qa("demo", "Do you have durian?", {"en": "Yes, in season."})
        html = client.get("/admin/qa").text
        assert "Do you have durian?" in html
        assert "Pending" in html
    finally:
        _close(store)


def test_approve_pending_qa_makes_it_grounding(tmp_path):
    client, store = _client(tmp_path)
    try:
        entry = store.add_pending_qa("demo", "Do you have durian?", {"en": "old"})
        resp = client.post(
            f"/admin/qa/{entry.id}/approve",
            data={"question": "Do you sell durian?", "answer_en": "Yes, when in season!", "answer_ko": "", "answer_ms": ""},
        )
        assert resp.status_code == 200
        assert store.list_qa("demo", status="pending") == []
        approved = {e.id: e for e in store.list_qa("demo", status="approved")}
        assert entry.id in approved
        assert approved[entry.id].question == "Do you sell durian?"
        assert approved[entry.id].answer["en"] == "Yes, when in season!"
    finally:
        _close(store)


def test_approve_without_answer_is_rejected_with_400(tmp_path):
    client, store = _client(tmp_path)
    try:
        entry = store.add_pending_qa("demo", "Q?", {"en": "a"})
        resp = client.post(
            f"/admin/qa/{entry.id}/approve",
            data={"question": "Q?", "answer_en": "", "answer_ko": "", "answer_ms": ""},
        )
        assert resp.status_code == 400
        # still pending, not approved
        assert entry.id in {e.id for e in store.list_qa("demo", status="pending")}
    finally:
        _close(store)


def test_reject_pending_qa_archives_it(tmp_path):
    client, store = _client(tmp_path)
    try:
        entry = store.add_pending_qa("demo", "Q?", {"en": "a"})
        resp = client.post(f"/admin/qa/{entry.id}/reject")
        assert resp.status_code == 200
        assert entry.id not in {e.id for e in store.list_qa("demo", status="pending")}
        assert entry.id not in {e.id for e in store.list_qa("demo", status="approved")}
    finally:
        _close(store)


def test_approve_unknown_qa_returns_404(tmp_path):
    client, store = _client(tmp_path)
    try:
        resp = client.post("/admin/qa/nope/approve", data={"answer_en": "x"})
        assert resp.status_code == 404
    finally:
        _close(store)
