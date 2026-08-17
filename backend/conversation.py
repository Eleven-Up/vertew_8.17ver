"""Conversation_Server orchestration: the per-turn transcript handling pipeline.

This module wires the deterministic pure-function core into the single
side-effecting flow that turns a customer transcript into a character response
(see design.md, "Conversation_Server"):

    Prompt_Builder.build
        -> LLM_Client.complete (10s timeout, <=1 retry)
        -> LLM_Client.parse
        -> ProfanityFilter.apply
        -> Data_Store.record_turn
        -> return response

The orchestration owns two error boundaries:

- **Network failure (Req 4.5, 9.5):** when the Gemini Flash call is unreachable,
  errors, or exceeds its timeout, :func:`complete` raises
  :class:`~llm.GeminiUnavailableError`. ``handle_transcript`` catches it and returns
  :data:`~llm.FALLBACK_UNAVAILABLE` (neutral/idle), aborting the turn with **no
  partial output** — no turn is recorded for a failed network call, so storage
  reflects only completed exchanges.
- **Successful turn (Req 4.3):** the parsed-and-filtered response is recorded via
  :meth:`~db.DataStore.record_turn` and returned to the caller (the WebSocket layer
  wired in Task 8.3), and the in-memory :class:`ConversationSession` is updated so
  the next prompt carries this turn in its recent-history window.

The :class:`~db.DataStore` and the Gemini HTTP client are injected so the pipeline
is importable and testable without a running web server or real network/disk
(Task 8.2 mocks the network-failure path).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

import llm
import profanity
import prompt_builder
from db import DataStore
from llm import GeminiClient, GeminiUnavailableError
from models import CharacterResponse, ConversationTurn, StoreInfo

# Maximum number of recent Conversation_Turns carried into a prompt (Req 3.2).
# Mirrors prompt_builder.MAX_RECENT_TURNS; the builder also clamps defensively.
MAX_RECENT_TURNS = prompt_builder.MAX_RECENT_TURNS


class ConversationSession:
    """In-memory holder of recent Conversation_Turns for prompt history (Req 3.2).

    Tracks the chronological sequence of completed turns and exposes the most
    recent ``max`` of them for the Prompt_Builder. The session can be seeded from
    the persisted log at startup via :meth:`from_store` so history survives a
    restart, and is appended to after each successfully recorded turn.
    """

    def __init__(self, recent_turns: list[ConversationTurn] | None = None) -> None:
        self._turns: list[ConversationTurn] = list(recent_turns) if recent_turns else []

    @classmethod
    def from_store(cls, data_store: DataStore) -> "ConversationSession":
        """Seed a session from the persisted conversation log (chronological)."""
        return cls(recent_turns=data_store.load_turns())

    def recent_turns(self, max: int = MAX_RECENT_TURNS) -> list[ConversationTurn]:
        """Return the most recent ``max`` turns in chronological order (Req 3.2)."""
        if max <= 0:
            return []
        return self._turns[-max:]

    def add_turn(self, turn: ConversationTurn) -> None:
        """Append a completed turn so it appears in subsequent prompt history."""
        self._turns.append(turn)


def _utcnow() -> datetime:
    """Return the current UTC time (injectable default for :func:`handle_transcript`)."""
    return datetime.now(timezone.utc)


async def handle_transcript(
    transcript: str,
    *,
    data_store: DataStore,
    session: ConversationSession,
    llm_client: GeminiClient | None = None,
    language: str = "en",
    local_on_unavailable: bool = False,
    now: Callable[[], datetime] = _utcnow,
) -> CharacterResponse:
    """Run one Conversation_Turn from transcript to character response.

    Builds the prompt from the current persona/store info and the session's recent
    turns, issues the single Gemini Flash call, parses and content-filters the
    result, records the completed turn, and returns the safe response (Req 4.3).

    On a Gemini network failure (unreachable, error status, or timeout) the call
    aborts with **no partial output**: :data:`~llm.FALLBACK_UNAVAILABLE`
    (neutral/idle) is returned and **no** turn is recorded, preserving the
    conversation state (Req 4.5, 9.5).

    Args:
        transcript: The customer's latest utterance transcript (>=1 recognized
            word, gated upstream by the Speech_Module).
        data_store: Repository providing current store info and turn persistence.
        session: In-memory recent-turn history for the prompt window.
        llm_client: Optional Gemini HTTP boundary; defaults to the shared client.
            Tests inject a mock to exercise the success and network-failure paths.
        now: Callable returning the completion timestamp; injectable for tests.

    Returns:
        The :class:`CharacterResponse` to deliver to the Kiosk_UI — either the
        parsed-and-filtered model response or a predefined fallback.
    """
    store_info: StoreInfo | None = data_store.load_store_info()
    persona = store_info.persona if store_info is not None else None

    prompt = prompt_builder.build(
        persona,
        store_info,
        session.recent_turns(max=MAX_RECENT_TURNS),
        transcript,
    )

    # Single combined Gemini call (10s timeout, <=1 retry inside complete()).
    try:
        raw = await llm.complete(prompt, client=llm_client)
    except GeminiUnavailableError:
        # Network unreachable/error/timeout: abort with no partial output and do
        # not record a turn for the failed call (Req 4.5, 9.5).
        if local_on_unavailable:
            return _local_response(transcript, language)
        return llm.FALLBACK_UNAVAILABLE

    # Success path: parse -> profanity filter -> persist -> return (Req 4.3).
    parsed = llm.parse(raw)
    safe = profanity.apply(parsed)

    completed_at = now()
    data_store.record_turn(transcript, safe.text, completed_at)
    session.add_turn(
        ConversationTurn(
            customer_text=transcript,
            character_text=safe.text,
            completed_at=completed_at,
        )
    )
    return safe


def _local_response(transcript: str, language: str) -> CharacterResponse:
    """Useful multilingual demo response when the optional cloud LLM is offline."""
    text = transcript.lower()
    prices = {"watermelon": 4, "mango": 5, "banana": 3, "apple": 3.5,
              "수박": 4, "망고": 5, "바나나": 3, "사과": 3.5,
              "tembikai": 4, "mangga": 5, "pisang": 3, "epal": 3.5}
    product = next(((name, price) for name, price in prices.items() if name in text), None)
    asks_price = any(word in text for word in ("price", "how much", "얼마", "가격", "berapa", "harga"))
    wants_order = any(word in text for word in ("order", "buy", "take one", "주문", "살게", "주세요", "pesan", "beli"))
    lang = language if language in {"en", "ko", "ms"} else "en"
    if product and asks_price:
        price = product[1]
        copies = {"en": f"It is RM {price:g}. You can scan the QR code to order!",
                  "ko": f"가격은 RM {price:g}입니다. QR 코드를 스캔해서 주문해 주세요!",
                  "ms": f"Harganya RM {price:g}. Imbas kod QR untuk membuat pesanan!"}
    elif wants_order:
        copies = {"en": "Great! Please scan the QR code to place your order.",
                  "ko": "좋아요! QR 코드를 스캔해서 주문해 주세요.",
                  "ms": "Baik! Sila imbas kod QR untuk membuat pesanan."}
    else:
        copies = {"en": "We have fresh watermelon, mango, banana, and apple. What would you like?",
                  "ko": "신선한 수박, 망고, 바나나, 사과가 있어요. 어떤 과일을 원하세요?",
                  "ms": "Kami ada tembikai, mangga, pisang dan epal segar. Anda mahu yang mana?"}
    return CharacterResponse(copies[lang], "happy", "wave" if wants_order else "nod", True)
