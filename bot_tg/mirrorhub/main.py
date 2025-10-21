import asyncio
import logging
import re
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ErrorEvent

from mirrorhub.core.repo import init_db
from mirrorhub.central_bot import get_dp
from mirrorhub.config import CENTRAL_BOT_TOKEN, SUPERADMINS, DB_PATH

TOKEN_RE = re.compile(r'^\d{6,12}:[A-Za-z0-9_-]{35,}$')

async def on_startup(bot: Bot):
    me = await bot.get_me()
    logging.info("Central bot started as @%s (id=%s)", me.username, me.id)
    logging.info("DB path: %s", DB_PATH)
    text = f"✅ Центральный бот запущен: @{me.username} ({me.id})"
    for admin_id in SUPERADMINS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:
            logging.warning("Can't notify admin %s: %s", admin_id, e)

async def on_shutdown(bot: Bot):
    logging.info("Central bot shutdown")
    await bot.session.close()

async def on_error(event: ErrorEvent):
    logging.exception("Unhandled error in handler: %s", event.exception)

async def main():
    """Оригинальная функция запуска (для standalone режима)"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    init_db()

    if not TOKEN_RE.match(CENTRAL_BOT_TOKEN):
        raise RuntimeError("CENTRAL_BOT_TOKEN не задан или некорректен в mirrorhub/config.py")

    dp = get_dp()
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    dp.errors.register(on_error)

    bot = Bot(token=CENTRAL_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    logging.info("Starting polling…")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())