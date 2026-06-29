"""LLM_Client: isolates the Gemini Flash HTTP call and response parsing.

This module owns the boundary to the Gemini Flash API and the pure parsing of its
responses into :class:`CharacterResponse` values. Parsing and the fallback
substitutions are deterministic and side-effect free so they can be exhaustively
validated with property-based tests; the network call (``complete``) is added in
Task 4.4 behind the same HTTP boundary.

Parsing rules (Req 4.2, 4.4):

- A well-formed JSON object with non-empty ``text``, ``emotion``, and ``gesture``
  fields yields a :class:`CharacterResponse` whose ``text`` is truncated to at most
  1000 characters and whose emotion/gesture are normalized to the supported sets.
- Anything else (invalid JSON, non-object JSON, or a missing/empty required field)
  yields :data:`FALLBACK_UNDERSTAND` (neutral/idle).

The network call (:func:`complete`) is isolated behind the :class:`GeminiClient`
HTTP boundary (Task 4.4) so it can be mocked in tests. ``complete`` makes exactly
one *logical* Gemini Flash call per conversation turn (Req 9.3 / Property 12),
applies a 10-second per-attempt timeout (Req 4.1, 9.4), and retries at most once on
failure or timeout (Req 9.6 / Property 11) — the retry *replaces* a failed attempt,
it is never a second successful call. When every attempt fails, it raises
:class:`GeminiUnavailableError`, which the caller (Task 8.1) translates into
:data:`FALLBACK_UNAVAILABLE`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Protocol

from models import (
    CharacterResponse,
    normalize_emotion,
    normalize_gesture,
)

logger = logging.getLogger("vertew.llm")

# Raw, unparsed payload returned by the Gemini Flash API (a JSON document). The
# HTTP boundary in Task 4.4 produces this; :func:`parse` consumes it.
RawResponse = str

# Maximum length of a parsed response text value (Req 4.2).
MAX_TEXT_LENGTH = 1000


# ---------------------------------------------------------------------------
# Fallback constants (see design.md "Fallback Constants" table).
# ---------------------------------------------------------------------------

# Substituted when a response is invalid JSON or is missing/empty a required
# field (Req 4.4). Carries the neutral expression and idle motion.
FALLBACK_UNDERSTAND: CharacterResponse = CharacterResponse(
    text="I'm sorry, I didn't quite catch that. Could you say it again?",
    emotion="neutral",
    gesture="idle",
    is_fallback=True,
)

# Returned by the Conversation_Server when the Gemini Flash API is unreachable,
# errors, or times out (Req 4.5). The network failure itself is detected by the
# caller (Task 4.4 / 8.1); this constant is the user-facing substitution.
FALLBACK_UNAVAILABLE: CharacterResponse = CharacterResponse(
    text="I'm sorry, I'm having trouble right now. Please try again in a moment.",
    emotion="neutral",
    gesture="idle",
    is_fallback=True,
)


def _is_non_empty_str(value: object) -> bool:
    """Return ``True`` when ``value`` is a string with at least one non-whitespace char."""
    return isinstance(value, str) and value.strip() != ""


def _strip_code_fence(raw: object) -> str | None:
    """Return the inner content of a markdown code fence, or ``None`` if absent.

    Handles ```` ```json ... ``` ```` and plain ```` ``` ... ``` ```` wrappers that
    some models emit around JSON. Returns ``None`` when ``raw`` is not a string or
    contains no fenced block.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text.startswith("```"):
        return None
    # Drop the opening fence line (``` or ```json) and the trailing fence.
    first_newline = text.find("\n")
    if first_newline == -1:
        return None
    body = text[first_newline + 1 :]
    closing = body.rfind("```")
    if closing != -1:
        body = body[:closing]
    return body.strip() or None


def parse(raw: RawResponse) -> CharacterResponse:
    """Parse a raw Gemini response into a :class:`CharacterResponse`.

    A valid response is a JSON object carrying non-empty ``text``, ``emotion``, and
    ``gesture`` fields. For such responses the text is truncated to at most
    :data:`MAX_TEXT_LENGTH` characters and the emotion/gesture are normalized to the
    supported sets (Req 4.2). Any malformed or incomplete response yields
    :data:`FALLBACK_UNDERSTAND` with emotion ``neutral`` and gesture ``idle``
    (Req 4.4).
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        # Some models wrap JSON in markdown code fences (```json ... ```); strip a
        # single fenced block and retry once before giving up.
        stripped = _strip_code_fence(raw)
        if stripped is None:
            return FALLBACK_UNDERSTAND
        try:
            data = json.loads(stripped)
        except (ValueError, TypeError):
            return FALLBACK_UNDERSTAND

    if not isinstance(data, dict):
        return FALLBACK_UNDERSTAND

    text = data.get("text")
    emotion = data.get("emotion")
    gesture = data.get("gesture")

    if not (
        _is_non_empty_str(text)
        and _is_non_empty_str(emotion)
        and _is_non_empty_str(gesture)
    ):
        return FALLBACK_UNDERSTAND

    return CharacterResponse(
        text=text[:MAX_TEXT_LENGTH],
        emotion=normalize_emotion(emotion),
        gesture=normalize_gesture(gesture),
        is_fallback=False,
    )


# ---------------------------------------------------------------------------
# Gemini Flash HTTP boundary (Task 4.4).
# ---------------------------------------------------------------------------

# Per-request network timeout in seconds. The spec budget is 10 s (Req 4.1, 9.4),
# but the LLM gateway can occasionally take longer, so this is configurable via the
# LLM_TIMEOUT_SECONDS environment variable and defaults to a more forgiving value.
def _timeout_from_env() -> float:
    try:
        return float(os.environ.get("LLM_TIMEOUT_SECONDS", "30"))
    except (TypeError, ValueError):
        return 30.0


GEMINI_TIMEOUT_SECONDS: float = _timeout_from_env()

# Maximum number of attempts per turn: the initial attempt plus at most one retry
# (Req 9.6 / Property 11). A retry replaces a failed attempt; it never represents a
# second successful Gemini call.
GEMINI_MAX_ATTEMPTS: int = 2

# Environment variable names (see backend/.env.example).
GEMINI_API_KEY_ENV: str = "GEMINI_API_KEY"
GEMINI_MODEL_ENV: str = "GEMINI_MODEL"

# Default Gemini Flash model used when GEMINI_MODEL is not configured.
DEFAULT_GEMINI_MODEL: str = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Provider selection.
#
# LLM_PROVIDER chooses which backend `complete` uses by default:
#   "gemini"   -> Google Gemini directly via google-genai (GeminiFlashClient)
#   "factchat" -> the Mindlogic CNU API Gateway (OpenAI-compatible) via
#                 OpenAICompatibleClient.
# ---------------------------------------------------------------------------
LLM_PROVIDER_ENV: str = "LLM_PROVIDER"
DEFAULT_LLM_PROVIDER: str = "gemini"

# Factchat / CNU API Gateway (OpenAI-compatible Chat Completions).
FACTCHAT_API_KEY_ENV: str = "FACTCHAT_API_KEY"
FACTCHAT_BASE_URL_ENV: str = "FACTCHAT_BASE_URL"
FACTCHAT_MODEL_ENV: str = "FACTCHAT_MODEL"
DEFAULT_FACTCHAT_BASE_URL: str = "https://factchat-cloud.mindlogic.ai/v1/gateway"
DEFAULT_FACTCHAT_MODEL: str = "solar-pro3"


class GeminiUnavailableError(RuntimeError):
    """Raised when every Gemini Flash attempt fails or times out.

    The Conversation_Server (Task 8.1) catches this and substitutes
    :data:`FALLBACK_UNAVAILABLE` (neutral/idle) so the customer never sees partial
    output (Req 4.5, 9.5).
    """


class GeminiClient(Protocol):
    """HTTP boundary to the Gemini Flash API.

    Each invocation of :meth:`generate` performs exactly one physical Gemini Flash
    request and returns its raw response payload (a JSON document for :func:`parse`).
    The protocol is intentionally minimal so tests can supply a mock that counts
    invocations to verify the single-call (Property 12) and at-most-one-retry
    (Property 11) semantics without touching the network.
    """

    async def generate(self, prompt: str) -> RawResponse:
        """Issue a single Gemini Flash call for ``prompt`` and return its raw text."""
        ...


class GeminiFlashClient:
    """Concrete :class:`GeminiClient` backed by the ``google-genai`` library.

    The API key is read from the ``GEMINI_API_KEY`` environment variable and the
    model from ``GEMINI_MODEL`` (defaulting to :data:`DEFAULT_GEMINI_MODEL`). The
    ``google-genai`` package is imported lazily inside :meth:`generate` so this
    module (and its pure parsing logic) imports cleanly in environments where the
    network dependency is not installed.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get(GEMINI_API_KEY_ENV)
        self._model = model or os.environ.get(GEMINI_MODEL_ENV) or DEFAULT_GEMINI_MODEL
        self._client = None  # lazily constructed on first use

    def _ensure_client(self):
        """Construct the underlying google-genai async client on first use."""
        if self._client is None:
            if not self._api_key:
                raise GeminiUnavailableError(
                    f"{GEMINI_API_KEY_ENV} is not configured"
                )
            # Lazy import keeps the dependency optional for pure-parsing imports.
            from google import genai  # type: ignore[import-not-found]

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def generate(self, prompt: str) -> RawResponse:
        """Perform exactly one Gemini Flash call and return its raw text payload."""
        client = self._ensure_client()
        # Lazy import of the config type alongside the client (optional dependency).
        from google.genai import types  # type: ignore[import-not-found]

        response = await client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            # Force a raw JSON object response so parse() does not have to peel
            # markdown code fences off the model output.
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        return response.text


class OpenAICompatibleClient:
    """:class:`GeminiClient` for any OpenAI-compatible Chat Completions endpoint.

    Used for the Mindlogic CNU API Gateway ("factchat"), which exposes OpenAI,
    Anthropic, Google, xAI and others behind one base URL and one key. A single
    POST to ``{base_url}/chat/completions/`` is issued per turn; the assistant
    message content is returned verbatim (Gateway responses commonly wrap JSON in
    markdown code fences, which :func:`parse` strips). ``httpx`` is imported lazily
    so the module imports cleanly when the dependency is absent.

    Auth uses the ``Authorization: Bearer <key>`` header (OpenAI style).
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self._api_key = (
            api_key if api_key is not None else os.environ.get(FACTCHAT_API_KEY_ENV)
        )
        self._model = model or os.environ.get(FACTCHAT_MODEL_ENV) or DEFAULT_FACTCHAT_MODEL
        self._base_url = (
            base_url
            or os.environ.get(FACTCHAT_BASE_URL_ENV)
            or DEFAULT_FACTCHAT_BASE_URL
        ).rstrip("/")
        # Reused across calls so subsequent requests skip the TLS/connection setup
        # cost; created lazily on first use inside the event loop.
        self._http = None  # type: ignore[var-annotated]

    async def generate(self, prompt: str) -> RawResponse:
        """Issue one chat-completion request and return the assistant message text."""
        if not self._api_key:
            raise GeminiUnavailableError(f"{FACTCHAT_API_KEY_ENV} is not configured")

        # Lazy import keeps httpx optional for pure-parsing imports.
        import httpx  # type: ignore[import-not-found]

        if self._http is None:
            # Keep-alive connection pool reused for the process lifetime.
            self._http = httpx.AsyncClient(timeout=GEMINI_TIMEOUT_SECONDS + 5)

        url = f"{self._base_url}/chat/completions/"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            # Cap generation so worst-case latency stays bounded (Req 4.2 keeps the
            # text short anyway); the prompt also asks for 1-2 short sentences.
            "max_tokens": 300,
        }

        response = await self._http.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        return data["choices"][0]["message"]["content"]


# A lazily created default client shared across calls when none is injected.
_default_client: GeminiClient | None = None


def _get_default_client() -> GeminiClient:
    """Return the process-wide default LLM client, selected by ``LLM_PROVIDER``.

    ``factchat`` uses the OpenAI-compatible CNU API Gateway; anything else (the
    default) uses Google Gemini directly. Created once and reused.
    """
    global _default_client
    if _default_client is None:
        provider = (
            os.environ.get(LLM_PROVIDER_ENV) or DEFAULT_LLM_PROVIDER
        ).strip().lower()
        if provider == "factchat":
            logger.info("LLM provider: factchat (CNU API Gateway)")
            _default_client = OpenAICompatibleClient()
        else:
            logger.info("LLM provider: gemini (google-genai)")
            _default_client = GeminiFlashClient()
    return _default_client


async def complete(prompt: str, client: GeminiClient | None = None) -> RawResponse:
    """Send ``prompt`` to Gemini Flash and return the raw response (Req 4.1, 9.3, 9.6).

    This is the single logical Gemini call for a conversation turn: on the success
    path the HTTP boundary (:meth:`GeminiClient.generate`) is invoked exactly once
    (Property 12). Each attempt is bounded by a :data:`GEMINI_TIMEOUT_SECONDS`
    timeout; if it fails or times out, the call is retried at most once
    (:data:`GEMINI_MAX_ATTEMPTS` total attempts, Property 11). The retry replaces a
    failed attempt rather than adding a second successful call.

    When every attempt fails, raises :class:`GeminiUnavailableError`; the caller
    (Task 8.1) translates that into :data:`FALLBACK_UNAVAILABLE`.

    Args:
        prompt: The fully assembled prompt produced by the Prompt_Builder.
        client: Optional HTTP boundary to use; defaults to the shared
            :class:`GeminiFlashClient`. Tests inject a mock here to count attempts.
    """
    active_client = client if client is not None else _get_default_client()

    last_error: Exception | None = None
    for _ in range(GEMINI_MAX_ATTEMPTS):
        try:
            return await asyncio.wait_for(
                active_client.generate(prompt),
                timeout=GEMINI_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised as GeminiUnavailableError
            # Both network/HTTP errors and asyncio timeouts are treated as a failed
            # attempt that the retry may replace. Log the real cause so operators can
            # see why a turn fell back to "temporarily unavailable".
            last_error = exc
            logger.warning("Gemini attempt failed: %r", exc)

    logger.error(
        "Gemini Flash call failed after %d attempts: %r",
        GEMINI_MAX_ATTEMPTS,
        last_error,
    )
    raise GeminiUnavailableError(
        f"Gemini Flash call failed after {GEMINI_MAX_ATTEMPTS} attempts"
    ) from last_error
