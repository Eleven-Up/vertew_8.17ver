"""Speech_Module STT: optional local, fully-offline transcription.

Two backends are supported (mirrors the LLM_PROVIDER pattern in llm.py):

- ``browser`` (default): the Kiosk_UI uses the browser's own Web Speech API
  (frontend/js/speech.ts's WebSpeechSttProvider). No backend involvement at all.
- ``local``: the Kiosk_UI records audio and posts it here (see stt_api.py); this
  module transcribes it with faster-whisper (CTranslate2), entirely on-device, no
  network call. Useful when the browser's built-in STT can't reach Google's speech
  service (blocked network/extension) or for privacy/fully-offline operation.

faster-whisper is optimized for x86 (AVX2); on Raspberry Pi, whisper.cpp performs
better per-watt. Both would sit behind the same :class:`SttEngine` shape, so
swapping is a matter of adding another engine class here, not touching the API or
the Kiosk_UI.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Protocol

logger = logging.getLogger(__name__)

# Which STT backend the Kiosk_UI should use: "browser" | "local".
STT_PROVIDER_ENV: str = "STT_PROVIDER"
DEFAULT_STT_PROVIDER: str = "browser"

# faster-whisper model configuration (only used when STT_PROVIDER=local).
STT_MODEL_SIZE_ENV: str = "STT_MODEL_SIZE"
DEFAULT_STT_MODEL_SIZE: str = "small"
STT_DEVICE_ENV: str = "STT_DEVICE"
DEFAULT_STT_DEVICE: str = "cpu"
STT_COMPUTE_TYPE_ENV: str = "STT_COMPUTE_TYPE"
DEFAULT_STT_COMPUTE_TYPE: str = "int8"


class SttUnavailableError(RuntimeError):
    """Raised when local transcription cannot run (model load or decode failure)."""


class SttEngine(Protocol):
    """Turns a raw audio clip into text plus the language Whisper itself
    detected from the audio (code, confidence 0..1). One call transcribes one
    full utterance (batch, not streaming) -- see LocalSttProvider in
    frontend/js/speech.ts, which records client-side and posts the finished
    clip here."""

    def transcribe(self, audio_bytes: bytes) -> tuple[str, str, float]: ...


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
        self._model = None  # type: ignore[var-annotated]

    def _ensure_model(self):
        if self._model is None:
            # Lazy import: faster-whisper (and its ctranslate2/av dependencies) is
            # only required when the local STT provider is actually selected.
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            logger.info(
                "Loading local Whisper model (%s, %s/%s)...",
                self._model_size, self._device, self._compute_type,
            )
            self._model = WhisperModel(
                self._model_size, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, audio_bytes: bytes) -> tuple[str, str, float]:
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
            return text, info.language, info.language_probability
        except Exception as exc:  # noqa: BLE001 - any decode/inference failure
            raise SttUnavailableError(str(exc)) from exc
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


_default_engine: FasterWhisperEngine | None = None


def get_engine() -> FasterWhisperEngine:
    """Return the process-wide local STT engine (created once, reused)."""
    global _default_engine
    if _default_engine is None:
        _default_engine = FasterWhisperEngine()
    return _default_engine


def is_local_provider_enabled() -> bool:
    """True when STT_PROVIDER=local -- the Kiosk_UI should record+POST instead of
    using the browser's own Web Speech API."""
    return (os.environ.get(STT_PROVIDER_ENV) or DEFAULT_STT_PROVIDER).strip().lower() == "local"
