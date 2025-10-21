
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError

from sqlalchemy import select
from mirrorhub.core.models import Token, BotInstance
from mirrorhub.core.repo import set_bot_meta, set_bot_running, mark_token_status
from mirrorhub.core.db import SessionLocal
from mirrorhub.token_pool import replace_dead_token
from mirrorhub.mirror_bot import setup_handlers

class MirrorRunner:
    def __init__(self, bot_id: int, token: str):
        self.bot_id = bot_id
        self.token = token
        self.bot: Bot | None = None
        self.dp: Dispatcher | None = None
        self.task: asyncio.Task | None = None

    async def start(self):
        self.bot = Bot(self.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher()
        setup_handlers(self.dp, self.bot_id)
        try:
            me = await self.bot.get_me()
            username = me.username or ""
            link = f"https://t.me/{username}" if username else ""
            with SessionLocal() as db:
                set_bot_meta(db, self.bot_id, username, link)
                set_bot_running(db, self.bot_id, True, None)
        except TelegramUnauthorizedError:
            with SessionLocal() as db:
                old_token_id = db.execute(
                    select(BotInstance.token_id).where(BotInstance.id == self.bot_id)
                ).scalars().first()
                if old_token_id:
                    mark_token_status(db, old_token_id, "banned")
                await replace_dead_token(db, self.bot_id, banned_old_token_id=old_token_id)
            return await self.restart_with_new_token()

        self.task = asyncio.create_task(
            self.dp.start_polling(self.bot, allowed_updates=["message", "callback_query"])
        )

    async def restart_with_new_token(self):
        with SessionLocal() as db:
            token_value = db.execute(
                select(Token.token)
                .join(BotInstance, BotInstance.token_id == Token.id)
                .where(BotInstance.id == self.bot_id)
            ).scalars().first()
            if not token_value:
                return
            self.token = token_value
        if self.bot:
            await self.bot.session.close()
        self.bot = Bot(self.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = Dispatcher()
        setup_handlers(self.dp, self.bot_id)
        self.task = asyncio.create_task(
            self.dp.start_polling(self.bot, allowed_updates=["message", "callback_query"])
        )

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.dp:
            await self.dp.storage.close()
        if self.bot:
            await self.bot.session.close()
        with SessionLocal() as db:
            set_bot_running(db, self.bot_id, False, None)
