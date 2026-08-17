"""Lightweight multilingual transcript analysis for the hardware-free MVP."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

LANGUAGE_CONFIDENCE_THRESHOLD = 0.8

MALAY_MARKERS = {
    "apa", "berapa", "ini", "mahu", "nak", "beli", "pesan", "mangga",
    "pisang", "tembikai", "epal", "segar", "manis", "harga",
}
ENGLISH_MARKERS = {
    "what", "how", "this", "want", "buy", "order", "mango", "banana",
    "watermelon", "apple", "fresh", "sweet", "price", "much",
}
INTEREST_PHRASES = {
    "ko": ("이게 뭐지", "뭐 파는 거야", "이건 뭐야", "과일 파네"),
    "en": ("what is this", "what are they selling", "what's that", "oh mango"),
    "ms": ("apa ini", "jual apa", "ada mangga", "buah apa"),
}
PURCHASE_PHRASES = {
    "ko": ("주문하고 싶", "살게요", "사고 싶", "하나 주세요", "주문할게"),
    "en": ("i want to buy", "i'll take", "how can i order", "place an order", "buy one"),
    "ms": ("saya mahu beli", "nak beli", "cara pesan", "mahu pesan", "beli satu"),
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
    purchase_score = _phrase_score(normalized, PURCHASE_PHRASES[language])
    interest_score = _phrase_score(normalized, INTEREST_PHRASES[language])
    if purchase_score >= 0.72:
        intent, intent_confidence = "purchase", purchase_score
    elif interest_score >= 0.7:
        intent, intent_confidence = "interest", interest_score
    elif normalized.endswith("?") or any(word in normalized.split() for word in ("what", "how", "apa", "뭐", "얼마")):
        intent, intent_confidence = "question", 0.75
    else:
        intent, intent_confidence = "other", 0.4
    return TranscriptAnalysis(language, confidence, intent, round(intent_confidence, 3))


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
