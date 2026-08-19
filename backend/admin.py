"""Admin_Interface: store/product/persona validation and /admin routes.

This module owns the field-level validation that guards every Admin_Interface
submission (Req 7.3, 7.4) and the ``/admin`` HTTP routes (an :class:`APIRouter`)
built on top of it. The routes serve the current store/product/persona info with
empty fields when none exist (Req 7.1, 7.2), accept submissions, persist them to
the shared :class:`~db.DataStore` so later prompts include the update (Req 7.6),
and on storage failure retain the entered values while showing a save error
(Req 7.5). ``main.py`` mounts the router and wires the real store via
:func:`set_data_store`.

Validation is a pure, deterministic function over the three editable fields
(``store_name``, ``products``, ``persona``). It accepts a submission only when the
required fields each contain 1..2000 characters and the optional persona, when
set, contains 1..500 characters; otherwise it rejects the submission while
naming the first invalid field so the route layer can surface an error
indication that identifies that field (Req 7.4). See design "Property 16:
Store-info validation accepts only well-formed fields".
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse

from db import DataStore, StorageError
from models import StoreInfo

# ---------------------------------------------------------------------------
# Field length constraints (single source of truth for Admin validation).
# Required text fields accept 1..2000 chars (Req 7.3); the optional persona,
# when set, accepts 1..500 chars (Req 7.7).
# ---------------------------------------------------------------------------
REQUIRED_FIELD_MIN_LEN: int = 1
REQUIRED_FIELD_MAX_LEN: int = 2000
PERSONA_MIN_LEN: int = 1
PERSONA_MAX_LEN: int = 500


@dataclass
class ValidationResult:
    """Outcome of validating an Admin_Interface store-info submission.

    ``ok`` is ``True`` only for a well-formed submission. When ``ok`` is ``True``,
    ``store_info`` carries the validated :class:`StoreInfo` ready to persist and
    ``invalid_field``/``message`` are ``None``. When ``ok`` is ``False``,
    ``invalid_field`` names the rejected field (``"store_name"``, ``"products"``,
    or ``"persona"``) and ``message`` describes why, enabling the ``/admin`` route
    to retain entered values and identify the invalid field (Req 7.4).
    """

    ok: bool
    store_info: StoreInfo | None = None
    invalid_field: str | None = None
    message: str | None = None


def _validate_required(field_name: str, value: str | None) -> str | None:
    """Return an error message when a required text field is invalid, else ``None``.

    A required field is invalid when it is missing, empty/whitespace-only, or
    longer than :data:`REQUIRED_FIELD_MAX_LEN` characters. Leading/trailing
    whitespace is ignored for the empty check and for the length check so that a
    field padded only with whitespace is treated as empty (Req 7.3).
    """
    if value is None:
        return f"{field_name} is required."
    trimmed = value.strip()
    if len(trimmed) < REQUIRED_FIELD_MIN_LEN:
        return f"{field_name} is required and must not be empty."
    if len(trimmed) > REQUIRED_FIELD_MAX_LEN:
        return f"{field_name} must be at most {REQUIRED_FIELD_MAX_LEN} characters."
    return None


def _validate_persona(value: str | None) -> str | None:
    """Return an error message when the optional persona is invalid, else ``None``.

    Persona is optional: a ``None`` value (persona not set) is always valid. When
    set, its trimmed length must be 1..:data:`PERSONA_MAX_LEN` characters; a
    whitespace-only value is treated as empty and rejected (Req 7.7).
    """
    if value is None:
        return None
    trimmed = value.strip()
    if len(trimmed) < PERSONA_MIN_LEN:
        return "persona must not be empty when set."
    if len(trimmed) > PERSONA_MAX_LEN:
        return f"persona must be at most {PERSONA_MAX_LEN} characters."
    return None


def validate_store_info(
    store_name: str | None,
    products: str | None,
    persona: str | None = None,
) -> ValidationResult:
    """Validate an Admin_Interface store-info submission (Req 7.3, 7.4).

    Accepts the submission only when ``store_name`` and ``products`` are each
    non-empty after trimming and at most 2000 characters, and ``persona`` (when
    set/non-``None``) is non-empty after trimming and at most 500 characters.

    On success, returns a :class:`ValidationResult` with ``ok=True`` and a
    :class:`StoreInfo` built from the trimmed field values (persona stays ``None``
    when not provided). On failure, returns ``ok=False`` with ``invalid_field`` set
    to the first offending field — checked in order ``store_name``, ``products``,
    ``persona`` — and a human-readable ``message`` (Req 7.4).
    """
    store_name_error = _validate_required("store_name", store_name)
    if store_name_error is not None:
        return ValidationResult(ok=False, invalid_field="store_name", message=store_name_error)

    products_error = _validate_required("products", products)
    if products_error is not None:
        return ValidationResult(ok=False, invalid_field="products", message=products_error)

    persona_error = _validate_persona(persona)
    if persona_error is not None:
        return ValidationResult(ok=False, invalid_field="persona", message=persona_error)

    normalized_persona = persona.strip() if persona is not None else None
    store_info = StoreInfo(
        store_name=store_name.strip(),  # type: ignore[union-attr]
        products=products.strip(),  # type: ignore[union-attr]
        persona=normalized_persona,
    )
    return ValidationResult(ok=True, store_info=store_info)


# ---------------------------------------------------------------------------
# /admin HTTP routes (Task 7.3, Req 7.1, 7.2, 7.5, 7.6)
#
# The routes server-render a self-contained store/product/persona form so the
# Admin_Interface works without a separate client bundle, and build directly on
# validate_store_info above. The form posts back to the same path; on success the
# values are persisted to the shared Data_Store (so the Prompt_Builder reads the
# updated info at turn time, Req 7.6) and a confirmation is shown; on validation
# or storage failure the entered values are retained and an error is shown
# (Req 7.4, 7.5).
# ---------------------------------------------------------------------------

# Form field names shared between the rendered HTML and the submission handler.
FIELD_STORE_NAME = "store_name"
FIELD_PRODUCTS = "products"
FIELD_PERSONA = "persona"


# The single DataStore the routes read from and write to. main.py wires the real
# repository at startup via :func:`set_data_store`; tests can inject a fake the
# same way or override the :func:`get_data_store` dependency.
_data_store: DataStore | None = None


def set_data_store(store: DataStore | None) -> None:
    """Register the shared :class:`DataStore` used by the ``/admin`` routes.

    main.py calls this at startup with the real repository so admin saves land in
    the same store the Conversation_Server reads from at turn time (Req 7.6).
    """
    global _data_store
    _data_store = store


def get_data_store() -> DataStore:
    """FastAPI dependency returning the shared :class:`DataStore`.

    Raises:
        RuntimeError: when no store has been registered yet (server misconfigured).
    """
    if _data_store is None:
        raise RuntimeError("DataStore has not been configured for the admin routes.")
    return _data_store


router = APIRouter()


@dataclass
class _AdminView:
    """View state used to render the admin page.

    Holds the values to show in each field (either the persisted info or the
    values just entered by the vendor) plus optional confirmation/error
    indications and the name of the field flagged as invalid (Req 7.4).
    """

    store_name: str = ""
    products: str = ""
    persona: str = ""
    confirmation: str | None = None
    error: str | None = None
    invalid_field: str | None = None


def _view_from_store(store_info: StoreInfo | None) -> _AdminView:
    """Build an :class:`_AdminView` from persisted info, or empty when absent.

    When no store info exists the fields are left empty so the Admin_Interface
    presents empty inputs (Req 7.2).
    """
    if store_info is None:
        return _AdminView()
    return _AdminView(
        store_name=store_info.store_name,
        products=store_info.products,
        persona=store_info.persona or "",
    )


def _render_admin_page(view: _AdminView) -> str:
    """Render the full admin HTML page for the given view state.

    All vendor-supplied values are HTML-escaped before interpolation so they are
    displayed as text rather than interpreted as markup.
    """
    store_name = html.escape(view.store_name)
    products = html.escape(view.products)
    persona = html.escape(view.persona)

    def _field_class(field: str) -> str:
        return ' class="invalid"' if view.invalid_field == field else ""

    banner = ""
    if view.confirmation is not None:
        banner = (
            f'<p id="confirmation" role="status" class="confirmation">'
            f"{html.escape(view.confirmation)}</p>"
        )
    elif view.error is not None:
        banner = (
            f'<p id="error" role="alert" class="error">'
            f"{html.escape(view.error)}</p>"
        )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Vertew Admin</title>
    <style>
      body {{ font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }}
      label {{ display: block; margin: 1rem 0 0.25rem; font-weight: 600; }}
      input, textarea {{ width: 100%; box-sizing: border-box; padding: 0.5rem; font: inherit; }}
      textarea {{ min-height: 6rem; }}
      .invalid {{ border: 2px solid #c0392b; }}
      .confirmation {{ color: #1e7e34; font-weight: 600; }}
      .error {{ color: #c0392b; font-weight: 600; }}
      button {{ margin-top: 1.25rem; padding: 0.6rem 1.2rem; font: inherit; }}
    </style>
  </head>
  <body>
    {_ADMIN_NAV}
    <main id="admin">
      <h1>Store &amp; product information</h1>
      {banner}
      <form id="admin-form" method="post" action="/admin">
        <label for="{FIELD_STORE_NAME}">Store name (required)</label>
        <input id="{FIELD_STORE_NAME}" name="{FIELD_STORE_NAME}" type="text"
               maxlength="{REQUIRED_FIELD_MAX_LEN}" value="{store_name}"{_field_class(FIELD_STORE_NAME)} />

        <label for="{FIELD_PRODUCTS}">Products (required)</label>
        <textarea id="{FIELD_PRODUCTS}" name="{FIELD_PRODUCTS}"
                  maxlength="{REQUIRED_FIELD_MAX_LEN}"{_field_class(FIELD_PRODUCTS)}>{products}</textarea>

        <label for="{FIELD_PERSONA}">Persona (optional)</label>
        <textarea id="{FIELD_PERSONA}" name="{FIELD_PERSONA}"
                  maxlength="{PERSONA_MAX_LEN}"{_field_class(FIELD_PERSONA)}>{persona}</textarea>

        <button type="submit">Save</button>
      </form>
    </main>
  </body>
</html>"""


@router.get("/admin", response_class=HTMLResponse)
def get_admin(store: DataStore = Depends(get_data_store)) -> HTMLResponse:
    """Serve the Admin_Interface with the current store/product info (Req 7.1, 7.2).

    Loads the persisted store info and renders the form pre-filled with it. When
    no info has been saved yet, the fields are presented empty (Req 7.2). If the
    load itself fails, the page is still served with empty fields and an error
    indication so the vendor can re-enter and save.
    """
    try:
        store_info = store.load_store_info()
        view = _view_from_store(store_info)
    except Exception:  # noqa: BLE001 - surface load failure without crashing the page
        view = _AdminView(error="Could not load saved information. You can re-enter and save it.")
    return HTMLResponse(content=_render_admin_page(view))


@router.post("/admin", response_class=HTMLResponse)
def post_admin(
    store_name: str = Form(default=""),
    products: str = Form(default=""),
    persona: str = Form(default=""),
    store: DataStore = Depends(get_data_store),
) -> HTMLResponse:
    """Accept an Admin_Interface submission, validate, and persist it.

    Validates via :func:`validate_store_info`. On invalid input, re-renders the
    form retaining the entered values and identifying the invalid field with a
    400 status (Req 7.4). On valid input, persists via
    :meth:`DataStore.upsert_store_info`; on success shows a confirmation (Req 7.3,
    and Req 7.6 since the shared store now holds the updated info), and on storage
    failure retains the entered values and shows a save-did-not-complete error with
    a 500 status (Req 7.5).
    """
    # The optional persona is treated as unset when left blank/whitespace-only.
    persona_value: str | None = persona if persona.strip() else None

    result = validate_store_info(store_name, products, persona_value)
    if not result.ok:
        view = _AdminView(
            store_name=store_name,
            products=products,
            persona=persona,
            error=result.message,
            invalid_field=result.invalid_field,
        )
        return HTMLResponse(content=_render_admin_page(view), status_code=400)

    assert result.store_info is not None  # guaranteed when ok is True
    try:
        store.upsert_store_info(result.store_info)
    except StorageError:
        view = _AdminView(
            store_name=store_name,
            products=products,
            persona=persona,
            error="Save did not complete. Your entries were kept; please try saving again.",
        )
        return HTMLResponse(content=_render_admin_page(view), status_code=500)

    # Re-render from the persisted values with a confirmation indication.
    view = _view_from_store(result.store_info)
    view.confirmation = "Store and product information saved."
    return HTMLResponse(content=_render_admin_page(view))


# ---------------------------------------------------------------------------
# Product editing + Q&A review pages (server-rendered, no client bundle).
#
# These extend the Admin_Interface so the vendor can (a) set the structured
# menu-knowledge fields that ground answers — spice level, ingredients, allergens
# — per product, and (b) review the pending Q&A entries the learning loop captured,
# approving (optionally editing) or rejecting each. Single-store MVP: routes accept
# a ``store_id`` query param defaulting to the demo store.
# ---------------------------------------------------------------------------

LANGS = ("en", "ko", "ms")
DEFAULT_STORE_ID = "demo"
SPICE_LABELS = {0: "0 - not spicy", 1: "1 - mild", 2: "2 - medium", 3: "3 - hot"}

_ADMIN_NAV = (
    '<nav><a href="/admin">Store info</a> '
    '<a href="/admin/products">Products</a> '
    '<a href="/admin/qa">Q&amp;A review</a></nav>'
)

_ADMIN_STYLE = """
<style>
  body { font-family: system-ui, sans-serif; max-width: 52rem; margin: 2rem auto; padding: 0 1rem; }
  nav a { margin-right: 1rem; }
  h1 { margin-bottom: .25rem; }
  .card { border: 1px solid #ccc; border-radius: 8px; padding: 1rem; margin: 1rem 0; }
  label { display: block; margin: .6rem 0 .2rem; font-weight: 600; }
  input, textarea, select { width: 100%; box-sizing: border-box; padding: .4rem; font: inherit; }
  textarea { min-height: 3rem; }
  .rowfields { display: flex; gap: .75rem; flex-wrap: wrap; }
  .rowfields > div { flex: 1; min-width: 8rem; }
  label.inline, input.inline { display: inline; width: auto; }
  .confirmation { color: #1e7e34; font-weight: 600; }
  .error { color: #c0392b; font-weight: 600; }
  .muted { color: #666; font-size: .9rem; }
  button { margin-top: .6rem; padding: .5rem 1rem; font: inherit; }
  .q { font-weight: 600; }
</style>
"""


def _banner(confirmation: str | None = None, error: str | None = None) -> str:
    if confirmation is not None:
        return f'<p id="confirmation" role="status" class="confirmation">{html.escape(confirmation)}</p>'
    if error is not None:
        return f'<p id="error" role="alert" class="error">{html.escape(error)}</p>'
    return ""


def _page(title: str, body: str, banner: str = "") -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>'
        f"<title>{html.escape(title)}</title>{_ADMIN_STYLE}</head>"
        f"<body>{_ADMIN_NAV}<main>{banner}{body}</main></body></html>"
    )


def _render_products_page(store: DataStore, store_id: str, banner: str = "") -> str:
    products = store.list_products(store_id)
    cards: list[str] = []
    if not products:
        cards.append("<p class='muted'>No products found for this store.</p>")
    for product in products:
        name = html.escape(product.name.get("en") or product.id)
        price = f"{product.currency} {product.price_minor / 100:.2f}"
        spice_opts = "".join(
            f'<option value="{level}"{" selected" if product.spice_level == level else ""}>'
            f"{html.escape(label)}</option>"
            for level, label in SPICE_LABELS.items()
        )
        ingredient_inputs = "".join(
            f"<div><label>Ingredients ({lang})</label>"
            f'<input name="ing_{lang}" maxlength="500" '
            f'value="{html.escape(product.ingredients.get(lang, ""))}"/></div>'
            for lang in LANGS
        )
        allergens = html.escape(", ".join(product.allergens))
        checked = " checked" if product.available else ""
        action = f"/admin/products/{html.escape(product.id)}?store_id={html.escape(store_id)}"
        cards.append(
            f'<form class="card" method="post" action="{action}">'
            f'<h3>{name} <span class="muted">- {price} - id={html.escape(product.id)}</span></h3>'
            f"<label>Spice level</label><select name=\"spice_level\">{spice_opts}</select>"
            f'<div class="rowfields">{ingredient_inputs}</div>'
            f"<label>Allergens (comma-separated, e.g. nuts, dairy)</label>"
            f'<input name="allergens" maxlength="300" value="{allergens}"/>'
            f'<label class="inline"><input class="inline" type="checkbox" name="available"{checked}/> Available</label>'
            f'<br/><button type="submit">Save "{name}"</button>'
            "</form>"
        )
    body = (
        "<h1>Products</h1>"
        "<p class='muted'>These fields ground the assistant's answers about "
        "ingredients, spice, and allergens.</p>" + "".join(cards)
    )
    return _page("Vertew Admin - Products", body, banner)


def _render_qa_page(store: DataStore, store_id: str, banner: str = "") -> str:
    pending = store.list_qa(store_id, status="pending")
    approved = store.list_qa(store_id, status="approved")

    pending_cards: list[str] = []
    if not pending:
        pending_cards.append(
            "<p class='muted'>No pending questions. Answers the assistant generates "
            "for uncovered questions will appear here for review.</p>"
        )
    for entry in pending:
        answer_inputs = "".join(
            f"<div><label>Answer ({lang})</label>"
            f'<textarea name="answer_{lang}">{html.escape(entry.answer.get(lang, ""))}</textarea></div>'
            for lang in LANGS
        )
        approve_action = f"/admin/qa/{html.escape(entry.id)}/approve?store_id={html.escape(store_id)}"
        reject_action = f"/admin/qa/{html.escape(entry.id)}/reject?store_id={html.escape(store_id)}"
        pending_cards.append(
            '<div class="card">'
            f'<p class="muted">source: {html.escape(entry.source)} - id={html.escape(entry.id)}</p>'
            f'<form method="post" action="{approve_action}">'
            f'<label>Question</label><input name="question" maxlength="500" value="{html.escape(entry.question)}"/>'
            f'<div class="rowfields">{answer_inputs}</div>'
            '<button type="submit">Approve</button></form>'
            f'<form method="post" action="{reject_action}"><button type="submit">Reject</button></form>'
            "</div>"
        )

    approved_items = "".join(
        f'<li><span class="q">{html.escape(entry.question)}</span> - '
        f'{html.escape(entry.answer.get("en") or next(iter(entry.answer.values()), ""))}</li>'
        for entry in approved
    )
    body = (
        "<h1>Q&amp;A review</h1>"
        "<h2>Pending</h2>" + "".join(pending_cards)
        + "<h2>Approved</h2><ul>"
        + (approved_items or "<li class='muted'>none</li>")
        + "</ul>"
    )
    return _page("Vertew Admin - Q&A", body, banner)


def _parse_allergens(raw: str) -> tuple[str, ...]:
    """Split a comma-separated allergen field into a normalized tuple of tags."""
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _collect_langs(values: dict[str, str]) -> dict[str, str]:
    """Keep only non-empty, trimmed per-language strings."""
    return {lang: text.strip() for lang, text in values.items() if text.strip()}


@router.get("/admin/products", response_class=HTMLResponse)
def get_admin_products(
    store_id: str = DEFAULT_STORE_ID, store: DataStore = Depends(get_data_store)
) -> HTMLResponse:
    """List the store's products with editable spice/ingredients/allergens fields."""
    return HTMLResponse(content=_render_products_page(store, store_id))


@router.post("/admin/products/{product_id}", response_class=HTMLResponse)
def post_admin_product(
    product_id: str,
    store_id: str = DEFAULT_STORE_ID,
    spice_level: str = Form(default="0"),
    ing_en: str = Form(default=""),
    ing_ko: str = Form(default=""),
    ing_ms: str = Form(default=""),
    allergens: str = Form(default=""),
    available: str | None = Form(default=None),
    store: DataStore = Depends(get_data_store),
) -> HTMLResponse:
    """Update one product's menu-knowledge fields and availability (Req 7.6:
    the Conversation_Server reads the updated catalog at turn time)."""
    try:
        level = int(spice_level)
    except (TypeError, ValueError):
        level = 0
    ingredients = _collect_langs({"en": ing_en, "ko": ing_ko, "ms": ing_ms})
    updated = store.update_product(
        store_id,
        product_id,
        spice_level=level,
        ingredients=ingredients,
        allergens=_parse_allergens(allergens),
        available=available is not None,
    )
    if updated is None:
        raise HTTPException(404, "Product not found")
    name = updated.name.get("en") or updated.id
    banner = _banner(confirmation=f'Saved "{name}".')
    return HTMLResponse(content=_render_products_page(store, store_id, banner))


@router.get("/admin/qa", response_class=HTMLResponse)
def get_admin_qa(
    store_id: str = DEFAULT_STORE_ID, store: DataStore = Depends(get_data_store)
) -> HTMLResponse:
    """Review pending Q&A (learned/generated) and see the approved knowledge base."""
    return HTMLResponse(content=_render_qa_page(store, store_id))


@router.post("/admin/qa/{qa_id}/approve", response_class=HTMLResponse)
def post_admin_qa_approve(
    qa_id: str,
    store_id: str = DEFAULT_STORE_ID,
    question: str = Form(default=""),
    answer_en: str = Form(default=""),
    answer_ko: str = Form(default=""),
    answer_ms: str = Form(default=""),
    store: DataStore = Depends(get_data_store),
) -> HTMLResponse:
    """Approve a pending Q&A entry (optionally editing its question/answer first) so
    it grounds future answers."""
    entry = store.get_qa_entry(qa_id)
    if entry is None:
        raise HTTPException(404, "Q&A entry not found")
    answer = _collect_langs({"en": answer_en, "ko": answer_ko, "ms": answer_ms})
    if not answer:
        banner = _banner(error="An answer in at least one language is required to approve.")
        return HTMLResponse(content=_render_qa_page(store, store_id, banner), status_code=400)
    store.update_qa(qa_id, question=(question.strip() or entry.question), answer=answer)
    store.approve_qa(qa_id)
    banner = _banner(confirmation="Approved and added to the knowledge base.")
    return HTMLResponse(content=_render_qa_page(store, store_id, banner))


@router.post("/admin/qa/{qa_id}/reject", response_class=HTMLResponse)
def post_admin_qa_reject(
    qa_id: str,
    store_id: str = DEFAULT_STORE_ID,
    store: DataStore = Depends(get_data_store),
) -> HTMLResponse:
    """Reject (archive) a pending Q&A entry so it is not used or shown again."""
    entry = store.get_qa_entry(qa_id)
    if entry is None:
        raise HTTPException(404, "Q&A entry not found")
    store.archive_qa(qa_id)
    banner = _banner(confirmation="Rejected.")
    return HTMLResponse(content=_render_qa_page(store, store_id, banner))
