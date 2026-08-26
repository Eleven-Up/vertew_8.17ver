"""Lightweight multilingual transcript analysis for the hardware-free MVP."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

LANGUAGE_CONFIDENCE_THRESHOLD = 0.8

MALAY_MARKERS = {
    "apa", "berapa", "ini", "mahu", "nak", "beli", "pesan", "boleh",
    "sila", "segar", "manis", "harga",
}
ENGLISH_MARKERS = {
    "what", "how", "this", "want", "buy", "order", "please", "can",
    "fresh", "sweet", "price", "much",
}
INTEREST_PHRASES = {
    "ko": ("이게 뭐지", "뭐 파는 거야", "이건 뭐야", "나시고랭 파네"),
    "en": ("what is this", "what are they selling", "what's that", "oh nasi goreng"),
    "ms": ("apa ini", "jual apa", "ada nasi goreng", "makanan apa"),
}
PURCHASE_PHRASES = {
    "ko": ("주문하고 싶", "살게요", "사고 싶", "하나 주세요", "주문할게"),
    "en": ("i want to buy", "i'll take", "how can i order", "place an order", "buy one"),
    "ms": ("saya mahu beli", "nak beli", "cara pesan", "mahu pesan", "beli satu"),
}
# Distinct from PURCHASE_PHRASES ("I want to order X") -- this is the explicit
# "yes, pay now" confirmation that reveals the payment QR on a touchless kiosk
# (see main.handle_websocket / ai_api.analyze).
CONFIRM_PAYMENT_PHRASES = {
    "ko": ("결제할게", "결제해주세요", "결제해줘", "계산할게", "계산해주세요", "네 결제", "지불할게"),
    "en": ("i'll pay", "let's pay", "pay now", "yes, pay", "ready to pay", "i want to pay"),
    "ms": ("saya nak bayar", "nak bayar sekarang", "boleh bayar", "ya, bayar"),
}


@dataclass(frozen=True)
class TranscriptAnalysis:
    language: str
    confidence: float
    intent: str
    intent_confidence: float


def analyze_transcript(text: str, current_language: str = "en") -> TranscriptAnalysis:
    normalized = _normalize(text)
    language, confidence = detect_language(normalized, current_language)
    confirm_payment_score = _phrase_score(normalized, CONFIRM_PAYMENT_PHRASES[language])
    purchase_score = _phrase_score(normalized, PURCHASE_PHRASES[language])
    interest_score = _phrase_score(normalized, INTEREST_PHRASES[language])
    # Checked first: "I'll pay now" must win over the (looser) purchase-phrase
    # match rather than being classified as just another order request.
    if confirm_payment_score >= 0.72:
        intent, intent_confidence = "confirm_payment", confirm_payment_score
    elif purchase_score >= 0.72:
        intent, intent_confidence = "purchase", purchase_score
    elif interest_score >= 0.7:
        intent, intent_confidence = "interest", interest_score
    elif normalized.endswith("?") or any(word in normalized.split() for word in ("what", "how", "apa", "뭐", "얼마")):
        intent, intent_confidence = "question", 0.75
    else:
        intent, intent_confidence = "other", 0.4
    return TranscriptAnalysis(language, confidence, intent, round(intent_confidence, 3))


def is_plausible_for_text(language: str, text: str) -> bool:
    """Cheap sanity check: is ``language`` consistent with what's actually in
    ``text``?

    Guards against trusting an upstream speech-to-text engine's per-clip
    language guess when the transcript itself contradicts it -- e.g. Whisper
    (via Groq) can report "ko" with a deceptively high confidence for a
    short/ambiguous clip whose actual transcribed text has no Hangul at all
    (see stt.GroqWhisperEngine._groq_confidence and the caller in ai_api.py),
    which otherwise reads as the session suddenly, spuriously switching to
    Korean. Latin-script languages (en/ms) aren't reliably distinguishable by
    charset alone, so this only rules out the impossible case of Hangul text
    claimed as non-Korean or non-Hangul text claimed as Korean.
    """
    has_hangul = bool(re.search(r"[가-힣]", text))
    if language == "ko":
        return has_hangul
    return not has_hangul


def detect_language(text: str, current_language: str = "en") -> tuple[str, float]:
    if re.search(r"[가-힣]", text):
        return "ko", 0.98
    words = set(re.findall(r"[a-z']+", text.lower()))
    malay = len(words & MALAY_MARKERS)
    english = len(words & ENGLISH_MARKERS)
    if malay >= 2 and malay > english:
        return "ms", min(0.96, 0.78 + malay * 0.05)
    if english >= 2 and english >= malay:
        return "en", min(0.96, 0.78 + english * 0.05)
    return current_language if current_language in {"en", "ko", "ms"} else "en", 0.45


def _normalize(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _phrase_score(text: str, phrases: tuple[str, ...]) -> float:
    if any(phrase in text for phrase in phrases):
        return 0.98
    return max((SequenceMatcher(None, text, phrase).ratio() for phrase in phrases), default=0.0)
