"""Transcript analysis API bridging STT text to session and hologram events."""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from commerce_api import get_data_store
from db import DataStore
from intent import LANGUAGE_CONFIDENCE_THRESHOLD, TranscriptAnalysis, analyze_transcript, is_plausible_for_text
from models import LanguageSource
from realtime import manager

router = APIRouter(prefix="/api/ai", tags=["ai"])

# How many consecutive confident auto-detections of the SAME new language are
# required before it actually replaces the session's current language.
REQUIRED_CONSECUTIVE_LANGUAGE_SWITCHES = 2

# session_id -> (candidate_language, consecutive confident readings of it so
# far). Speech-to-text language detection is inherently noisy for short or
# acoustically ambiguous utterances -- even a well-calibrated confidence score
# (see stt.GroqWhisperEngine._groq_confidence, intent.is_plausible_for_text)
# can occasionally be confidently wrong for a single utterance. Without this,
# one flaky reading instantly flips a customer's session away from the
# language they've been consistently speaking. In-memory and per-process is
# fine here: this is transient turn-taking state, not something that needs to
# survive a restart (mirrors conversation.ConversationSession's in-memory
# history). A USER_SELECTED language change (the PATCH .../language endpoint)
# is a separate, explicit, high-trust signal and is unaffected by this.
_pending_language_switch: dict[str, tuple[str, int]] = {}


def _debounced_auto_language(session_id: str, current_language: str, candidate: str) -> str | None:
    """Decide whether this turn's confident auto-detected ``candidate``
    language should actually be applied.

    Reconfirming the session's current language always clears any pending
    switch and applies immediately (never held back). A genuinely different
    language must be confidently detected
    :data:`REQUIRED_CONSECUTIVE_LANGUAGE_SWITCHES` times in a row -- any other
    reading in between (back to the original language, or a third one) resets
    the count. Returns the language to apply, or ``None`` to hold the switch
    pending for now (the session keeps its current language this turn).
    """
    if candidate == current_language:
        _pending_language_switch.pop(session_id, None)
        return candidate
    pending_language, count = _pending_language_switch.get(session_id, (None, 0))
    count = count + 1 if pending_language == candidate else 1
    if count >= REQUIRED_CONSECUTIVE_LANGUAGE_SWITCHES:
        _pending_language_switch.pop(session_id, None)
        return candidate
    _pending_language_switch[session_id] = (candidate, count)
    return None


class TranscriptRequest(BaseModel):
    session_id: str
    transcript: str = Field(min_length=1, max_length=2000)
    detected_language: Literal["en", "ko", "ms"] | None = None
    language_confidence: float | None = Field(default=None, ge=0, le=1)


@router.post("/analyze-transcript")
async def analyze(
    body: TranscriptRequest,
    store: DataStore = Depends(get_data_store),  # noqa: B008
) -> dict[str, object]:
    session = store.get_session(body.session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    whisper_language = (
        body.detected_language
        if body.detected_language
        and (body.language_confidence or 0) >= LANGUAGE_CONFIDENCE_THRESHOLD
        and is_plausible_for_text(body.detected_language, body.transcript)
        else None
    )
    result = analyze_transcript(
        body.transcript,
        whisper_language or session.language,
    )
    if whisper_language:
        # Whisper heard the actual audio, so its confident language result is
        # stronger evidence than a short transcript with too few marker words.
        result = TranscriptAnalysis(
            whisper_language,
            body.language_confidence or 0,
            result.intent,
            result.intent_confidence,
        )
    await manager.broadcast(
        session.store_id,
        "language_detected",
        {"language": result.language, "confidence": result.confidence},
        session_id=session.id,
    )
    if result.confidence >= LANGUAGE_CONFIDENCE_THRESHOLD:
        # A session still on the untouched DEFAULT language has no established
        # language to protect yet -- trust the very first confident reading
        # immediately rather than debouncing it (that's the customer's actual
        # first utterance establishing their language, not a switch away from
        # one they've already been speaking).
        to_apply = (
            result.language
            if session.language_source == LanguageSource.DEFAULT
            else _debounced_auto_language(session.id, session.language, result.language)
        )
        if to_apply is not None:
            updated = store.update_session_language(
                session.id, to_apply, LanguageSource.AUTO_DETECTED,
                confidence=result.confidence,
            )
            if updated and updated.language != session.language:
                await manager.broadcast(
                    session.store_id,
                    "language_changed",
                    {"language": updated.language, "language_source": updated.language_source.value},
                    session_id=session.id,
                )
                session = updated
    if result.intent in {"interest", "purchase", "confirm_payment"}:
        await manager.broadcast(
            session.store_id,
            "keyword_detected",
            {"intent": result.intent, "confidence": result.intent_confidence, "transcript": body.transcript},
            session_id=session.id,
        )
    # The kiosk has no touchscreen, so the payment QR appears only on an explicit
    # "yes, pay now" confirmation -- merely recognizing a purchase/order intent no
    # longer pops it (see main.handle_websocket's confirm_payment gate, which
    # requires the same explicit confirmation from the LLM conversation path).
    if result.intent == "confirm_payment":
        await manager.broadcast(
            session.store_id, "show_qr", {"reason": "payment_confirmed"}, session_id=session.id
        )
    return {**asdict(result), "current_language": session.language}
