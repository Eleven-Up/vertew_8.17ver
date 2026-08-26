from __future__ import annotations

import httpx
import pytest

import stt


def test_groq_whisper_posts_audio_and_returns_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        assert request.url.path == "/openai/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer test-key"
        assert b"whisper-large-v3-turbo" in body
        assert b"sample-audio" in body
        return httpx.Response(
            200,
            json={
                "text": "hello market",
                "language": "ko",
                "segments": [{"no_speech_prob": 0.1}],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    engine = stt.GroqWhisperEngine(
        api_key="test-key",
        base_url="https://api.groq.com/openai/v1",
        http_client=client,
    )

    text, language, confidence = engine.transcribe(
        b"sample-audio",
        "clip.webm",
        "audio/webm",
    )
    assert text == "hello market"
    assert language == "ko"
    assert confidence == pytest.approx(0.9)


def test_groq_whisper_requires_api_key() -> None:
    engine = stt.GroqWhisperEngine(api_key="")
    with pytest.raises(stt.SttUnavailableError, match="GROQ_API_KEY"):
        engine.transcribe(b"audio")


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("English", "en"),
        ("Korean", "ko"),
        ("Malay", "ms"),
        ("Bahasa Melayu", "ms"),
        ("unsupported", ""),
    ],
)
def test_normalizes_whisper_language_labels(label: str, expected: str) -> None:
    assert stt.normalize_stt_language(label) == expected


@pytest.mark.parametrize("provider", ["local", "groq"])
def test_server_stt_providers_enable_recording(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    monkeypatch.setenv(stt.STT_PROVIDER_ENV, provider)
    assert stt.is_local_provider_enabled()


def test_browser_provider_does_not_enable_server_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(stt.STT_PROVIDER_ENV, "browser")
    assert not stt.is_local_provider_enabled()
