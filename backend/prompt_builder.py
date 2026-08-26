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

from models import EMOTIONS, GESTURES, ConversationTurn, Product, QAEntry, StoreInfo

# Maximum number of recent Conversation_Turns included in the prompt (Req 3.2).
MAX_RECENT_TURNS = 5

# Maximum length of the model's ``text`` field as instructed in the prompt (Req 3.4).
MAX_TEXT_CHARS = 500

# ---------------------------------------------------------------------------
# Structured menu knowledge (grounding for product Q&A).
#
# ``format_menu_knowledge`` renders the structured product catalog into a labeled,
# localized text block that is injected into the prompt so the assistant can answer
# customer questions such as "what is in this?" or "how spicy is it?" from real
# data instead of guessing. Answer quality for those questions depends on this
# grounding far more than on model size, so the block is explicit and self-labeled.
# ---------------------------------------------------------------------------

# Language-independent 0..3 heat scale rendered into words per supported language.
_SPICE_WORDS: dict[str, dict[int, str]] = {
    "en": {0: "not spicy", 1: "mild", 2: "medium", 3: "hot"},
    "ko": {0: "안 매움", 1: "약간 매움", 2: "보통 매움", 3: "많이 매움"},
    "ms": {0: "tidak pedas", 1: "sedikit pedas", 2: "sederhana pedas", 3: "sangat pedas"},
}

# Section header and field labels per supported language.
_MENU_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "header": (
            "MENU (answer questions about the food using only this information; do "
            "not invent items, prices, ingredients, or spice levels):"
        ),
        "price": "Price",
        "spice": "Spice",
        "ingredients": "Ingredients",
        "allergens": "Allergens",
        "origin": "Origin",
        "none": "none declared",
    },
    "ko": {
        "header": (
            "메뉴 (음식에 대한 질문은 아래 정보로만 답하세요; 메뉴·가격·재료·맵기를 "
            "지어내지 마세요):"
        ),
        "price": "가격",
        "spice": "맵기",
        "ingredients": "재료",
        "allergens": "알레르기 유발 성분",
        "origin": "원산지",
        "none": "없음",
    },
    "ms": {
        "header": (
            "MENU (jawab soalan tentang makanan menggunakan maklumat ini sahaja; "
            "jangan reka item, harga, bahan, atau tahap kepedasan):"
        ),
        "price": "Harga",
        "spice": "Kepedasan",
        "ingredients": "Bahan",
        "allergens": "Alergen",
        "origin": "Asal",
        "none": "tiada",
    },
}


def spice_word(level: int, language: str = "en") -> str:
    """Render a 0..3 spice level into a localized word (clamped to range)."""
    lang = language if language in _SPICE_WORDS else "en"
    table = _SPICE_WORDS[lang]
    return table.get(max(0, min(3, level)), table[0])


def _localized(values: dict[str, str] | None, language: str) -> str:
    """Return the ``language`` string from a per-language mapping, falling back to
    English, then to any available value, then to the empty string."""
    if not values:
        return ""
    return values.get(language) or values.get("en") or next(iter(values.values()), "")


# Human-readable language names for the reply-language lock in the fixed
# instructions (see build()). The model is told the name explicitly instead of
# being left to (mis)infer the reply language from the transcript alone, which
# some providers get wrong even on plain, unambiguous input.
_LANGUAGE_NAMES: dict[str, str] = {"en": "English", "ko": "Korean", "ms": "Malay"}


# Curated Q&A block header per language (see format_qa_knowledge).
_QA_HEADER: dict[str, str] = {
    "en": (
        "KNOWN ANSWERS (prefer one of these when it answers the question, and put "
        "its id in matched_qa_id):"
    ),
    "ko": (
        "정해진 답변 (질문에 맞는 항목이 있으면 우선 사용하고, 그 id를 "
        "matched_qa_id에 넣으세요):"
    ),
    "ms": (
        "JAWAPAN SEDIA ADA (utamakan salah satu jika ia menjawab soalan, dan letak "
        "id-nya dalam matched_qa_id):"
    ),
}


def format_qa_knowledge(entries: list[QAEntry], language: str = "en") -> str:
    """Render approved Q&A entries into a labeled block for the prompt.

    Only ``approved`` entries are included, each tagged with its id so the model can
    report which curated answer it used (``matched_qa_id``). Returns an empty string
    when there are no approved entries.
    """
    lang = language if language in _QA_HEADER else "en"
    approved = [entry for entry in entries if entry.status == "approved"]
    if not approved:
        return ""
    lines: list[str] = [_QA_HEADER[lang]]
    for entry in approved:
        answer = _localized(entry.answer, lang)
        lines.append(f"[{entry.id}] Q: {entry.question} A: {answer}")
    return "\n".join(lines)


def format_menu_knowledge(products: list[Product], language: str = "en") -> str:
    """Render the available products into a localized menu-knowledge block.

    Only ``available`` products are included. Each line carries the catalog id in
    brackets (so the model can reference it in ``order_items``, mirroring how
    ``format_qa_knowledge`` brackets QA ids), the localized name and description,
    the price, the spice level in words, the ingredients, the allergens (or a
    "none declared" marker), and the origin when set (e.g. "Sarawak, Malaysia"),
    so the assistant can answer "where is this from?" without inventing it.
    Returns an empty string when there is nothing to describe so callers can
    omit the section entirely.

    The output is deterministic (products in catalog order, no timestamps), so it is
    safe to include in a cached prompt and straightforward to test.
    """
    lang = language if language in _MENU_LABELS else "en"
    labels = _MENU_LABELS[lang]

    available = [product for product in products if product.available]
    if not available:
        return ""

    lines: list[str] = [labels["header"]]
    for product in available:
        name = _localized(product.name, lang) or product.id
        description = _localized(product.description, lang)
        price = f"{product.currency} {product.price_minor / 100:.2f}"
        spice = spice_word(product.spice_level, lang)
        ingredients = _localized(product.ingredients, lang)
        allergens = ", ".join(product.allergens) if product.allergens else labels["none"]
        origin = _localized(product.origin, lang)

        detail = f'{labels["price"]}: {price}. {labels["spice"]}: {spice}.'
        if ingredients:
            detail += f' {labels["ingredients"]}: {ingredients}.'
        detail += f' {labels["allergens"]}: {allergens}.'
        if origin:
            detail += f' {labels["origin"]}: {origin}.'

        prefix = f"- [{product.id}] {name}"
        if description:
            prefix += f" — {description}."
        else:
            prefix += " —"
        lines.append(f"{prefix} {detail}")

    return "\n".join(lines)


def _format_allowed_set(values: frozenset[str]) -> str:
    """Return a deterministic, comma-separated rendering of an allowed value set."""
    return ", ".join(sorted(values))


def build(
    persona: str | None,
    store_info: StoreInfo | None,
    recent_turns: list[ConversationTurn],
    transcript: str,
    language: str = "en",
) -> str:
    """Assemble the Gemini prompt for a single Conversation_Turn.

    Args:
        persona: The configurable merchant personality, or ``None`` when unset.
        store_info: The current store/product information, or ``None`` when absent.
        recent_turns: The Conversation_Turn history; clamped here to the five most
            recent turns in chronological order (Req 3.2).
        transcript: The customer's latest utterance transcript.
        language: The customer session's current confirmed language (``"en"``,
            ``"ko"``, or ``"ms"``), tracked separately by Intent_Analyzer across
            turns (see intent.py). Passed through explicitly so the model is told
            which language to reply in, rather than having to (re-)infer it from
            this one transcript alone -- some providers drift into a different
            language mid-conversation on plain, unambiguous input when left to
            infer it themselves.

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
    language_name = _LANGUAGE_NAMES.get(language, "English")
    instructions = (
        "INSTRUCTIONS:\n"
        f"- The customer's conversation language is {language_name} ({language}). "
        f'Write the "text" field ONLY in {language_name}, in every reply, with no '
        "words from any other language mixed in -- even if the customer message "
        "above contains foreign words, a mangled or unclear phrase, or a brand/"
        "product name you don't recognize. If you are ever unsure what the "
        f"customer meant, ask for clarification, but still do this in {language_name} "
        "only.\n"
        "- Respond with exactly one JSON object and nothing else.\n"
        "- The JSON object must contain these fields:\n"
        f'  - "text": a non-empty string of at most {MAX_TEXT_CHARS} characters.\n'
        f'  - "emotion": exactly one of these lowercase values: '
        f"{_format_allowed_set(EMOTIONS)}.\n"
        f'  - "gesture": exactly one of these lowercase values: '
        f'{_format_allowed_set(GESTURES)}. Use "wave" for greetings, "point" '
        'when directing attention to a menu item, "nod" for simple agreement, '
        '"think" when unsure or considering, "jump" or "fly" for excited/'
        'celebratory moments (a delightful answer, an order just placed), and '
        '"approach" for a warm welcome. Default to "idle" otherwise.\n'
        "- The JSON object may also include:\n"
        '  - "action": either "answer" (default) or "call_owner".\n'
        '  - "matched_qa_id": the id of the KNOWN ANSWER you used (for example '
        '"qa_hours"), or null.\n'
        '  - "order_items": include this ONLY when the customer\'s message just now '
        "clearly asks to order one or more specific MENU items (with or without a "
        "quantity). An array of objects, each "
        '{"product_id": <the bracketed id from MENU, e.g. "nasi_goreng">, "quantity": '
        "<a positive integer; use 1 if no quantity was said>}. Only the item(s) "
        "ordered in THIS message, not earlier turns. Omit this field entirely (or "
        "use an empty array) when nothing was ordered in this message -- a question "
        "about a product is not an order.\n"
        '  - "confirm_payment": true ONLY when the customer\'s message just now is '
        "an explicit yes/confirmation that they want to pay or check out right now "
        "(e.g. \"yes, I'll pay\", \"결제할게요\", \"네 계산해주세요\", \"saya nak bayar\") "
        "-- never merely because they ordered or mentioned an item. Omit or set "
        "false otherwise.\n"
        "- The kiosk screen has no touchscreen, so the payment QR code only "
        "appears when you set \"confirm_payment\" to true -- never tell the "
        "customer to scan/look at a QR code otherwise. When you set \"order_items\" "
        "on a message that is not itself a payment confirmation, do not mention a "
        "QR code at all: recap the item(s) and ask whether they'd like to pay now. "
        "Only once the customer explicitly confirms (setting \"confirm_payment\" "
        "true) should the reply tell them the QR code is showing and to scan it.\n"
        "- Answer questions about the store using only the information above (the "
        "KNOWN ANSWERS and the MENU). When a KNOWN ANSWER fits the question, use it "
        "and set matched_qa_id to its id. When you answer from MENU facts, set "
        "matched_qa_id to null.\n"
        "- Never invent prices, ingredients, spice levels, or allergy/health "
        "information. If the information above does not cover the question, or the "
        "question is about an allergy or health concern you cannot confirm from it, "
        'set "action" to "call_owner" and tell the customer you will call the owner.\n'
        "- Keep the reply to 1-2 short, friendly sentences.\n"
        "- Write the text at a grade-8 reading level or below, using plain "
        "conversational vocabulary.\n"
        "- Do not include any profanity in the text.\n"
        "- Use polite, service-oriented language consistent with a market vendor "
        "addressing a customer."
    )
    sections.append(instructions)

    return "\n\n".join(sections)
