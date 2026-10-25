from datetime import datetime
from decimal import Decimal
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text, Numeric, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
class Base(DeclarativeBase): pass
class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="Букеты")
    categories_json: Mapped[str] = mapped_column(Text, default="[]")
    source_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    price_rub: Mapped[int] = mapped_column(Integer)
    image_url: Mapped[str] = mapped_column(String(1000), default="")
    stock: Mapped[int] = mapped_column(Integer, default=10)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    customer_name: Mapped[str] = mapped_column(String(160))
    phone: Mapped[str] = mapped_column(String(40))
    address: Mapped[str] = mapped_column(String(500))
    delivery_at: Mapped[str] = mapped_column(String(100), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="new")
    fulfillment_method: Mapped[str] = mapped_column(String(20), default="delivery")
    subtotal_rub: Mapped[int] = mapped_column(Integer)
    delivery_rub: Mapped[int] = mapped_column(Integer)
    total_rub: Mapped[int] = mapped_column(Integer)
    payment_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    payment_status: Mapped[str] = mapped_column(String(40), default="not_created")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(Integer)
    title_snapshot: Mapped[str] = mapped_column(String(160))
    variant_snapshot: Mapped[str | None] = mapped_column(String(160), nullable=True)
    variant_id_snapshot: Mapped[str | None] = mapped_column(String(40), nullable=True)
    unit_price_rub: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    order: Mapped[Order] = relationship(back_populates="items")

class Promotion(Base):
    __tablename__ = "promotions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(Integer)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class TelegramSubscriber(Base):
    __tablename__ = "telegram_subscribers"
    telegram_user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str] = mapped_column(String(160), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FlowerStockItem(Base):
    __tablename__ = "flower_stock_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(120), unique=True)
    unit: Mapped[str] = mapped_column(String(20), default="шт")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    reserved_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    average_cost_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    minimum_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("0"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FlowerMovement(Base):
    __tablename__ = "flower_movements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("flower_stock_items.id"))
    movement_type: Mapped[str] = mapped_column(String(20))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_cost_rub: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    total_cost_rub: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    note: Mapped[str] = mapped_column(Text, default="")
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    order_item_id: Mapped[int | None] = mapped_column(ForeignKey("order_items.id"), nullable=True)
    is_reversed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FlowerRecipe(Base):
    __tablename__ = "flower_recipes"
    __table_args__ = (UniqueConstraint("product_id", "variant_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    variant_id: Mapped[str] = mapped_column(String(40), default="")
    lines: Mapped[list["FlowerRecipeLine"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )


class FlowerRecipeLine(Base):
    __tablename__ = "flower_recipe_lines"
    __table_args__ = (UniqueConstraint("recipe_id", "item_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("flower_recipes.id", ondelete="CASCADE"))
    item_id: Mapped[int] = mapped_column(ForeignKey("flower_stock_items.id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    recipe: Mapped[FlowerRecipe] = relationship(back_populates="lines")
