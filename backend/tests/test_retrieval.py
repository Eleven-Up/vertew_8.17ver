"""Tests for QA retrieval (top-k relevance selection and the embedder factory)."""

import retrieval
from models import QAEntry
from retrieval import (
    FastEmbedEmbedder,
    HashingEmbedder,
    get_embedder,
    select_relevant_qa,
)


def _qa(qa_id: str, question: str, aliases: tuple[str, ...] = ()) -> QAEntry:
    return QAEntry(
        id=qa_id,
        store_id="demo",
        question=question,
        answer={"en": f"answer for {qa_id}"},
        aliases=aliases,
    )


# --- hashing embedder -------------------------------------------------------

def test_hashing_embedder_is_normalized_and_deterministic():
    emb = HashingEmbedder()
    (v1,) = emb.embed(["how do I pay?"])
    (v2,) = emb.embed(["how do I pay?"])
    assert v1 == v2  # deterministic
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-9  # unit length

    (empty,) = emb.embed([""])
    assert all(x == 0.0 for x in empty)  # empty text -> zero vector (no crash)


# --- selection --------------------------------------------------------------

def test_select_ranks_lexically_closest_first():
    entries = [
        _qa("qa_hours", "What time are you open?"),
        _qa("qa_pay", "How can I pay for my order?"),
        _qa("qa_halal", "Is the food halal?"),
    ]
    result = select_relevant_qa(
        "how do I pay?", entries, embedder=HashingEmbedder(), k=3
    )
    assert result[0].id == "qa_pay"  # most relevant surfaces first


def test_select_limits_to_k():
    entries = [_qa(f"qa_{i}", f"question number {i}") for i in range(10)]
    result = select_relevant_qa("question", entries, embedder=HashingEmbedder(), k=4)
    assert len(result) == 4


def test_select_empty_returns_empty():
    assert select_relevant_qa("anything", [], embedder=HashingEmbedder()) == []


def test_select_small_table_returns_all_entries():
    entries = [_qa("qa_a", "alpha"), _qa("qa_b", "beta")]
    result = select_relevant_qa("gamma", entries, embedder=HashingEmbedder(), k=6)
    assert {e.id for e in result} == {"qa_a", "qa_b"}  # k >= size -> all, reordered


def test_aliases_help_matching():
    entries = [
        _qa("qa_hours", "What time are you open?"),
        _qa("qa_pay", "Payment", aliases=("how do I pay", "can I use a card")),
    ]
    result = select_relevant_qa(
        "can I use a card?", entries, embedder=HashingEmbedder(), k=2
    )
    assert result[0].id == "qa_pay"


# --- embedder factory -------------------------------------------------------

def test_get_embedder_defaults_to_hashing(monkeypatch):
    monkeypatch.delenv("QA_EMBEDDER", raising=False)
    retrieval._embedder = None
    try:
        assert isinstance(get_embedder(), HashingEmbedder)
    finally:
        retrieval._embedder = None


def test_get_embedder_selects_fastembed_without_importing(monkeypatch):
    # Selecting fastembed must not import the package until embed() is called.
    monkeypatch.setenv("QA_EMBEDDER", "fastembed")
    retrieval._embedder = None
    try:
        assert isinstance(get_embedder(), FastEmbedEmbedder)
    finally:
        retrieval._embedder = None
