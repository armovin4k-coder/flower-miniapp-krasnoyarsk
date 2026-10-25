from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    FlowerMovement,
    FlowerRecipe,
    FlowerStockItem,
    OrderItem,
    Product,
)

MILLI = Decimal("0.001")
KOPECK = Decimal("0.01")


def quantize_quantity(value: Decimal) -> Decimal:
    return value.quantize(MILLI, rounding=ROUND_HALF_UP)


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)


def is_low_stock(quantity: Decimal, reserved: Decimal, minimum: Decimal) -> bool:
    return minimum > 0 and quantity - reserved <= minimum


def weighted_average_cost(
    current_quantity: Decimal,
    current_cost: Decimal,
    incoming_quantity: Decimal,
    incoming_cost: Decimal,
) -> Decimal:
    total_quantity = current_quantity + incoming_quantity
    if total_quantity <= 0:
        return Decimal("0.00")
    total_value = current_quantity * current_cost + incoming_quantity * incoming_cost
    return quantize_money(total_value / total_quantity)


async def consume_order_recipes(
    session: AsyncSession,
    order_id: int,
    order_lines: list[tuple[OrderItem, str | None, int]],
    products: dict[int, Product],
) -> list[int]:
    product_ids = sorted({item.product_id for item, _, _ in order_lines})
    recipes = list(
        await session.scalars(
            select(FlowerRecipe)
            .options(selectinload(FlowerRecipe.lines))
            .where(FlowerRecipe.product_id.in_(product_ids))
        )
    )
    recipes_by_key = {(recipe.product_id, recipe.variant_id): recipe for recipe in recipes}

    planned: list[tuple[OrderItem, int, Decimal]] = []
    required: dict[int, Decimal] = {}
    uncovered: set[int] = set()
    for order_item, variant_id, bouquet_quantity in order_lines:
        recipe = recipes_by_key.get((order_item.product_id, variant_id or ""))
        if recipe is None:
            uncovered.add(order_item.product_id)
            continue
        for line in recipe.lines:
            quantity = quantize_quantity(line.quantity * bouquet_quantity)
            planned.append((order_item, line.item_id, quantity))
            required[line.item_id] = required.get(line.item_id, Decimal("0")) + quantity

    if not required:
        return sorted(uncovered)

    stock_items = {
        item.id: item
        for item in await session.scalars(
            select(FlowerStockItem)
            .where(FlowerStockItem.id.in_(sorted(required)), FlowerStockItem.active.is_(True))
            .order_by(FlowerStockItem.id)
            .with_for_update()
        )
    }
    if len(stock_items) != len(required):
        raise ValueError("Рецепт ссылается на отключённый цветок. Проверьте рецепты.")
    for item_id, quantity in required.items():
        stock_item = stock_items[item_id]
        available = stock_item.quantity - stock_item.reserved_quantity
        if available < quantity:
            raise ValueError(
                f"Недостаточно на складе: {stock_item.title} "
                f"(нужно {quantity}, доступно {available} {stock_item.unit})"
            )

    for item_id, quantity in required.items():
        stock_items[item_id].reserved_quantity += quantity
    for order_item, item_id, quantity in planned:
        stock_item = stock_items[item_id]
        cost = quantize_money(stock_item.average_cost_rub)
        session.add(
            FlowerMovement(
                item_id=item_id,
                movement_type="reservation",
                quantity=quantity,
                unit_cost_rub=cost,
                total_cost_rub=quantize_money(quantity * cost),
                note=f"Резерв по заказу №{order_id}: {products[order_item.product_id].title}",
                order_id=order_id,
                order_item_id=order_item.id,
            )
        )
    return sorted(uncovered)


async def update_order_flower_inventory(
    session: AsyncSession,
    order_id: int,
    target_status: str,
) -> None:
    active_reservations = list(
        await session.scalars(
            select(FlowerMovement)
            .where(
                FlowerMovement.order_id == order_id,
                FlowerMovement.movement_type == "reservation",
                FlowerMovement.is_reversed.is_(False),
            )
            .order_by(FlowerMovement.item_id, FlowerMovement.id)
        )
    )
    active_sales = list(
        await session.scalars(
            select(FlowerMovement)
            .where(
                FlowerMovement.order_id == order_id,
                FlowerMovement.movement_type == "sale",
                FlowerMovement.is_reversed.is_(False),
            )
            .order_by(FlowerMovement.item_id, FlowerMovement.id)
        )
    )
    active_returns = list(
        await session.scalars(
            select(FlowerMovement)
            .where(
                FlowerMovement.order_id == order_id,
                FlowerMovement.movement_type == "sale_return",
                FlowerMovement.is_reversed.is_(False),
            )
            .order_by(FlowerMovement.item_id, FlowerMovement.id)
        )
    )
    active_releases = list(
        await session.scalars(
            select(FlowerMovement)
            .where(
                FlowerMovement.order_id == order_id,
                FlowerMovement.movement_type == "reservation_release",
                FlowerMovement.is_reversed.is_(False),
            )
            .order_by(FlowerMovement.item_id, FlowerMovement.id)
        )
    )
    item_ids = sorted(
        {
            movement.item_id
            for movement in active_reservations + active_sales + active_returns + active_releases
        }
    )
    if not item_ids:
        return
    stock_items = {
        item.id: item
        for item in await session.scalars(
            select(FlowerStockItem)
            .where(FlowerStockItem.id.in_(item_ids))
            .order_by(FlowerStockItem.id)
            .with_for_update()
        )
    }
    if len(stock_items) != len(item_ids):
        raise ValueError("Позиция цветка из рецепта заказа больше недоступна")

    if target_status == "delivered":
        if active_reservations:
            for movement in active_reservations:
                stock_item = stock_items[movement.item_id]
                if stock_item.quantity < movement.quantity:
                    raise ValueError(
                        f"Недостаточно цветка для завершения заказа: {stock_item.title}"
                    )
            for movement in active_reservations:
                stock_item = stock_items[movement.item_id]
                stock_item.quantity -= movement.quantity
                stock_item.reserved_quantity -= movement.quantity
                movement.is_reversed = True
                session.add(
                    FlowerMovement(
                        item_id=movement.item_id,
                        movement_type="sale",
                        quantity=movement.quantity,
                        unit_cost_rub=movement.unit_cost_rub,
                        total_cost_rub=movement.total_cost_rub,
                        note=f"Продажа по заказу №{order_id}",
                        order_id=order_id,
                        order_item_id=movement.order_item_id,
                    )
                )
            return
        if active_returns or await session.scalar(
            select(FlowerMovement.id).where(
                FlowerMovement.order_id == order_id,
                FlowerMovement.movement_type == "reservation_release",
                FlowerMovement.is_reversed.is_(False),
            ).limit(1)
        ):
            await update_order_flower_inventory(session, order_id, "reserved")
            await update_order_flower_inventory(session, order_id, "delivered")
        return

    if target_status == "cancelled":
        for movement in active_reservations:
            stock_items[movement.item_id].reserved_quantity -= movement.quantity
            movement.is_reversed = True
            session.add(
                FlowerMovement(
                    item_id=movement.item_id,
                    movement_type="reservation_release",
                    quantity=movement.quantity,
                    unit_cost_rub=movement.unit_cost_rub,
                    total_cost_rub=movement.total_cost_rub,
                    note=f"Снят резерв по отменённому заказу №{order_id}",
                    order_id=order_id,
                    order_item_id=movement.order_item_id,
                )
            )
        for movement in active_sales:
            stock_item = stock_items[movement.item_id]
            stock_item.average_cost_rub = weighted_average_cost(
                stock_item.quantity,
                stock_item.average_cost_rub,
                movement.quantity,
                movement.unit_cost_rub,
            )
            stock_item.quantity += movement.quantity
            movement.is_reversed = True
            session.add(
                FlowerMovement(
                    item_id=movement.item_id,
                    movement_type="sale_return",
                    quantity=movement.quantity,
                    unit_cost_rub=movement.unit_cost_rub,
                    total_cost_rub=movement.total_cost_rub,
                    note=f"Возврат продажи по отменённому заказу №{order_id}",
                    order_id=order_id,
                    order_item_id=movement.order_item_id,
                )
            )
        return

    if active_reservations:
        return

    if active_sales:
        for movement in active_sales:
            stock_item = stock_items[movement.item_id]
            stock_item.average_cost_rub = weighted_average_cost(
                stock_item.quantity,
                stock_item.average_cost_rub,
                movement.quantity,
                movement.unit_cost_rub,
            )
            stock_item.quantity += movement.quantity
            stock_item.reserved_quantity += movement.quantity
            movement.is_reversed = True
            session.add(
                FlowerMovement(
                    item_id=movement.item_id,
                    movement_type="reservation",
                    quantity=movement.quantity,
                    unit_cost_rub=movement.unit_cost_rub,
                    total_cost_rub=movement.total_cost_rub,
                    note=f"Резерв восстановлен по заказу №{order_id}",
                    order_id=order_id,
                    order_item_id=movement.order_item_id,
                )
            )
        return

    releasable = active_returns + active_releases
    required: dict[int, Decimal] = {}
    for movement in releasable:
        if movement.movement_type == "sale_return":
            required[movement.item_id] = required.get(
                movement.item_id, Decimal("0")
            ) + movement.quantity
    for item_id, quantity in required.items():
        available = stock_items[item_id].quantity - stock_items[item_id].reserved_quantity
        if available < quantity:
            raise ValueError(
                f"Недостаточно цветка для восстановления заказа: {stock_items[item_id].title}"
            )
    for movement in releasable:
        movement.is_reversed = True
        stock_item = stock_items[movement.item_id]
        stock_item.reserved_quantity += movement.quantity
        session.add(
            FlowerMovement(
                item_id=movement.item_id,
                movement_type="reservation",
                quantity=movement.quantity,
                unit_cost_rub=movement.unit_cost_rub,
                total_cost_rub=movement.total_cost_rub,
                note=f"Резерв восстановлен по заказу №{order_id}",
                order_id=order_id,
                order_item_id=movement.order_item_id,
            )
        )
