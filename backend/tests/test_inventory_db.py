import os
import unittest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import engine
from app.inventory import consume_order_recipes, update_order_flower_inventory
from app.models import FlowerRecipe, FlowerRecipeLine, FlowerStockItem, Order, OrderItem, Product
from app.order_workflow import change_order_status


@unittest.skipUnless(
    os.getenv("RUN_INVENTORY_DB_TESTS") == "1",
    "Set RUN_INVENTORY_DB_TESTS=1 to run transaction-isolated PostgreSQL tests.",
)
class InventoryWorkflowDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_reserve_deliver_cancel_and_reopen_order(self):
        async with engine.connect() as connection:
            transaction = await connection.begin()
            sessions = async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                async with sessions() as session:
                    token = uuid4().hex
                    product = Product(
                        title=f"Тестовый букет {token}",
                        description="Тест рецепта",
                        category="Тест",
                        price_rub=1000,
                        image_url="",
                        stock=8,
                    )
                    flower = FlowerStockItem(
                        title=f"Тестовый цветок {token}",
                        unit="стебель",
                        quantity=Decimal("20"),
                        reserved_quantity=Decimal("0"),
                        average_cost_rub=Decimal("100"),
                        minimum_quantity=Decimal("2"),
                    )
                    order = Order(
                        telegram_user_id=1,
                        customer_name="Тест",
                        phone="00000",
                        address="Самовывоз",
                        status="new",
                        fulfillment_method="pickup",
                        subtotal_rub=1000,
                        delivery_rub=0,
                        total_rub=1000,
                    )
                    session.add_all([product, flower, order])
                    await session.flush()
                    recipe = FlowerRecipe(product_id=product.id, variant_id="")
                    session.add(recipe)
                    await session.flush()
                    session.add(FlowerRecipeLine(
                        recipe_id=recipe.id,
                        item_id=flower.id,
                        quantity=Decimal("3"),
                    ))
                    order_item = OrderItem(
                        order_id=order.id,
                        product_id=product.id,
                        title_snapshot=product.title,
                        unit_price_rub=1000,
                        quantity=2,
                    )
                    order.items.append(order_item)
                    await session.flush()

                    missing = await consume_order_recipes(
                        session, order.id, [(order_item, None, 2)], {product.id: product}
                    )
                    self.assertEqual(missing, [])
                    self.assertEqual(flower.quantity, Decimal("20.000"))
                    self.assertEqual(flower.reserved_quantity, Decimal("6.000"))

                    await change_order_status(session, order, "delivered")
                    self.assertEqual(flower.quantity, Decimal("14.000"))
                    self.assertEqual(flower.reserved_quantity, Decimal("0.000"))

                    await change_order_status(session, order, "cancelled")
                    self.assertEqual(flower.quantity, Decimal("20.000"))
                    self.assertEqual(flower.reserved_quantity, Decimal("0.000"))
                    self.assertEqual(product.stock, 10)

                    await change_order_status(session, order, "confirmed")
                    self.assertEqual(flower.quantity, Decimal("20.000"))
                    self.assertEqual(flower.reserved_quantity, Decimal("6.000"))
                    self.assertEqual(product.stock, 8)

                    await change_order_status(session, order, "delivered")
                    self.assertEqual(flower.quantity, Decimal("14.000"))
                    self.assertEqual(flower.reserved_quantity, Decimal("0.000"))
            finally:
                await transaction.rollback()
                await engine.dispose()

    async def test_cancelling_unfulfilled_order_releases_reserved_stock(self):
        async with engine.connect() as connection:
            transaction = await connection.begin()
            sessions = async_sessionmaker(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            )
            try:
                async with sessions() as session:
                    token = uuid4().hex
                    product = Product(
                        title=f"Букет {token}", description="", category="Тест",
                        price_rub=100, image_url="", stock=3,
                    )
                    flower = FlowerStockItem(
                        title=f"Цветок {token}", unit="шт",
                        quantity=Decimal("8"), reserved_quantity=Decimal("0"),
                        average_cost_rub=Decimal("12.50"),
                    )
                    order = Order(
                        telegram_user_id=1, customer_name="Тест", phone="00000",
                        address="Самовывоз", status="new", fulfillment_method="pickup",
                        subtotal_rub=100, delivery_rub=0, total_rub=100,
                    )
                    session.add_all([product, flower, order])
                    await session.flush()
                    recipe = FlowerRecipe(product_id=product.id, variant_id="")
                    session.add(recipe)
                    await session.flush()
                    session.add(FlowerRecipeLine(
                        recipe_id=recipe.id, item_id=flower.id,
                        quantity=Decimal("2"),
                    ))
                    order_item = OrderItem(
                        order_id=order.id, product_id=product.id,
                        title_snapshot=product.title, unit_price_rub=100, quantity=1,
                    )
                    order.items.append(order_item)
                    await session.flush()

                    await consume_order_recipes(
                        session, order.id, [(order_item, None, 1)], {product.id: product}
                    )
                    await change_order_status(session, order, "cancelled")
                    self.assertEqual(flower.quantity, Decimal("8.000"))
                    self.assertEqual(flower.reserved_quantity, Decimal("0.000"))
                    self.assertEqual(product.stock, 3)
                    await change_order_status(session, order, "confirmed")
                    self.assertEqual(flower.quantity, Decimal("8.000"))
                    self.assertEqual(flower.reserved_quantity, Decimal("2.000"))
                    self.assertEqual(product.stock, 2)
            finally:
                await transaction.rollback()
                await engine.dispose()


if __name__ == "__main__":
    unittest.main()
