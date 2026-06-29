"""Core data models and allowed value sets for the Vertew Conversation_Server.

This module is the single source of truth for the Emotion/Gesture value sets and
the pure data structures shared across the server core. The normalization helpers
guarantee that every emotion/gesture string maps to a value within the supported
sets, falling back to the neutral/idle defaults for missing, empty, or unknown
values (Req 4.6, 5.2, 5.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# ---------------------------------------------------------------------------
# Allowed value sets (single source of truth, mirrored to the renderer).
# Each named value maps to exactly one expression/motion of the 2D character.
# ---------------------------------------------------------------------------
EMOTIONS: frozenset[str] = frozenset({"happy", "neutral", "surprised", "sad", "angry"})
GESTURES: frozenset[str] = frozenset({"wave", "idle", "point", "nod", "think"})

DEFAULT_EMOTION: str = "neutral"
DEFAULT_GESTURE: str = "idle"


@dataclass
class CharacterResponse:
    """A single character response delivered to the Kiosk_UI.

    ``text`` is non-empty for valid responses and at most 1000 characters after
    parsing (Req 4.2). ``emotion`` belongs to :data:`EMOTIONS` (or is normalized to
    ``"neutral"``) and ``gesture`` belongs to :data:`GESTURES` (or is normalized to
    ``"idle"``). ``is_fallback`` marks predefined fallback responses.
    """

    text: str
    emotion: str
    gesture: str
    is_fallback: bool = False


@dataclass
class StoreInfo:
    """Vendor store and product information entered through the Admin_Interface.

    ``store_name`` and ``products`` are required (1..2000 chars, Req 7.3); ``persona``
    is optional (1..500 chars when set, Req 7.7).
    """

    store_name: str
    products: str
    persona: str | None = None


@dataclass
class ConversationTurn:
    """A completed exchange of one customer utterance and one character response."""

    customer_text: str
    character_text: str
    completed_at: datetime


def normalize_emotion(emotion: str | None) -> str:
    """Return ``emotion`` when it is a supported Emotion, else ``DEFAULT_EMOTION``.

    Missing (``None``), empty/whitespace-only, or out-of-set values normalize to
    ``"neutral"`` (Req 4.6, 5.2, 5.6).
    """
    if emotion is not None and emotion in EMOTIONS:
        return emotion
    return DEFAULT_EMOTION


def normalize_gesture(gesture: str | None) -> str:
    """Return ``gesture`` when it is a supported Gesture, else ``DEFAULT_GESTURE``.

    Missing (``None``), empty/whitespace-only, or out-of-set values normalize to
    ``"idle"`` (Req 4.6, 5.2, 5.6).
    """
    if gesture is not None and gesture in GESTURES:
        return gesture
    return DEFAULT_GESTURE
