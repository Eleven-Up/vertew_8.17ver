"""ProfanityFilter: last content-safety gate before responses reach the Kiosk_UI.

This module guarantees that the Kiosk_UI never receives flagged text regardless of
the model output (Req 10.1). It is a pure, deterministic function over
:class:`CharacterResponse`: when the response text contains one or more configured
profanity terms it is replaced wholesale with :data:`FALLBACK_COURTEOUS` (which is
itself free of any configured term); otherwise the response passes through unchanged
(Req 10.2).
"""

from __future__ import annotations

from models import CharacterResponse

# ---------------------------------------------------------------------------
# Configured profanity term list.
#
# Module-level and configurable: callers/tests may extend or replace
# ``PROFANITY_TERMS`` to suit a deployment's content policy. Terms are matched
# case-insensitively as substrings of the response text (Req 10.1). The default
# set uses neutral placeholder terms rather than real slurs; real deployments are
# expected to supply their own list.
# ---------------------------------------------------------------------------
PROFANITY_TERMS: list[str] = [
    "badword",
    "darn",
    "heck",
    "shoot",
    "blast",
]

# Courteous, profanity-free fallback substituted whenever a flagged term is found
# (Req 10.1). Its text is intentionally simple and contains none of the configured
# terms above so that re-filtering it would be a no-op.
FALLBACK_COURTEOUS = CharacterResponse(
    text="I'm sorry, but I can't help with that. Is there something else I can do for you?",
    emotion="neutral",
    gesture="idle",
    is_fallback=True,
)


def _contains_profanity(text: str) -> bool:
    """Return ``True`` when ``text`` contains any configured profanity term.

    Matching is case-insensitive and treats each term as a substring, so a term
    embedded at the start, middle, or end of the text (or within another word) is
    detected.
    """
    lowered = text.lower()
    return any(term.lower() in lowered for term in PROFANITY_TERMS)


def apply(resp: CharacterResponse) -> CharacterResponse:
    """Replace flagged responses with a courteous fallback, else pass through.

    If ``resp.text`` contains one or more configured profanity terms, return
    :data:`FALLBACK_COURTEOUS` (which itself contains no flagged terms, Req 10.1);
    otherwise return ``resp`` unchanged (Req 10.2).
    """
    if _contains_profanity(resp.text):
        return FALLBACK_COURTEOUS
    return resp
