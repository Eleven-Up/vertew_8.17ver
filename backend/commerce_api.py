"""MVP menu, customer-session, and order REST API."""

import re
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db import DataStore
from models import LanguageSource, OrderStatus
from realtime import manager

router = APIRouter(prefix="/api", tags=["commerce"])
_data_store: DataStore | None = None


def set_data_store(store: DataStore | None) -> None:
    global _data_store
    _data_store = store


def get_data_store() -> DataStore:
    if _data_store is None:
        raise RuntimeError("DataStore has not been configured for commerce routes.")
    return _data_store


class SessionCreate(BaseModel):
    store_id: str = Field(min_length=1, max_length=100)


class LanguageUpdate(BaseModel):
    language: Literal["en", "ko", "ms"]
    language_source: LanguageSource
    confidence: float | None = Field(default=None, ge=0, le=1)


class OrderItemCreate(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    quantity: int = Field(ge=1, le=99)
    # Free-text special request for this item, e.g. "no cilantro please".
    note: str = Field(default="", max_length=200)


class OrderCreate(BaseModel):
    store_id: str
    session_id: str
    items: list[OrderItemCreate] = Field(min_length=1)
    customer_language: Literal["en", "ko", "ms"]
    order_source: Literal["qr"] = "qr"


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


class DraftItemsUpdate(BaseModel):
    items: list[OrderItemCreate]


class ProductWrite(BaseModel):
    """Vendor menu-editor submission: create or fully replace one listing's
    name/description/price/image/stock. Distinct from the assistant-grounding
    fields (spice/ingredients/allergens) edited via /admin/products."""

    name_en: str = Field(min_length=1, max_length=100)
    name_ko: str = Field(default="", max_length=100)
    name_ms: str = Field(default="", max_length=100)
    description_en: str = Field(default="", max_length=500)
    description_ko: str = Field(default="", max_length=500)
    description_ms: str = Field(default="", max_length=500)
    price_minor: int = Field(ge=0, le=100_000_00)
    image: str = Field(default="", max_length=1000)
    stock_count: int = Field(ge=0, le=100_000)
    available: bool = True
    # Provenance, e.g. "Sarawak, Malaysia" -- shown to the customer and usable
    # by the assistant for "where is this from?" questions.
    origin_en: str = Field(default="", max_length=200)
    origin_ko: str = Field(default="", max_length=200)
    origin_ms: str = Field(default="", max_length=200)


def _payload(value):
    return asdict(value)


def _lang_dict(en: str, ko: str, ms: str) -> dict[str, str]:
    """Build a per-language dict from vendor-form fields, keeping only the
    languages actually filled in (mirrors the /admin products page)."""
    values = {"en": en.strip()} if en.strip() else {}
    if ko.strip():
        values["ko"] = ko.strip()
    if ms.strip():
        values["ms"] = ms.strip()
    return values


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "item"


def _unique_product_id(store: DataStore, store_id: str, name_en: str) -> str:
    """Derive a stable product id from the English name, disambiguating against
    the store's existing catalog (e.g. "nasi-goreng", then "nasi-goreng-2")."""
    base = _slugify(name_en)
    existing = {product.id for product in store.list_products(store_id)}
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


@router.get("/stores/{store_id}/menu")
def get_menu(store_id: str, store: DataStore = Depends(get_data_store)) -> dict:
    products = store.list_products(store_id)
    if not products:
        raise HTTPException(404, "Store not found or menu is empty")
    return {"store_id": store_id, "products": [_payload(product) for product in products]}


@router.post("/stores/{store_id}/products", status_code=201)
def create_product(store_id: str, body: ProductWrite, store: DataStore = Depends(get_data_store)) -> dict:
    """Vendor menu editor: add a new menu item."""
    product_id = _unique_product_id(store, store_id, body.name_en)
    product = store.create_product(
        store_id,
        product_id,
        name=_lang_dict(body.name_en, body.name_ko, body.name_ms),
        description=_lang_dict(body.description_en, body.description_ko, body.description_ms),
        price_minor=body.price_minor,
        image=body.image,
        stock_count=body.stock_count,
        available=body.available,
        origin=_lang_dict(body.origin_en, body.origin_ko, body.origin_ms),
    )
    return _payload(product)


@router.patch("/stores/{store_id}/products/{product_id}")
def update_product_listing(
    store_id: str, product_id: str, body: ProductWrite, store: DataStore = Depends(get_data_store)
) -> dict:
    """Vendor menu editor: update an existing item's name/description/price/
    image/stock/origin/availability."""
    product = store.update_product_listing(
        store_id,
        product_id,
        name=_lang_dict(body.name_en, body.name_ko, body.name_ms),
        description=_lang_dict(body.description_en, body.description_ko, body.description_ms),
        price_minor=body.price_minor,
        image=body.image,
        stock_count=body.stock_count,
        available=body.available,
        origin=_lang_dict(body.origin_en, body.origin_ko, body.origin_ms),
    )
    if product is None:
        raise HTTPException(404, "Product not found")
    return _payload(product)


@router.delete("/stores/{store_id}/products/{product_id}", status_code=204)
def delete_product(store_id: str, product_id: str, store: DataStore = Depends(get_data_store)) -> None:
    """Vendor menu editor: remove a menu item. Past orders keep their own copy
    of the product name/price, so this does not affect order history."""
    if not store.delete_product(store_id, product_id):
        raise HTTPException(404, "Product not found")


@router.get("/stores/{store_id}/media")
def get_media(store_id: str, store: DataStore = Depends(get_data_store)) -> dict:
    assets = store.get_media_config(store_id)
    if not assets:
        raise HTTPException(404, "Store media configuration not found")
    return {"store_id": store_id, "assets": assets}


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreate, store: DataStore = Depends(get_data_store)):
    try:
        return _payload(store.create_session(body.store_id))
    except KeyError:
        raise HTTPException(404, "Store not found") from None


@router.get("/sessions/{session_id}")
def get_session(session_id: str, store: DataStore = Depends(get_data_store)):
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    return _payload(session)


@router.patch("/sessions/{session_id}/language")
async def update_language(session_id: str, body: LanguageUpdate, store: DataStore = Depends(get_data_store)):
    previous = store.get_session(session_id)
    session = store.update_session_language(
        session_id, body.language, body.language_source, confidence=body.confidence
    )
    if session is None:
        raise HTTPException(404, "Session not found")
    if previous is not None and (
        previous.language != session.language
        or previous.language_source != session.language_source
    ):
        await manager.broadcast(
            session.store_id,
            "language_changed",
            {"language": session.language, "language_source": session.language_source.value},
            session_id=session.id,
        )
    return _payload(session)


def _resolve_draft(session_id: str, store: DataStore) -> dict:
    """Shared by GET/PATCH draft: resolve the session's draft item ids against
    the product catalog into the priced view the payment page renders."""
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    product_map = {product.id: product for product in store.list_products(session.store_id)}
    items = store.get_draft_items(session_id)
    resolved = []
    total = 0
    for product_id, entry in items.items():
        product = product_map.get(product_id)
        if product is None:
            continue  # stale/removed product id -- skip rather than error
        quantity = entry["quantity"]
        resolved.append({
            "product_id": product_id,
            "quantity": quantity,
            "note": entry.get("note", ""),
            "name": product.name,
            "unit_price_minor": product.price_minor,
        })
        total += product.price_minor * quantity
    return {"session_id": session_id, "items": resolved, "total_minor": total, "currency": "MYR"}


@router.get("/sessions/{session_id}/draft")
def get_draft(session_id: str, store: DataStore = Depends(get_data_store)) -> dict:
    """The customer's in-progress order, as recognized so far from the voice
    conversation (or edited on the payment page) -- not yet paid/vendor-visible."""
    return _resolve_draft(session_id, store)


@router.patch("/sessions/{session_id}/draft")
def update_draft(
    session_id: str, body: DraftItemsUpdate, store: DataStore = Depends(get_data_store)
) -> dict:
    """Payment-page quantity/remove edits: an absolute replace of the draft
    (omit an item entirely to remove it), not a delta."""
    if store.get_session(session_id) is None:
        raise HTTPException(404, "Session not found")
    store.set_draft_items(
        session_id,
        {item.product_id: {"quantity": item.quantity, "note": item.note} for item in body.items},
    )
    return _resolve_draft(session_id, store)


@router.post("/sessions/{session_id}/draft/checkout", status_code=201)
async def checkout_draft(session_id: str, store: DataStore = Depends(get_data_store)):
    """Mock payment: turn the draft into a real order (vendor-visible, gets an
    order number) and broadcast it exactly like a directly-placed order."""
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    try:
        order = store.checkout_draft(session.store_id, session_id, session.language)
    except ValueError:
        raise HTTPException(409, "Nothing to pay for -- the draft order is empty") from None
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from None
    payload = _payload(order)
    await manager.broadcast(
        order.store_id, "new_order", payload, session_id=order.session_id
    )
    return payload


@router.post("/orders", status_code=201)
async def create_order(body: OrderCreate, store: DataStore = Depends(get_data_store)):
    try:
        order = store.create_order(
            body.store_id,
            body.session_id,
            [(item.product_id, item.quantity, item.note) for item in body.items],
            body.customer_language,
            body.order_source,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from None
    payload = _payload(order)
    await manager.broadcast(
        order.store_id, "new_order", payload, session_id=order.session_id
    )
    return payload


@router.get("/orders/{order_id}")
def get_order(order_id: str, store: DataStore = Depends(get_data_store)):
    order = store.get_order(order_id)
    if order is None:
        raise HTTPException(404, "Order not found")
    return _payload(order)


@router.get("/stores/{store_id}/orders")
def get_orders(
    store_id: str,
    status: OrderStatus | None = Query(default=None),
    store: DataStore = Depends(get_data_store),
):
    orders = store.list_orders(store_id)
    if status is not None:
        orders = [order for order in orders if order.status == status]
    return {"store_id": store_id, "orders": [_payload(order) for order in orders]}


@router.patch("/orders/{order_id}/status")
async def update_order_status(order_id: str, body: OrderStatusUpdate, store: DataStore = Depends(get_data_store)):
    try:
        order = store.update_order_status(order_id, body.status)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if order is None:
        raise HTTPException(404, "Order not found")
    payload = _payload(order)
    event_types = {
        OrderStatus.ACCEPTED: "order_accepted",
        OrderStatus.PREPARING: "order_preparing",
        OrderStatus.READY: "order_ready",
        OrderStatus.COMPLETED: "order_completed",
        OrderStatus.REJECTED: "order_rejected",
    }
    event_type = event_types.get(order.status)
    if event_type is not None:
        await manager.broadcast(
            order.store_id, event_type, payload, session_id=order.session_id
        )
    return payload
