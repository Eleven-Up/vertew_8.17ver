"""QA retrieval: select the most relevant curated Q&A entries for a question.

As the curated Q&A table grows, injecting every approved entry into the prompt
stops scaling. This module ranks entries by similarity to the customer question
and returns only the top matches, so the prompt stays small and focused.

Two embedders are provided behind a common interface:

- :class:`HashingEmbedder` (default): a zero-dependency, deterministic lexical
  embedder (word + character-trigram hashing). It runs anywhere with no model
  download, is great for same-language matching, and is fully testable offline.
  It does NOT do cross-lingual *semantic* matching (a Korean question will not
  lexically match an English stored question).
- :class:`FastEmbedEmbedder` (opt-in via ``QA_EMBEDDER=fastembed``): real
  multilingual semantic embeddings via the lightweight ONNX ``fastembed`` package
  (no PyTorch), suitable for a Raspberry Pi. Enables cross-lingual retrieval.
  ``fastembed`` is imported lazily so it is only required when selected.

Selection is deterministic and side-effect free (no timestamps/RNG), so it is
safe to unit-test and to run inside a cached prompt build.
"""

from __future__ import annotations

import math
import os
import re
import zlib
from typing import Protocol

from models import QAEntry

# Dimensionality of the hashing embedder's vectors. Small is fine for short FAQ
# text and keeps the per-turn cost negligible.
_HASHING_DIM = 256

# Default number of QA entries injected into the prompt (top-k by relevance).
QA_TOP_K: int = 6

# Default multilingual model used when QA_EMBEDDER=fastembed (overridable via
# QA_EMBED_MODEL). A small multilingual model keeps it Raspberry-Pi-viable.
DEFAULT_EMBED_MODEL: str = "intfloat/multilingual-e5-small"

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class Embedder(Protocol):
    """Turns a list of texts into a list of equal-length numeric vectors."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


def _tokens(text: str) -> list[str]:
    """Word tokens plus character trigrams (trigrams help Korean/Malay matching)."""
    lowered = text.lower().strip()
    words = _TOKEN_RE.findall(lowered)
    trigrams = [lowered[i : i + 3] for i in range(len(lowered) - 2)]
    return words + (trigrams if trigrams else [lowered] if lowered else [])


class HashingEmbedder:
    """Deterministic, dependency-free lexical embedder (feature hashing)."""

    def __init__(self, dim: int = _HASHING_DIM) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        for token in _tokens(text):
            digest = zlib.crc32(token.encode("utf-8"))
            index = digest % self._dim
            sign = 1.0 if (digest >> 8) & 1 else -1.0
            vec[index] += sign
        norm = math.sqrt(sum(value * value for value in vec))
        if norm == 0.0:
            return vec
        return [value / norm for value in vec]


class FastEmbedEmbedder:
    """Multilingual semantic embedder backed by the optional ``fastembed`` (ONNX)
    package. Imported lazily so the dependency is only needed when selected."""

    def __init__(self, model_name: str = DEFAULT_EMBED_MODEL) -> None:
        self._model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from fastembed import TextEmbedding  # type: ignore[import-not-found]

            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        return [list(vector) for vector in model.embed(list(texts))]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """Return the process-wide embedder chosen by ``QA_EMBEDDER`` (cached).

    ``fastembed`` selects the semantic ONNX backend; anything else (default) uses
    the zero-dependency hashing embedder.
    """
    global _embedder
    if _embedder is None:
        kind = (os.environ.get("QA_EMBEDDER") or "hashing").strip().lower()
        if kind == "fastembed":
            _embedder = FastEmbedEmbedder(
                os.environ.get("QA_EMBED_MODEL") or DEFAULT_EMBED_MODEL
            )
        else:
            _embedder = HashingEmbedder()
    return _embedder


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for equal-length vectors (0.0 when either is a zero vector)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _entry_text(entry: QAEntry) -> str:
    """The text an entry is matched on: its question plus any alias phrasings."""
    return " ".join([entry.question, *entry.aliases]).strip()


def select_relevant_qa(
    question: str,
    entries: list[QAEntry],
    *,
    embedder: Embedder | None = None,
    k: int = QA_TOP_K,
    min_score: float = 0.0,
) -> list[QAEntry]:
    """Return the ``k`` entries most similar to ``question``, most relevant first.

    Entries scoring at or above ``min_score`` are preferred; if none clear the
    threshold, the top ``k`` are still returned so the model always has candidates
    to consider (it can still escalate). With ``k`` >= ``len(entries)`` this simply
    orders every entry by relevance — a safe no-op for small tables.
    """
    if not entries:
        return []
    embedder = embedder or get_embedder()
    vectors = embedder.embed([question, *(_entry_text(entry) for entry in entries)])
    question_vec, entry_vecs = vectors[0], vectors[1:]
    scored = sorted(
        zip(entries, (_cosine(question_vec, vec) for vec in entry_vecs)),
        key=lambda pair: pair[1],
        reverse=True,
    )
    top = scored[:k]
    above = [entry for entry, score in top if score >= min_score]
    return above or [entry for entry, _ in top]
