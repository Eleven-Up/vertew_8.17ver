"""Integration tests for catalog-grounded menu Q&A.

Cover three things end to end against a seeded in-memory store:
- the structured product fields round-trip through the Data_Store,
- ``handle_transcript`` injects the localized menu-knowledge block into the prompt
  when a store_id with products is given (and leaves the prompt unchanged without),
- the offline fallback answers ingredient/spice questions from the catalog.
"""

import asyncio

from conversation import ConversationSession, _local_response, handle_transcript
from db import DataStore
from models import CharacterResponse


class _CapturingClient:
    """Fake GeminiClient that records the prompt and returns a fixed valid reply."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.prompt: str | None = None

    async def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return self.raw


_VALID_REPLY = '{"text": "Sure!", "emotion": "happy", "gesture": "nod"}'


def _seeded_store() -> DataStore:
    store = DataStore(":memory:")
    store.seed_demo_data()
    return store


def test_seeded_products_have_structured_fields():
    store = _seeded_store()
    try:
        products = {p.id: p for p in store.list_products("demo")}
        mango = products["mango"]
        assert mango.spice_level == 0
        assert mango.ingredients.get("ko")  # localized ingredients present
        assert isinstance(mango.allergens, tuple)
    finally:
        store.close()


def test_prompt_includes_menu_knowledge_when_store_id_given():
    store = _seeded_store()
    client = _CapturingClient(_VALID_REPLY)
    try:
        response = asyncio.run(
            handle_transcript(
                "What is in the mango, and is it spicy?",
                data_store=store,
                session=ConversationSession(),
                llm_client=client,
                language="en",
                store_id="demo",
            )
        )
        assert isinstance(response, CharacterResponse)
        assert client.prompt is not None
        assert "MENU" in client.prompt
        assert "Mango" in client.prompt
        assert "Spice:" in client.prompt
        assert "Ingredients:" in client.prompt
    finally:
        store.close()


def test_prompt_menu_knowledge_localized_to_customer_language():
    store = _seeded_store()
    client = _CapturingClient(_VALID_REPLY)
    try:
        asyncio.run(
            handle_transcript(
                "망고에 뭐가 들어가요?",
                data_store=store,
                session=ConversationSession(),
                llm_client=client,
                language="ko",
                store_id="demo",
            )
        )
        assert "망고" in client.prompt
        assert "맵기:" in client.prompt
    finally:
        store.close()


def test_prompt_unchanged_without_store_id():
    store = _seeded_store()
    client = _CapturingClient(_VALID_REPLY)
    try:
        asyncio.run(
            handle_transcript(
                "hello",
                data_store=store,
                session=ConversationSession(),
                llm_client=client,
                language="en",
            )
        )
        # No store_id -> no structured menu block injected (behavior preserved).
        assert "MENU (" not in client.prompt
    finally:
        store.close()


def test_local_response_answers_ingredient_question_from_catalog():
    store = _seeded_store()
    try:
        products = store.list_products("demo")
        response = _local_response("what's in the mango?", "en", products)
        assert response.is_fallback
        assert "mango" in response.text.lower()
    finally:
        store.close()


def test_local_response_spice_question_korean_from_catalog():
    store = _seeded_store()
    try:
        products = store.list_products("demo")
        response = _local_response("망고 매워요?", "ko", products)
        assert "망고" in response.text
        assert "안 매움" in response.text  # demo fruit is spice_level 0
    finally:
        store.close()


def test_local_response_backward_compatible_without_catalog():
    # Existing two-argument behavior is unchanged (see test_local_response.py).
    assert "RM 5" in _local_response("망고 가격이 얼마예요?", "ko").text
    assert "QR" in _local_response("I want to order", "en").text
