import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.filters.command import CommandObject
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .config import settings
from .db import SessionLocal
from .models import Order, Product, Promotion, TelegramSubscriber
from .order_workflow import ORDER_STATUSES, change_order_status, mark_order_paid
from .telegram_notifications import send_customer_notification

logger = logging.getLogger(__name__)


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in settings.admin_ids


def order_text(order: Order) -> str:
    method = "Самовывоз" if order.fulfillment_method == "pickup" else "Доставка"
    address = order.address or settings.pickup_address
    items = "\n".join(
        f"• {item.title_snapshot}"
        f"{f' — {item.variant_snapshot}' if item.variant_snapshot else ''}"
        f" × {item.quantity} — {item.unit_price_rub * item.quantity} ₽"
        for item in order.items
    )
    return (
        f"Заказ №{order.id} · {ORDER_STATUSES.get(order.status, order.status)}\n"
        f"{order.customer_name}, {order.phone}\n{method}: {address}\n"
        f"{items}\nИтого: {order.total_rub} ₽\n"
        f"Оплата: {'получена' if order.payment_status == 'succeeded' else order.payment_status}"
        + (f"\nВремя: {order.delivery_at}" if order.delivery_at else "")
        + (f"\nКомментарий: {order.note}" if order.note else "")
    )


def order_keyboard(order: Order) -> InlineKeyboardMarkup:
    next_method_status = (
        ("ready_for_pickup", "Готов к самовывозу")
        if order.fulfillment_method == "pickup"
        else ("out_for_delivery", "Передан в доставку")
    )
    buttons = [
        [
            InlineKeyboardButton(
                text="Подтвердить",
                callback_data=f"order:status:{order.id}:confirmed",
            ),
            InlineKeyboardButton(
                text="Собирается",
                callback_data=f"order:status:{order.id}:preparing",
            ),
        ],
        [
            InlineKeyboardButton(
                text=next_method_status[1],
                callback_data=f"order:status:{order.id}:{next_method_status[0]}",
            ),
            InlineKeyboardButton(
                text="Выдан",
                callback_data=f"order:status:{order.id}:delivered",
            ),
        ],
        [
            InlineKeyboardButton(
                text="Оплата получена",
                callback_data=f"order:paid:{order.id}",
            ),
            InlineKeyboardButton(
                text="Отменить",
                callback_data=f"order:status:{order.id}:cancelled",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def load_order(order_id: int) -> Order | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.id == order_id)
        )


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not configured; Telegram bot cannot start")

    dp = Dispatcher()

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        if message.from_user:
            async with SessionLocal() as session:
                subscriber = await session.get(
                    TelegramSubscriber, message.from_user.id
                )
                if subscriber is None:
                    session.add(
                        TelegramSubscriber(
                            telegram_user_id=message.from_user.id,
                            first_name=message.from_user.first_name[:160],
                        )
                    )
                else:
                    subscriber.first_name = message.from_user.first_name[:160]
                    subscriber.is_active = True
                await session.commit()
        await message.answer(
            "🌷 Цветочный магазин Красноярска.\n"
            "Нажмите кнопку «Открыть магазин» в меню, чтобы выбрать букет и оформить заказ."
        )

    @dp.message(Command("admin"))
    async def admin(message: Message) -> None:
        if not is_admin(message.from_user.id if message.from_user else None):
            await message.answer("Доступ запрещён.")
            return
        await message.answer(
            "Команды администратора:\n"
            "/orders — последние заказы\n"
            "/order ID — детали заказа и действия\n"
            "/stock — остатки; /stock ID КОЛИЧЕСТВО — изменить остаток\n"
            "/payinfo ID ТЕКСТ — отправить клиенту способ оплаты после подтверждения\n"
            "/promo ТЕКСТ — объявление акции с подтверждением рассылки"
        )

    @dp.message(Command("orders"))
    async def orders(message: Message) -> None:
        if not is_admin(message.from_user.id if message.from_user else None):
            await message.answer("Доступ запрещён.")
            return
        async with SessionLocal() as session:
            rows = list(
                await session.scalars(
                    select(Order)
                    .options(selectinload(Order.items))
                    .order_by(Order.id.desc())
                    .limit(10)
                )
            )
        if not rows:
            await message.answer("Заказов пока нет.")
            return
        for order in rows:
            await message.answer(
                order_text(order),
                reply_markup=order_keyboard(order),
            )

    @dp.message(Command("order"))
    async def order_detail(message: Message, command: CommandObject) -> None:
        if not is_admin(message.from_user.id if message.from_user else None):
            await message.answer("Доступ запрещён.")
            return
        if not command.args or not command.args.strip().isdigit():
            await message.answer("Использование: /order ID")
            return
        order = await load_order(int(command.args.strip()))
        if order is None:
            await message.answer("Заказ не найден.")
            return
        await message.answer(order_text(order), reply_markup=order_keyboard(order))

    @dp.message(Command("payinfo"))
    async def payinfo(message: Message, command: CommandObject) -> None:
        if not is_admin(message.from_user.id if message.from_user else None):
            await message.answer("Доступ запрещён.")
            return
        parts = (command.args or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit():
            await message.answer("Использование: /payinfo ID заказа Текст с реквизитами или способом оплаты")
            return
        if len(parts[1]) > 3500:
            await message.answer("Текст оплаты слишком длинный. Максимум — 3500 символов.")
            return
        order = await load_order(int(parts[0]))
        if order is None:
            await message.answer("Заказ не найден.")
            return
        if order.status not in {"confirmed", "preparing", "ready_for_pickup", "out_for_delivery"}:
            await message.answer("Сначала подтвердите заказ командой /orders.")
            return
        delivered = await send_customer_notification(
            message.bot,
            order.telegram_user_id,
            f"Информация об оплате заказа №{order.id}:\n\n{parts[1]}",
        )
        await message.answer(
            "Информация об оплате отправлена клиенту."
            if delivered
            else "Не удалось доставить сообщение клиенту. Проверьте, что он запустил бота."
        )

    @dp.message(Command("stock"))
    async def stock(message: Message, command: CommandObject) -> None:
        if not is_admin(message.from_user.id if message.from_user else None):
            await message.answer("Доступ запрещён.")
            return
        if command.args:
            parts = command.args.split()
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                await message.answer("Использование: /stock ID_ТОВАРА КОЛИЧЕСТВО")
                return
            product_id, quantity = map(int, parts)
            async with SessionLocal() as session:
                product = await session.scalar(
                    select(Product)
                    .where(Product.id == product_id, Product.active.is_(True))
                    .with_for_update()
                )
                if product is None:
                    await message.answer("Активный товар с таким ID не найден.")
                    return
                product.stock = quantity
                await session.commit()
            await message.answer(
                f"Остаток «{product.title}» обновлён: {product.stock} шт."
            )
            return

        async with SessionLocal() as session:
            products = list(
                await session.scalars(
                    select(Product)
                    .where(Product.active.is_(True))
                    .order_by(Product.id)
                )
            )
        if not products:
            await message.answer("Активных товаров нет.")
            return
        for start in range(0, len(products), 20):
            chunk = products[start : start + 20]
            await message.answer(
                "\n".join(f"{product.id}. {product.title} — {product.stock} шт." for product in chunk)
            )

    @dp.message(Command("promo"))
    async def promo(message: Message, command: CommandObject) -> None:
        if not is_admin(message.from_user.id if message.from_user else None):
            await message.answer("Доступ запрещён.")
            return
        text = (command.args or "").strip()
        if not text:
            await message.answer("Напишите объявление в одной команде:\n/promo Текст акции")
            return
        if len(text) > 3500:
            await message.answer("Текст акции слишком длинный. Максимум — 3500 символов.")
            return
        await message.answer(
            f"Предпросмотр акции:\n\n{text}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Разослать клиентам",
                            callback_data="promo:send",
                        ),
                        InlineKeyboardButton(
                            text="Отмена",
                            callback_data="promo:cancel",
                        ),
                    ]
                ]
            ),
        )

    @dp.callback_query(F.data.startswith("order:"))
    async def order_action(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Доступ запрещён.", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) not in {3, 4} or not parts[2].isdigit():
            await callback.answer("Некорректное действие.", show_alert=True)
            return
        order_id = int(parts[2])

        async with SessionLocal() as session:
            order = await session.scalar(
                select(Order)
                .options(selectinload(Order.items))
                .where(Order.id == order_id)
                .with_for_update()
            )
            if order is None:
                await callback.answer("Заказ не найден.", show_alert=True)
                return
            if parts[1] == "paid" and len(parts) == 3:
                try:
                    changed = await mark_order_paid(order, session)
                except ValueError as exc:
                    await callback.answer(str(exc), show_alert=True)
                    return
                notification = f"Оплата заказа №{order.id} отмечена."
            elif parts[1] == "status" and len(parts) == 4:
                try:
                    changed = await change_order_status(session, order, parts[3])
                except ValueError as exc:
                    await callback.answer(str(exc), show_alert=True)
                    return
                notification = (
                    f"Статус заказа №{order.id}: "
                    f"{ORDER_STATUSES[order.status]}."
                )
            else:
                await callback.answer("Некорректное действие.", show_alert=True)
                return
            user_id = order.telegram_user_id
            updated_text = order_text(order)
            updated_keyboard = order_keyboard(order)
            await session.commit()

        notification_sent = True
        if changed:
            notification_sent = await send_customer_notification(
                callback.bot, user_id, notification
            )
        await callback.answer("Сохранено.")
        if isinstance(callback.message, Message):
            suffix = "" if notification_sent else "\n⚠️ Клиенту не удалось отправить уведомление."
            await callback.message.edit_text(
                updated_text + suffix,
                reply_markup=updated_keyboard,
            )

    @dp.callback_query(F.data.startswith("promo:"))
    async def promo_action(callback: CallbackQuery) -> None:
        if not is_admin(callback.from_user.id):
            await callback.answer("Доступ запрещён.", show_alert=True)
            return
        if callback.data == "promo:cancel":
            await callback.answer("Рассылка отменена.")
            if isinstance(callback.message, Message):
                await callback.message.edit_text("Рассылка акции отменена.")
            return
        if (
            callback.data != "promo:send"
            or not isinstance(callback.message, Message)
            or not callback.message.text
        ):
            await callback.answer("Предпросмотр акции не найден.", show_alert=True)
            return

        prefix = "Предпросмотр акции:\n\n"
        promotion_text = callback.message.text.removeprefix(prefix)
        if not promotion_text.strip():
            await callback.answer("Текст акции пуст.", show_alert=True)
            return
        await callback.answer("Начинаю рассылку.")

        async with SessionLocal() as session:
            subscriber_rows = list(
                await session.scalars(
                    select(TelegramSubscriber).where(
                        TelegramSubscriber.is_active.is_(True)
                    )
                )
            )
            subscribers = {subscriber.telegram_user_id: subscriber for subscriber in subscriber_rows}
            customer_ids = set(subscribers)
            past_customers = list(
                await session.scalars(select(Order.telegram_user_id).distinct())
            )
            customer_ids.update(past_customers)
            promotion = Promotion(
                text=promotion_text,
                created_by=callback.from_user.id,
            )
            session.add(promotion)
            await session.flush()
            sent_count = 0
            failed_count = 0
            for customer_id in customer_ids:
                delivered = await send_customer_notification(
                    callback.bot, customer_id, promotion_text
                )
                if delivered:
                    sent_count += 1
                else:
                    failed_count += 1
                    subscriber = subscribers.get(customer_id)
                    if subscriber:
                        subscriber.is_active = False
            promotion.sent_count = sent_count
            promotion.failed_count = failed_count
            await session.commit()

        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                f"Рассылка завершена.\nДоставлено: {sent_count}\nНе доставлено: {failed_count}"
            )

    async with Bot(settings.bot_token) as bot:
        await dp.start_polling(bot, close_bot_session=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
