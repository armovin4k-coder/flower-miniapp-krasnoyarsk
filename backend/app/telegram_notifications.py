import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)


async def send_customer_notification(bot: Bot, user_id: int, text: str) -> bool:
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError:
        logger.exception("Could not notify Telegram customer %s", user_id)
        return False
    return True


async def notify_admins(bot_token: str, admin_ids: set[int], text: str) -> None:
    if not bot_token:
        logger.error("Cannot notify admins: Telegram bot token is not configured")
        return
    async with Bot(bot_token) as bot:
        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, text)
            except TelegramAPIError:
                logger.exception("Could not notify Telegram admin %s", admin_id)


async def notify_user(bot_token: str, user_id: int, text: str) -> bool:
    if not bot_token:
        logger.error("Cannot notify Telegram customer: bot token is not configured")
        return False
    async with Bot(bot_token) as bot:
        return await send_customer_notification(bot, user_id, text)
