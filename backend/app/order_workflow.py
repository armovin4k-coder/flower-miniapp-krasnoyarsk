from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Order, Product
from .inventory import update_order_flower_inventory

ORDER_STATUSES = {
    "new": "Новый",
    "confirmed": "Подтверждён",
    "preparing": "Собирается",
    "ready_for_pickup": "Готов к самовывозу",
    "out_for_delivery": "Передан в доставку",
    "delivered": "Выдан",
    "cancelled": "Отменён",
}


async def change_order_status(
    session: AsyncSession, order: Order, status: str
) -> bool:
    if status not in ORDER_STATUSES:
        raise ValueError("Недопустимый статус заказа")
    if order.status == status:
        return False

    items = list(order.items)
    product_ids = sorted({item.product_id for item in items})
    products = {
        product.id: product
        for product in await session.scalars(
            select(Product)
            .where(Product.id.in_(product_ids))
            .order_by(Product.id)
            .with_for_update()
        )
    }
    if order.status == "cancelled" and status != "cancelled":
        missing = set(product_ids) - products.keys()
        if missing:
            raise ValueError("Невозможно восстановить заказ: товар удалён")

    if order.status == "cancelled" and status != "cancelled":
        required: dict[int, int] = {}
        for item in items:
            required[item.product_id] = required.get(item.product_id, 0) + item.quantity
        if any(products[product_id].stock < quantity for product_id, quantity in required.items()):
            raise ValueError("Недостаточно остатков, чтобы восстановить заказ")
        for product_id, quantity in required.items():
            products[product_id].stock -= quantity
    elif order.status != "cancelled" and status == "cancelled":
        for item in items:
            product = products.get(item.product_id)
            if product is not None:
                product.stock += item.quantity
    try:
        await update_order_flower_inventory(session, order.id, status)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    order.status = status
    if status == "confirmed" and order.payment_status != "succeeded":
        order.payment_status = "awaiting_payment"
    elif status == "cancelled" and order.payment_status != "succeeded":
        order.payment_status = "cancelled"
    await session.flush()
    return True


async def mark_order_paid(order: Order, session: AsyncSession) -> bool:
    if order.payment_status == "succeeded":
        return False
    if order.status not in {
        "confirmed",
        "preparing",
        "ready_for_pickup",
        "out_for_delivery",
        "delivered",
    }:
        raise ValueError("Сначала подтвердите заказ")
    order.payment_status = "succeeded"
    await session.flush()
    return True
