"""Tests for the structured menu-knowledge grounding used for product Q&A.

These cover the pure rendering (`prompt_builder.format_menu_knowledge` and
`spice_word`) that lets the assistant answer "what is in this?" / "how spicy is
it?" from real catalog data rather than guessing.
"""

from models import Product
from prompt_builder import format_menu_knowledge, spice_word


def _product(**overrides) -> Product:
    base = {
        "id": "nasi_goreng",
        "store_id": "demo",
        "name": {"en": "Chicken Fried Rice", "ko": "치킨 나시고랭", "ms": "Nasi Goreng Ayam"},
        "description": {"en": "Spicy fried rice", "ko": "매콤한 볶음밥", "ms": "Nasi goreng pedas"},
        "price_minor": 800,
        "currency": "MYR",
        "available": True,
        "image": "/images/nasi_goreng.png",
        "spice_level": 2,
        "ingredients": {"en": "rice, chicken, sambal chili", "ko": "밥, 닭고기, 삼발 고추"},
        "allergens": ("egg",),
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
    assert "Chicken Fried Rice" in text
    assert "MYR 8.00" in text
    assert "Spice: medium" in text
    assert "sambal chili" in text
    assert "Allergens: egg" in text


def test_menu_knowledge_localized_korean():
    text = format_menu_knowledge([_product()], "ko")
    assert "치킨 나시고랭" in text
    assert "맵기: 보통 매움" in text
    assert "재료:" in text
    assert "삼발 고추" in text


def test_menu_knowledge_marks_absent_allergens():
    text = format_menu_knowledge([_product(allergens=())], "en")
    assert "none declared" in text


def test_menu_knowledge_omits_unavailable_and_empty():
    assert format_menu_knowledge([_product(available=False)], "en") == ""
    assert format_menu_knowledge([], "en") == ""


def test_menu_knowledge_falls_back_to_english_labels_for_unknown_language():
    # Unknown language uses english labels but still renders the product.
    text = format_menu_knowledge([_product()], "xx")
    assert "Chicken Fried Rice" in text
    assert "Spice:" in text
