"""Local Speech-to-Text API: transcribe one recorded audio clip via faster-whisper.

Plain HTTP (not the conversation WebSocket) so it composes cleanly with the
Kiosk_UI's SttProvider abstraction -- see frontend/js/speech.ts's LocalSttProvider,
which records with MediaRecorder client-side and posts the finished clip here.
Only reachable/meaningful when STT_PROVIDER=local (see /api/stt/config).
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

import stt

router = APIRouter(prefix="/api/stt", tags=["stt"])

# Safety cap well above any real utterance (MAX_CAPTURE_MS caps clips at 30s
# client-side); guards against a misbehaving/malicious client sending something huge.
MAX_AUDIO_BYTES = 10 * 1024 * 1024


@router.get("/config")
def stt_config() -> dict:
    """Tell the Kiosk_UI which STT backend to use for this deployment."""
    return {"provider": "local" if stt.is_local_provider_enabled() else "browser"}


@router.post("/transcribe")
async def transcribe(audio: UploadFile, language: str | None = Form(default=None)) -> dict:
    """Transcribe one uploaded audio clip and return its text."""
    body = await audio.read()
    if not body:
        raise HTTPException(400, "Empty audio upload")
    if len(body) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio clip too large")
    try:
        text = await run_in_threadpool(stt.get_engine().transcribe, body, language)
    except stt.SttUnavailableError as exc:
        raise HTTPException(503, f"Local STT unavailable: {exc}") from exc
    return {"text": text}
