
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from sqlalchemy.orm import Session

from mirrorhub.core.models import BotInstance, Token
from mirrorhub.core.repo import next_free_token, mark_token_status
from mirrorhub.config import CENTRAL_BOT_TOKEN, SUPERADMINS
from mirrorhub.core.db import SessionLocal

from mirrorhub.core.repo import get_setting  # для чтения шаблона
from mirrorhub.utils.text_tools import replace_link_placeholder

REPLACE_NOTIFY_TEMPLATE_KEY = "replace_notify_text"
REPLACE_NOTIFY_DEFAULT = "Новые контакты:\n*Ссылка*"

async def _notify_superadmins(text: str):
    try:
        bot = Bot(CENTRAL_BOT_TOKEN, default=DefaultBotProperties())
        for admin_id in SUPERADMINS:
            try:
                await bot.send_message(admin_id, text, parse_mode="HTML")
            except Exception:
                pass
        await bot.session.close()
    except Exception:
        pass

async def probe_token(bot_token: str) -> tuple[bool, str | None, str | None]:
    try:
        bot = Bot(bot_token, default=DefaultBotProperties())
        me = await bot.get_me()
        username = me.username or ""
        link = f"https://t.me/{username}" if username else ""
        await bot.session.close()
        return True, username, link
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"

async def _broadcast_replacement_to_running_bots(new_bot_link: str, text_html: str):
    from aiogram.types import FSInputFile  # не используется (на будущее)
    from mirrorhub.core.repo import list_bots, get_bot_users
    from mirrorhub.core.models import Token as TokenModel

    with SessionLocal() as db:
        bots = [b for b in list_bots(db) if b.is_running]


    if not bots:
        return


    for b in bots:
        with SessionLocal() as db:
            tok_obj = db.get(TokenModel, b.token_id) if b.token_id else None
            users = get_bot_users(db, b.id)

        if not tok_obj or not users:
            continue

        cli = Bot(token=tok_obj.token)
        try:
            for u in users:
                try:
                    await cli.send_message(chat_id=u.user_id, text=text_html, parse_mode="HTML")
                except Exception:
                    pass
        finally:
            await cli.session.close()

async def replace_dead_token(db: Session, bot_id: int, banned_old_token_id: int | None = None):
    token_row = next_free_token(db)
    if not token_row:
        bot = db.get(BotInstance, bot_id)
        if bot:
            bot.is_running = False
            bot.last_error = "Нет свободных токенов в пуле"
            db.commit()
        await _notify_superadmins(f"⚠️ Бот #{bot_id}: нет свободных токенов для замены.")
        return False, "Пул пуст"

    ok, username, link = await probe_token(token_row.token)
    if not ok:
        mark_token_status(db, token_row.id, "dead")
        return await replace_dead_token(db, bot_id, banned_old_token_id=banned_old_token_id)

    bot = db.get(BotInstance, bot_id)
    if not bot:
        return False, "BotInstance not found"


    bot.token_id = token_row.id
    bot.username = username or ""
    bot.link = link or ""
    bot.is_running = True
    bot.last_error = None
    mark_token_status(db, token_row.id, "in_use")
    db.commit()


    info_old = f" (старый token_id={banned_old_token_id})" if banned_old_token_id else ""
    await _notify_superadmins(
        f"🚨 Бот #{bot_id}: токен забанен{info_old}. "
        f"Подключён новый token_id={token_row.id} → @{bot.username or '—'} {bot.link or ''}"
    )


    with SessionLocal() as sdb:
        tmpl = get_setting(sdb, REPLACE_NOTIFY_TEMPLATE_KEY) or REPLACE_NOTIFY_DEFAULT
    link_text = bot.link or "(ссылка недоступна)"
    text_html = replace_link_placeholder(tmpl, link_text)


    await _broadcast_replacement_to_running_bots(link_text, text_html)
    return True, username or ""
