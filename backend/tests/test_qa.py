"""Tests for the curated Q&A knowledge base, escalation, and the learning loop.

Cover the QA table (seed / list / pending / approve), the QA prompt block, the
extended LLM response contract (action / matched_qa_id), and the orchestration:
curated hits are not re-learned, novel generated answers become pending Q&A, and
"call_owner" escalates without being learned.
"""

import asyncio

import llm
from conversation import ConversationSession, handle_transcript
from db import DataStore
from prompt_builder import format_qa_knowledge


class _CapturingClient:
    """Fake GeminiClient returning a fixed raw reply and recording the prompt."""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.prompt: str | None = None

    async def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return self.raw


def _seeded_store() -> DataStore:
    store = DataStore(":memory:")
    store.seed_demo_data()
    return store


# --- Data_Store -------------------------------------------------------------

def test_seed_creates_approved_qa_entries():
    store = _seeded_store()
    try:
        entries = {e.id: e for e in store.list_qa("demo")}
        assert "qa_payment" in entries
        assert entries["qa_payment"].status == "approved"
        assert entries["qa_hours"].answer.get("ko")
    finally:
        store.close()


def test_pending_qa_is_hidden_until_approved():
    store = _seeded_store()
    try:
        created = store.add_pending_qa(
            "demo", "Do you deliver?", {"en": "No, pickup only."}, source="generated"
        )
        assert created.status == "pending"
        approved_ids = {e.id for e in store.list_qa("demo", status="approved")}
        assert created.id not in approved_ids  # not used for grounding yet
        assert created.id in {e.id for e in store.list_qa("demo", status="pending")}

        assert store.approve_qa(created.id) is True
        assert created.id in {e.id for e in store.list_qa("demo", status="approved")}
        assert store.approve_qa("nope") is False
    finally:
        store.close()


# --- Prompt block -----------------------------------------------------------

def test_format_qa_knowledge_lists_ids_and_localized_answers():
    store = _seeded_store()
    try:
        block = format_qa_knowledge(store.list_qa("demo"), "ko")
        assert "[qa_payment]" in block
        assert "QR" in block  # korean payment answer mentions QR
        # A pending entry must not appear in the grounding block.
        store.add_pending_qa("demo", "secret?", {"en": "hidden"}, source="generated")
        block2 = format_qa_knowledge(store.list_qa("demo", status=None), "en")
        assert "hidden" not in block2
    finally:
        store.close()


# --- LLM response contract --------------------------------------------------

def test_parse_reads_action_and_matched_qa_id():
    resp = llm.parse(
        '{"text": "We open at 6pm.", "emotion": "happy", "gesture": "nod", '
        '"matched_qa_id": "qa_hours"}'
    )
    assert resp.action == "answer"
    assert resp.matched_qa_id == "qa_hours"

    esc = llm.parse(
        '{"text": "Let me call the owner.", "emotion": "neutral", '
        '"gesture": "idle", "action": "call_owner"}'
    )
    assert esc.action == "call_owner"
    assert esc.matched_qa_id is None


def test_parse_defaults_for_missing_or_invalid_action():
    resp = llm.parse(
        '{"text": "Hi!", "emotion": "happy", "gesture": "wave", '
        '"action": "dance", "matched_qa_id": ""}'
    )
    assert resp.action == "answer"  # invalid action falls back
    assert resp.matched_qa_id is None  # empty string -> None


# --- Orchestration ----------------------------------------------------------

def _run(store, client, transcript, language="en"):
    return asyncio.run(
        handle_transcript(
            transcript,
            data_store=store,
            session=ConversationSession(),
            llm_client=client,
            language=language,
            store_id="demo",
        )
    )


def test_prompt_includes_curated_qa_block():
    store = _seeded_store()
    client = _CapturingClient(
        '{"text": "We open at 6pm.", "emotion": "happy", "gesture": "nod", '
        '"matched_qa_id": "qa_hours"}'
    )
    try:
        _run(store, client, "What time do you open?")
        assert "KNOWN ANSWERS" in client.prompt
        assert "qa_hours" in client.prompt
    finally:
        store.close()


def test_curated_hit_is_not_relearned():
    store = _seeded_store()
    client = _CapturingClient(
        '{"text": "We open at 6pm.", "emotion": "happy", "gesture": "nod", '
        '"matched_qa_id": "qa_hours"}'
    )
    try:
        _run(store, client, "What time do you open?")
        # matched a curated entry -> nothing new captured
        assert store.list_qa("demo", status="pending") == []
    finally:
        store.close()


def test_generated_answer_becomes_pending_qa():
    store = _seeded_store()
    client = _CapturingClient(
        '{"text": "Yes, we have rendang today.", "emotion": "happy", '
        '"gesture": "nod"}'  # no matched_qa_id -> generated
    )
    try:
        response = _run(store, client, "Do you have rendang?")
        assert response.matched_qa_id is None
        pending = store.list_qa("demo", status="pending")
        assert len(pending) == 1
        assert pending[0].question == "Do you have rendang?"
        assert pending[0].source == "generated"
    finally:
        store.close()


def test_escalation_sets_action_and_is_not_learned():
    store = _seeded_store()
    client = _CapturingClient(
        '{"text": "Let me call the owner for you.", "emotion": "neutral", '
        '"gesture": "idle", "action": "call_owner"}'
    )
    try:
        response = _run(store, client, "Is this safe for a severe peanut allergy?")
        assert response.action == "call_owner"
        assert store.list_qa("demo", status="pending") == []  # escalations aren't learned
    finally:
        store.close()


def test_greeting_is_not_learned():
    store = _seeded_store()
    client = _CapturingClient(
        '{"text": "Hello, welcome!", "emotion": "happy", "gesture": "wave"}'
    )
    try:
        _run(store, client, "hello there")
        assert store.list_qa("demo", status="pending") == []  # not a question
    finally:
        store.close()
