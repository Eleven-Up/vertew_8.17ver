import pytest
from fastapi.testclient import TestClient

import commerce_api
from ai_api import _debounced_auto_language, _pending_language_switch
from db import DataStore
from intent import analyze_transcript, detect_language
from main import app


@pytest.fixture(autouse=True)
def _clear_pending_language_switches():
    # Module-level debounce state (see ai_api._pending_language_switch) must
    # not leak between tests that reuse the same session id pattern.
    _pending_language_switch.clear()
    yield
    _pending_language_switch.clear()


def test_debounced_auto_language_requires_two_consecutive_matching_readings():
    session_id = "s1"
    assert _debounced_auto_language(session_id, "en", "ms") is None
    assert _debounced_auto_language(session_id, "en", "ms") == "ms"


def test_debounced_auto_language_resets_on_a_different_candidate():
    session_id = "s2"
    assert _debounced_auto_language(session_id, "en", "ms") is None
    assert _debounced_auto_language(session_id, "en", "ko") is None  # different candidate, resets the count
    assert _debounced_auto_language(session_id, "en", "ko") == "ko"


def test_debounced_auto_language_reconfirming_current_language_applies_immediately():
    session_id = "s3"
    assert _debounced_auto_language(session_id, "en", "ms") is None
    assert _debounced_auto_language(session_id, "en", "en") == "en"
    # The earlier pending "ms" was cleared, so it takes two fresh readings again.
    assert _debounced_auto_language(session_id, "en", "ms") is None


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("이거 나시고랭이에요?", "ko"),
        ("How much is the nasi goreng?", "en"),
        ("Berapa harga nasi goreng ini?", "ms"),
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
    ["주문하고 싶어요", "I'll take one nasi goreng", "Saya mahu beli nasi goreng"],
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


@pytest.mark.parametrize(
    "text",
    ["결제할게요", "Yes, I'll pay", "Boleh saya nak bayar"],
)
def test_confirm_payment_is_distinct_from_purchase_intent(text):
    # A touchless kiosk only reveals the payment QR on this explicit
    # confirmation, never on a plain "I'd like to order" (see main.py /
    # ai_api.py's show_qr gating).
    result = analyze_transcript(text)
    assert result.intent == "confirm_payment"
    assert result.intent_confidence >= 0.72


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


def test_analysis_api_prefers_confident_whisper_language_after_two_confirmations(tmp_path):
    # A confident auto-detected language switch is held pending until the SAME
    # new language is confidently detected twice in a row (see
    # ai_api._debounced_auto_language) -- this is what "tolong satu" alone
    # exercises: the first reading is real per-utterance evidence but does not
    # yet flip the session, only a second matching one does.
    store = DataStore(str(tmp_path / "whisper-language.db"))
    store.seed_demo_data()
    app.dependency_overrides[commerce_api.get_data_store] = lambda: store
    client = TestClient(app)
    try:
        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        client.patch(
            f"/api/sessions/{session['id']}/language",
            json={"language": "ko", "language_source": "user_selected"},
        )
        body = {
            "session_id": session["id"],
            "transcript": "tolong satu",
            "detected_language": "ms",
            "language_confidence": 0.95,
        }
        first = client.post("/api/ai/analyze-transcript", json=body)
        assert first.status_code == 200
        assert first.json()["language"] == "ms"
        assert first.json()["current_language"] == "ko"  # not switched yet

        second = client.post("/api/ai/analyze-transcript", json=body)
        assert second.status_code == 200
        assert second.json()["current_language"] == "ms"  # confirmed, now switches
    finally:
        app.dependency_overrides.pop(commerce_api.get_data_store, None)
        store.close()


def test_analysis_api_does_not_switch_on_a_single_flaky_language_reading(tmp_path):
    # Regression: one confidently-but-wrongly detected language for an
    # isolated utterance must not flip a session away from the language the
    # customer has otherwise been consistently speaking.
    store = DataStore(str(tmp_path / "whisper-language-flaky.db"))
    store.seed_demo_data()
    app.dependency_overrides[commerce_api.get_data_store] = lambda: store
    client = TestClient(app)
    try:
        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        client.patch(
            f"/api/sessions/{session['id']}/language",
            json={"language": "en", "language_source": "user_selected"},
        )
        # One flaky "ms" reading, then back to genuine "en" speech.
        client.post(
            "/api/ai/analyze-transcript",
            json={
                "session_id": session["id"],
                "transcript": "tolong satu",
                "detected_language": "ms",
                "language_confidence": 0.95,
            },
        )
        response = client.post(
            "/api/ai/analyze-transcript",
            json={
                "session_id": session["id"],
                "transcript": "how much is the nasi goreng",
                "detected_language": "en",
                "language_confidence": 0.95,
            },
        )
        assert response.json()["current_language"] == "en"
    finally:
        app.dependency_overrides.pop(commerce_api.get_data_store, None)
        store.close()


def test_analysis_api_rejects_a_confident_but_implausible_whisper_language(tmp_path):
    # Regression: a speech-to-text engine can report a high confidence for a
    # short/ambiguous clip's language guess even when it's wrong (see
    # stt.GroqWhisperEngine._groq_confidence and intent.is_plausible_for_text)
    # -- observed as the session suddenly, spuriously switching to Korean.
    # "ko" claimed for a transcript with no Hangul at all must not be trusted;
    # the session should keep its current language rather than flip.
    store = DataStore(str(tmp_path / "whisper-language-implausible.db"))
    store.seed_demo_data()
    app.dependency_overrides[commerce_api.get_data_store] = lambda: store
    client = TestClient(app)
    try:
        session = client.post("/api/sessions", json={"store_id": "demo"}).json()
        client.patch(
            f"/api/sessions/{session['id']}/language",
            json={"language": "en", "language_source": "user_selected"},
        )
        response = client.post(
            "/api/ai/analyze-transcript",
            json={
                "session_id": session["id"],
                "transcript": "uh",
                "detected_language": "ko",
                "language_confidence": 0.95,
            },
        )
        assert response.status_code == 200
        assert response.json()["current_language"] == "en"
    finally:
        app.dependency_overrides.pop(commerce_api.get_data_store, None)
        store.close()
