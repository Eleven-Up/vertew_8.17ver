"""MVP menu, customer-session, and order REST API."""

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


def _payload(value):
    return asdict(value)


@router.get("/stores/{store_id}/menu")
def get_menu(store_id: str, store: DataStore = Depends(get_data_store)) -> dict:
    products = store.list_products(store_id)
    if not products:
        raise HTTPException(404, "Store not found or menu is empty")
    return {"store_id": store_id, "products": [_payload(product) for product in products]}


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
    for product_id, quantity in items.items():
        product = product_map.get(product_id)
        if product is None:
            continue  # stale/removed product id -- skip rather than error
        resolved.append({
            "product_id": product_id,
            "quantity": quantity,
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
    store.set_draft_items(session_id, {item.product_id: item.quantity for item in body.items})
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
            [(item.product_id, item.quantity) for item in body.items],
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
