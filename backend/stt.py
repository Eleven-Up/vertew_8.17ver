# mypy: disable-error-code=import-not-found

"""Speech_Module STT: optional local, fully-offline transcription.

Three backends are supported (mirrors the LLM_PROVIDER pattern in llm.py):

- ``browser`` (default): the Kiosk_UI uses the browser's own Web Speech API
  (frontend/js/speech.ts's WebSpeechSttProvider). No backend involvement at all.
- ``local``: the Kiosk_UI records audio and posts it here (see stt_api.py); this
  module transcribes it with faster-whisper (CTranslate2), entirely on-device, no
  network call. Useful when the browser's built-in STT can't reach Google's speech
  service (blocked network/extension) or for privacy/fully-offline operation.
- ``groq``: the Kiosk_UI uses the same single, noise-suppressed recording path but
  this module sends the clip to Groq Whisper. This avoids browser-extension and
  browser speech-service failures while retaining multilingual transcription.

faster-whisper is optimized for x86 (AVX2); on Raspberry Pi, whisper.cpp performs
better per-watt. Both would sit behind the same :class:`SttEngine` shape, so
swapping is a matter of adding another engine class here, not touching the API or
the Kiosk_UI.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Which STT backend the Kiosk_UI should use: "browser" | "local" | "groq".
STT_PROVIDER_ENV: str = "STT_PROVIDER"
DEFAULT_STT_PROVIDER: str = "browser"

# faster-whisper model configuration (only used when STT_PROVIDER=local).
STT_MODEL_SIZE_ENV: str = "STT_MODEL_SIZE"
DEFAULT_STT_MODEL_SIZE: str = "small"
STT_DEVICE_ENV: str = "STT_DEVICE"
DEFAULT_STT_DEVICE: str = "cpu"
STT_COMPUTE_TYPE_ENV: str = "STT_COMPUTE_TYPE"
DEFAULT_STT_COMPUTE_TYPE: str = "int8"

# Groq's OpenAI-compatible speech-to-text endpoint (used when STT_PROVIDER=groq).
GROQ_API_KEY_ENV: str = "GROQ_API_KEY"
GROQ_BASE_URL_ENV: str = "GROQ_BASE_URL"
DEFAULT_GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
GROQ_STT_MODEL_ENV: str = "GROQ_STT_MODEL"
DEFAULT_GROQ_STT_MODEL: str = "whisper-large-v3-turbo"
GROQ_STT_TIMEOUT_SECONDS: float = 30.0

SUPPORTED_LANGUAGE_CODES: frozenset[str] = frozenset({"en", "ko", "ms"})
_LANGUAGE_ALIASES: dict[str, str] = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "ko": "ko",
    "kor": "ko",
    "korean": "ko",
    "한국어": "ko",
    "ms": "ms",
    "msa": "ms",
    "may": "ms",
    "malay": "ms",
    "bahasa melayu": "ms",
}


def normalize_stt_language(language: object) -> str:
    """Normalize Whisper language labels to the kiosk's supported ISO codes."""
    value = str(language or "").strip().lower()
    return _LANGUAGE_ALIASES.get(value, value if value in SUPPORTED_LANGUAGE_CODES else "")


class SttUnavailableError(RuntimeError):
    """Raised when local transcription cannot run (model load or decode failure)."""


class SttEngine(Protocol):
    """Turns a raw audio clip into text plus the language Whisper itself
    detected from the audio (code, confidence 0..1). One call transcribes one
    full utterance (batch, not streaming) -- see LocalSttProvider in
    frontend/js/speech.ts, which records client-side and posts the finished
    clip here."""

    def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "clip.webm",
        content_type: str = "audio/webm",
    ) -> tuple[str, str, float]: ...


class FasterWhisperEngine:
    """:class:`SttEngine` backed by faster-whisper. The model is loaded lazily on
    first use (not at import/construction time) so the process starts quickly even
    when STT_PROVIDER=local, and so importing this module never requires the
    (optional) faster-whisper dependency to be installed.
    """

    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ) -> None:
        self._model_size = model_size or os.environ.get(STT_MODEL_SIZE_ENV) or DEFAULT_STT_MODEL_SIZE
        self._device = device or os.environ.get(STT_DEVICE_ENV) or DEFAULT_STT_DEVICE
        self._compute_type = (
            compute_type or os.environ.get(STT_COMPUTE_TYPE_ENV) or DEFAULT_STT_COMPUTE_TYPE
        )
        self._model: Any | None = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            # Lazy import: faster-whisper (and its ctranslate2/av dependencies) is
            # only required when the local STT provider is actually selected.
            from faster_whisper import WhisperModel

            logger.info(
                "Loading local Whisper model (%s, %s/%s)...",
                self._model_size, self._device, self._compute_type,
            )
            self._model = WhisperModel(
                self._model_size, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(
        self,
        audio_bytes: bytes,
        _filename: str = "clip.webm",
        _content_type: str = "audio/webm",
    ) -> tuple[str, str, float]:
        """Transcribe one complete audio clip and return (text, language, confidence).

        Always lets Whisper auto-detect the spoken language from the audio itself
        rather than being told which language to expect: forcing a language hint
        here previously meant a customer's first utterance in a non-English
        language was transcribed *as English* (garbled), which never produced the
        non-English characters the downstream session-language detection needs to
        switch away from English -- a self-reinforcing lock-in. Auto-detecting
        every time closes that loop.

        Runs synchronously (CPU-bound); callers on the FastAPI event loop should
        offload this to a thread (see stt_api.py's use of run_in_threadpool).
        """
        model = self._ensure_model()

        # Write to a real temp file rather than passing bytes/BytesIO directly:
        # faster-whisper decodes audio via PyAV, and Windows doesn't reliably allow
        # a second reader on a still-open NamedTemporaryFile, so the handle is
        # closed before decode starts and the file is removed afterward.
        fd, path = tempfile.mkstemp(suffix=".webm")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(audio_bytes)
            segments, info = model.transcribe(path, beam_size=1, vad_filter=True)
            text = "".join(segment.text for segment in segments).strip()
            return (
                text,
                normalize_stt_language(info.language),
                info.language_probability,
            )
        except Exception as exc:
            raise SttUnavailableError(str(exc)) from exc
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


class GroqWhisperEngine:
    """Fast multilingual cloud transcription using Groq's Whisper endpoint."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        *,
        http_client: Any | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get(GROQ_API_KEY_ENV)
        self._model = model or os.environ.get(GROQ_STT_MODEL_ENV) or DEFAULT_GROQ_STT_MODEL
        self._base_url = (
            base_url or os.environ.get(GROQ_BASE_URL_ENV) or DEFAULT_GROQ_BASE_URL
        ).rstrip("/")
        self._http: Any | None = http_client

    def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "clip.webm",
        content_type: str = "audio/webm",
    ) -> tuple[str, str, float]:
        if not self._api_key:
            raise SttUnavailableError(f"{GROQ_API_KEY_ENV} is not configured")

        try:
            import httpx

            if self._http is None:
                # Ignore stale machine-wide proxy settings; this service should
                # connect directly to the configured Groq endpoint.
                self._http = httpx.Client(
                    timeout=GROQ_STT_TIMEOUT_SECONDS,
                    trust_env=False,
                )
            response = self._http.post(
                f"{self._base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": (filename, audio_bytes, content_type)},
                data={
                    "model": self._model,
                    "response_format": "verbose_json",
                    "temperature": "0",
                },
            )
            response.raise_for_status()
            payload = response.json()
            text = str(payload.get("text") or "").strip()
            language = normalize_stt_language(payload.get("language"))
            probabilities = [
                1.0 - float(segment["no_speech_prob"])
                for segment in payload.get("segments", [])
                if isinstance(segment, dict) and segment.get("no_speech_prob") is not None
            ]
            confidence = (
                sum(probabilities) / len(probabilities)
                if probabilities
                else (1.0 if text else 0.0)
            )
            return text, language, max(0.0, min(1.0, confidence))
        except SttUnavailableError:
            raise
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f"HTTP {status}" if status is not None else str(exc)
            raise SttUnavailableError(f"Groq STT request failed: {detail}") from exc


_default_engine: SttEngine | None = None


def get_engine() -> SttEngine:
    """Return the configured process-wide server STT engine (created once)."""
    global _default_engine
    if _default_engine is None:
        provider = (os.environ.get(STT_PROVIDER_ENV) or DEFAULT_STT_PROVIDER).strip().lower()
        _default_engine = GroqWhisperEngine() if provider == "groq" else FasterWhisperEngine()
    return _default_engine


def is_local_provider_enabled() -> bool:
    """True for server-side STT -- record+POST instead of browser Web Speech."""
    provider = (os.environ.get(STT_PROVIDER_ENV) or DEFAULT_STT_PROVIDER).strip().lower()
    return provider in {"local", "groq"}
