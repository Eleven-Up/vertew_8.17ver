"""Transcript analysis API bridging STT text to session and hologram events."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from commerce_api import get_data_store
from db import DataStore
from intent import LANGUAGE_CONFIDENCE_THRESHOLD, analyze_transcript
from models import LanguageSource
from realtime import manager

router = APIRouter(prefix="/api/ai", tags=["ai"])


class TranscriptRequest(BaseModel):
    session_id: str
    transcript: str = Field(min_length=1, max_length=2000)


@router.post("/analyze-transcript")
async def analyze(body: TranscriptRequest, store: DataStore = Depends(get_data_store)) -> dict:
    session = store.get_session(body.session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    result = analyze_transcript(body.transcript, session.language)
    await manager.broadcast(
        session.store_id,
        "language_detected",
        {"language": result.language, "confidence": result.confidence},
        session_id=session.id,
    )
    if result.confidence >= LANGUAGE_CONFIDENCE_THRESHOLD:
        updated = store.update_session_language(
            session.id, result.language, LanguageSource.AUTO_DETECTED,
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
    if result.intent in {"interest", "purchase"}:
        await manager.broadcast(
            session.store_id,
            "keyword_detected",
            {"intent": result.intent, "confidence": result.intent_confidence, "transcript": body.transcript},
            session_id=session.id,
        )
    if result.intent == "purchase":
        await manager.broadcast(
            session.store_id, "show_qr", {"reason": "purchase_intent"}, session_id=session.id
        )
    return {**asdict(result), "current_language": session.language}
