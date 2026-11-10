from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from probe.models import Order, PaymentEvent, Product
from probe.schemas import CreateOrder, PaymentCallback


class BusinessError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def get_product(session: Session, product_id: int) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise BusinessError(404, "product_not_found", "Product does not exist")
    return product


def get_order(session: Session, order_id: UUID, *, lock: bool = False) -> Order:
    query = select(Order).where(Order.id == order_id)
    if lock:
        query = query.with_for_update(key_share=True)  # PostgreSQL FOR NO KEY UPDATE
    order = session.scalar(query)
    if order is None:
        raise BusinessError(404, "order_not_found", "Order does not exist")
    return order


def create_order(session: Session, payload: CreateOrder) -> Order:
    # UPDATE takes a PostgreSQL row lock; after waiting, the WHERE condition is
    # checked against the latest row. No read-then-write stock race is possible.
    product = session.execute(
        update(Product)
        .where(Product.id == payload.product_id, Product.is_active.is_(True), Product.stock >= payload.quantity)
        .values(stock=Product.stock - payload.quantity)
        .returning(Product.id, Product.price_cents)
    ).first()
    if product is None:
        existing = get_product(session, payload.product_id)
        if not existing.is_active:
            raise BusinessError(409, "product_inactive", "Product has been removed; restore it before creating orders")
        raise BusinessError(409, "insufficient_stock", "Insufficient stock")
    total = product.price_cents * payload.quantity
    if total > 9_223_372_036_854_775_807:
        raise BusinessError(409, "amount_overflow", "Order amount exceeds supported range")
    order = Order(
        product_id=product.id,
        quantity=payload.quantity,
        total_amount_cents=total,
        status="pending",
    )
    session.add(order)
    session.flush()  # INSERT must succeed before the surrounding transaction commits.
    return order


def cancel_order(session: Session, order_id: UUID) -> Order:
    order = get_order(session, order_id, lock=True)
    if order.status == "paid":
        raise BusinessError(409, "order_already_paid", "Paid orders cannot be cancelled")
    if order.status == "cancelled":
        return order
    session.execute(
        update(Product)
        .where(Product.id == order.product_id)
        .values(stock=Product.stock + order.quantity)
    )
    order.status = "cancelled"
    order.updated_at = func.now()
    session.flush()
    return order


def pay_order(session: Session, payload: PaymentCallback) -> tuple[Order, bool]:
    # Claim the unique event before locking the order. ON CONFLICT waits for an
    # uncommitted rival event. A failed transaction leaves no claimed event.
    # Check existence first so a missing order produces a business 404, not FK 503.
    get_order(session, payload.order_id)
    claimed = session.scalar(
        insert(PaymentEvent)
        .values(event_id=payload.event_id, order_id=payload.order_id, amount_cents=payload.amount_cents)
        .on_conflict_do_nothing(index_elements=[PaymentEvent.event_id])
        .returning(PaymentEvent.event_id)
    )
    if claimed is None:
        event = session.get(PaymentEvent, payload.event_id)
        if event.order_id != payload.order_id or event.amount_cents != payload.amount_cents:
            raise BusinessError(409, "event_conflict", "Event ID was used with different content")
        return get_order(session, payload.order_id), True

    # populate_existing refreshes the unlocked existence check after waiting for
    # a concurrent cancel/pay transaction, instead of trusting the identity map.
    order = session.scalar(
        select(Order).where(Order.id == payload.order_id)
        .with_for_update(key_share=True).execution_options(populate_existing=True)
    )
    if payload.amount_cents != order.total_amount_cents:
        raise BusinessError(409, "payment_amount_mismatch", "Payment amount does not match the order")
    if order.status == "cancelled":
        raise BusinessError(409, "order_cancelled", "Cancelled orders cannot be paid")
    if order.status == "pending":
        order.status = "paid"
        order.updated_at = func.now()
        session.flush()
    # A different event for an already paid order is recorded without another
    # state transition. No actual charge or external side effect exists here.
    return order, False
