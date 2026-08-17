import pytest

import commerce_api
from db import DataStore
from intent import analyze_transcript, detect_language
from main import app
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("이거 망고예요?", "ko"),
        ("How much is the mango?", "en"),
        ("Berapa harga mangga ini?", "ms"),
    ],
)
def test_detects_supported_languages_with_high_confidence(text, language):
    detected, confidence = detect_language(text)
    assert detected == language
    assert confidence >= 0.8


def test_low_confidence_keeps_current_language():
    assert detect_language("hmm", "ko") == ("ko", 0.45)


@pytest.mark.parametrize(
    "text",
    ["주문하고 싶어요", "I'll take one mango", "Saya mahu beli mangga"],
)
def test_purchase_intent_is_semantically_detected(text):
    result = analyze_transcript(text)
    assert result.intent == "purchase"
    assert result.intent_confidence >= 0.72


@pytest.mark.parametrize(
    "text",
    ["이게 뭐지?", "What are they selling?", "Apa ini?"],
)
def test_interest_expressions_are_detected(text):
    assert analyze_transcript(text).intent in {"interest", "question"}


def test_analysis_api_updates_session_language(tmp_path):
    store = DataStore(str(tmp_path / "ai.db"))
    store.seed_demo_data()
    app.dependency_overrides[commerce_api.get_data_store] = lambda: store
    client = TestClient(app)
    try:
        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        response = client.post(
            "/api/ai/analyze-transcript",
            json={"session_id": session["id"], "transcript": "주문하고 싶어요"},
        )
        assert response.status_code == 200
        assert response.json()["language"] == "ko"
        assert response.json()["current_language"] == "ko"
        assert response.json()["intent"] == "purchase"
    finally:
        app.dependency_overrides.pop(commerce_api.get_data_store, None)
        store.close()
