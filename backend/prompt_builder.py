"""Prompt_Builder: assembles the Gemini prompt from persona, store info, and turns.

Pure-function core (Req 3, Req 10.3/10.4). The :func:`build` function assembles a
single prompt string from the configured Persona, the current store/product
information, the recent Conversation_Turn history (clamped to the five most recent
in chronological order), and the customer transcript, together with the fixed
instructions that constrain the language model's output:

- return exactly one JSON object with a non-empty ``text`` field (<=500 chars), an
  ``emotion`` field from the allowed set, and a ``gesture`` field from the allowed
  set (Req 3.4),
- write at a grade-8 reading level using plain conversational vocabulary (Req 3.5),
- exclude all profanity (Req 10.3), and
- use polite, service-oriented vendor language (Req 10.4).

When no store information is available, the prompt still includes the Persona and
recent turns and instructs the model to state that no product information is
currently available rather than omitting a response (Req 3.3).
"""

from __future__ import annotations

from models import EMOTIONS, GESTURES, ConversationTurn, StoreInfo

# Maximum number of recent Conversation_Turns included in the prompt (Req 3.2).
MAX_RECENT_TURNS = 5

# Maximum length of the model's ``text`` field as instructed in the prompt (Req 3.4).
MAX_TEXT_CHARS = 500


def _format_allowed_set(values: frozenset[str]) -> str:
    """Return a deterministic, comma-separated rendering of an allowed value set."""
    return ", ".join(sorted(values))


def build(
    persona: str | None,
    store_info: StoreInfo | None,
    recent_turns: list[ConversationTurn],
    transcript: str,
) -> str:
    """Assemble the Gemini prompt for a single Conversation_Turn.

    Args:
        persona: The configurable merchant personality, or ``None`` when unset.
        store_info: The current store/product information, or ``None`` when absent.
        recent_turns: The Conversation_Turn history; clamped here to the five most
            recent turns in chronological order (Req 3.2).
        transcript: The customer's latest utterance transcript.

    Returns:
        A single prompt string containing the persona, store/product information,
        recent turns, customer transcript, and the fixed output instructions.
    """
    sections: list[str] = []

    # --- Persona ---------------------------------------------------------
    if persona is not None and persona.strip():
        sections.append(f"PERSONA:\n{persona}")
    else:
        sections.append(
            "PERSONA:\n"
            "You are a friendly street-market vendor character assisting a customer."
        )

    # --- Store / product information (Req 3.1, 3.3) ----------------------
    if store_info is not None:
        store_lines = [
            "STORE AND PRODUCT INFORMATION:",
            f"Store name: {store_info.store_name}",
            f"Products: {store_info.products}",
        ]
        sections.append("\n".join(store_lines))
    else:
        sections.append(
            "STORE AND PRODUCT INFORMATION:\n"
            "No store or product information is currently available. If the customer "
            "asks about products, politely state that no product information is "
            "currently available rather than omitting a response."
        )

    # --- Recent conversation history (Req 3.2) ---------------------------
    # Clamp to the most recent min(5, n) turns, preserving chronological order.
    clamped_turns = recent_turns[-MAX_RECENT_TURNS:]
    if clamped_turns:
        history_lines = ["RECENT CONVERSATION (oldest to newest):"]
        for turn in clamped_turns:
            history_lines.append(f"Customer: {turn.customer_text}")
            history_lines.append(f"Character: {turn.character_text}")
        sections.append("\n".join(history_lines))
    else:
        sections.append("RECENT CONVERSATION (oldest to newest):\n(no prior turns)")

    # --- Current customer transcript (Req 3.1) ---------------------------
    sections.append(f"CUSTOMER MESSAGE:\n{transcript}")

    # --- Fixed instructions (Req 3.4, 3.5, 10.3, 10.4) -------------------
    instructions = (
        "INSTRUCTIONS:\n"
        "- Respond with exactly one JSON object and nothing else.\n"
        "- The JSON object must contain exactly these fields:\n"
        f'  - "text": a non-empty string of at most {MAX_TEXT_CHARS} characters.\n'
        f'  - "emotion": exactly one of these lowercase values and nothing else: '
        f"{_format_allowed_set(EMOTIONS)}.\n"
        f'  - "gesture": exactly one of these lowercase values and nothing else: '
        f"{_format_allowed_set(GESTURES)}.\n"
        "- Reply in the same language the customer used in their message.\n"
        "- Keep the reply to 1-2 short, friendly sentences.\n"
        "- Write the text at a grade-8 reading level or below, using plain "
        "conversational vocabulary.\n"
        "- Do not include any profanity in the text.\n"
        "- Use polite, service-oriented language consistent with a market vendor "
        "addressing a customer."
    )
    sections.append(instructions)

    return "\n\n".join(sections)
