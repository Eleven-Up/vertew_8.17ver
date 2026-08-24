"""Tests for the structured menu-knowledge grounding used for product Q&A.

These cover the pure rendering (`prompt_builder.format_menu_knowledge` and
`spice_word`) that lets the assistant answer "what is in this?" / "how spicy is
it?" from real catalog data rather than guessing.
"""

from models import Product
from prompt_builder import format_menu_knowledge, spice_word


def _product(**overrides) -> Product:
    base = {
        "id": "mango",
        "store_id": "demo",
        "name": {"en": "Mango", "ko": "망고", "ms": "Mangga"},
        "description": {"en": "Sweet mango", "ko": "달콤한 망고", "ms": "Mangga manis"},
        "price_minor": 500,
        "currency": "MYR",
        "available": True,
        "image": "/images/mango.png",
        "spice_level": 2,
        "ingredients": {"en": "fresh mango, chili salt", "ko": "신선한 망고, 고추 소금"},
        "allergens": ("nuts",),
    }
    base.update(overrides)
    return Product(**base)


def test_spice_word_localized_and_clamped():
    assert spice_word(0, "en") == "not spicy"
    assert spice_word(3, "ko") == "많이 매움"
    assert spice_word(99, "en") == "hot"  # clamped into range
    assert spice_word(-5, "en") == "not spicy"  # clamped into range
    assert spice_word(2, "xx") == spice_word(2, "en")  # unknown language -> english


def test_menu_knowledge_includes_structured_fields_english():
    text = format_menu_knowledge([_product()], "en")
    assert "Mango" in text
    assert "MYR 5.00" in text
    assert "Spice: medium" in text
    assert "chili salt" in text
    assert "Allergens: nuts" in text


def test_menu_knowledge_localized_korean():
    text = format_menu_knowledge([_product()], "ko")
    assert "망고" in text
    assert "맵기: 보통 매움" in text
    assert "재료:" in text
    assert "고추 소금" in text


def test_menu_knowledge_marks_absent_allergens():
    text = format_menu_knowledge([_product(allergens=())], "en")
    assert "none declared" in text


def test_menu_knowledge_omits_unavailable_and_empty():
    assert format_menu_knowledge([_product(available=False)], "en") == ""
    assert format_menu_knowledge([], "en") == ""


def test_menu_knowledge_falls_back_to_english_labels_for_unknown_language():
    # Unknown language uses english labels but still renders the product.
    text = format_menu_knowledge([_product()], "xx")
    assert "Mango" in text
    assert "Spice:" in text
