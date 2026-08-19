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
import retrieval
from db import DataStore
from llm import GeminiClient, GeminiUnavailableError
from models import CharacterResponse, ConversationTurn, Product, StoreInfo

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
    store_id: str | None = None,
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

    # Ground the assistant in the structured product catalog so it can answer
    # "what is in this?" / "how spicy is it?" from real data. When a store_id is
    # provided and has products, the localized menu-knowledge block is merged into
    # the store info fed to the Prompt_Builder. With no store_id (or no products)
    # the behavior is unchanged.
    products = data_store.list_products(store_id) if store_id else []
    qa_entries = data_store.list_qa(store_id) if store_id else []
    # Retrieve only the most relevant curated answers so the prompt stays focused as
    # the Q&A table grows (top-k; a no-op ordering for small tables).
    if qa_entries:
        qa_entries = retrieval.select_relevant_qa(transcript, qa_entries)
    # Curated Q&A first (preferred over generation), then structured menu facts.
    knowledge_blocks = [
        prompt_builder.format_qa_knowledge(qa_entries, language),
        prompt_builder.format_menu_knowledge(products, language),
    ]
    knowledge = "\n\n".join(block for block in knowledge_blocks if block)
    effective_store_info = _augment_store_info(store_info, knowledge)

    prompt = prompt_builder.build(
        persona,
        effective_store_info,
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
            return _local_response(transcript, language, products)
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

    # Learning loop: when the assistant generated an answer that did not come from a
    # curated QA entry (and was neither a fallback nor an escalation), capture it as
    # a pending QA entry the vendor can review and approve, so the Q&A table grows
    # over time. Gated to genuine-looking questions and to turns with a known store.
    if (
        store_id
        and not safe.is_fallback
        and safe.action == "answer"
        and safe.matched_qa_id is None
        and _looks_like_question(transcript)
    ):
        data_store.add_pending_qa(
            store_id, transcript.strip(), {language: safe.text}, source="generated"
        )

    return safe


def _augment_store_info(
    store_info: StoreInfo | None, knowledge: str
) -> StoreInfo | None:
    """Merge the grounding knowledge block (curated Q&A + structured menu) into the
    store info for the prompt.

    Returns ``store_info`` unchanged when there is no knowledge (preserving the
    original behavior). Otherwise the knowledge is appended to any vendor-entered
    free-text product notes so both reach the Prompt_Builder. When no store info has
    been saved yet, a minimal :class:`StoreInfo` carrying just the knowledge is
    returned so the assistant is still grounded.
    """
    if not knowledge:
        return store_info
    if store_info is None:
        return StoreInfo(store_name="our stall", products=knowledge, persona=None)
    notes = store_info.products.strip()
    combined = f"{notes}\n\n{knowledge}" if notes else knowledge
    return StoreInfo(
        store_name=store_info.store_name,
        products=combined,
        persona=store_info.persona,
    )


# Question markers (en/ko/ms) used to gate what the learning loop captures, so the
# pending Q&A table collects genuine questions rather than greetings or chatter.
_QUESTION_WORDS = (
    "?", "？",
    "what", "how", "why", "when", "where", "which", "who", "is ", "are ", "do ",
    "does ", "can ", "could ",
    "뭐", "무엇", "어떻", "어떤", "왜", "언제", "어디", "얼마", "있나요", "있어요",
    "나요", "까요", "인가요", "가요",
    "apa", "bagaimana", "kenapa", "mengapa", "bila", "mana", "berapa", "adakah",
    "boleh",
)


def _looks_like_question(transcript: str) -> bool:
    """Heuristic: does ``transcript`` read like a genuine question worth learning?"""
    text = transcript.lower().strip()
    if len(text.split()) < 1:
        return False
    return any(marker in text for marker in _QUESTION_WORDS)


def _match_product(text: str, products: list[Product]) -> Product | None:
    """Return the first product whose id or any localized name appears in ``text``."""
    for product in products:
        candidates = [product.id, *product.name.values()]
        if any(candidate and candidate.lower() in text for candidate in candidates):
            return product
    return None


# Trigger words (across en/ko/ms) that mark a menu question the offline fallback can
# answer directly from the structured catalog.
_INGREDIENT_WORDS = ("ingredient", "what's in", "whats in", "what is in", "contain",
                     "들어", "재료", "성분", "bahan", "apa dalam")
_SPICE_WORDS_Q = ("spicy", "spiciness", "hot", "매워", "매운", "맵", "pedas")
_ALLERGEN_WORDS = ("allerg", "알레르기", "견과", "peanut", "nut", "alahan", "alergi")


def _local_product_answer(
    product: Product,
    language: str,
    asks_spice: bool,
    asks_ingredient: bool,
    asks_allergen: bool,
) -> CharacterResponse:
    """Compose an offline, catalog-grounded answer about a single product."""
    name = product.name.get(language) or product.name.get("en") or product.id
    ingredients = product.ingredients.get(language) or product.ingredients.get("en") or ""
    spice = prompt_builder.spice_word(product.spice_level, language)
    allergens = ", ".join(product.allergens)

    clauses: list[str] = []
    if language == "ko":
        none_word = "없음"
        if asks_ingredient and ingredients:
            clauses.append(f"{ingredients}(으)로 만들어요")
        if asks_spice:
            clauses.append(f"맵기는 '{spice}' 정도예요")
        if asks_allergen:
            clauses.append(f"알레르기 정보는 {allergens or none_word}")
        body = ", ".join(clauses) if clauses else f"{name}에 대해 알려드릴게요"
        text = f"{name}은(는) {body}. QR 코드를 스캔해서 주문하실 수 있어요!"
    elif language == "ms":
        none_word = "tiada"
        if asks_ingredient and ingredients:
            clauses.append(f"diperbuat daripada {ingredients}")
        if asks_spice:
            clauses.append(f"tahap kepedasan: {spice}")
        if asks_allergen:
            clauses.append(f"alergen: {allergens or none_word}")
        body = ", ".join(clauses) if clauses else "boleh saya bantu"
        text = f"{name} — {body}. Imbas kod QR untuk membuat pesanan!"
    else:
        none_word = "none declared"
        if asks_ingredient and ingredients:
            clauses.append(f"it's made with {ingredients}")
        if asks_spice:
            clauses.append(f"it's {spice}")
        if asks_allergen:
            clauses.append(f"allergens: {allergens or none_word}")
        body = ", and ".join(clauses) if clauses else "let me tell you about it"
        text = f"{name}: {body}. You can scan the QR code to order!"
    return CharacterResponse(text, "happy", "point", True)


def _local_response(
    transcript: str, language: str, products: list[Product] | None = None
) -> CharacterResponse:
    """Useful multilingual demo response when the optional cloud LLM is offline.

    When a product catalog is provided, ingredient/spice/allergen questions about a
    named product are answered from the structured fields (Q&A grounding); price,
    ordering, and a general greeting are handled as before.
    """
    text = transcript.lower()
    lang = language if language in {"en", "ko", "ms"} else "en"

    if products:
        matched = _match_product(text, products)
        asks_ingredient = any(word in text for word in _INGREDIENT_WORDS)
        asks_spice = any(word in text for word in _SPICE_WORDS_Q)
        asks_allergen = any(word in text for word in _ALLERGEN_WORDS)
        if matched and (asks_ingredient or asks_spice or asks_allergen):
            return _local_product_answer(
                matched, lang, asks_spice, asks_ingredient, asks_allergen
            )

    prices = {"watermelon": 4, "mango": 5, "banana": 3, "apple": 3.5,
              "수박": 4, "망고": 5, "바나나": 3, "사과": 3.5,
              "tembikai": 4, "mangga": 5, "pisang": 3, "epal": 3.5}
    product = next(((name, price) for name, price in prices.items() if name in text), None)
    asks_price = any(word in text for word in ("price", "how much", "얼마", "가격", "berapa", "harga"))
    wants_order = any(word in text for word in ("order", "buy", "take one", "주문", "살게", "주세요", "pesan", "beli"))
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
