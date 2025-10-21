
import asyncio
import re
from typing import Dict
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, FSInputFile
from aiogram.types import Message, CallbackQuery, FSInputFile, ReplyKeyboardRemove
from mirrorhub.utils.keyboards import admin_menu_kb, bots_menu_kb, bot_row_kb, tokens_menu_kb, telethon_menu_kb, admin_reply_kb
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from mirrorhub.core.models import Token, BotInstance
from mirrorhub.config import CENTRAL_BOT_TOKEN, SUPERADMINS
from mirrorhub.core.repo import (
    add_token, add_tokens_bulk, list_tokens, list_bots, create_bot_instance, get_bot_users,
    add_broadcast_log, set_setting, get_setting, delete_bot_completely,
    aggregate_stats, next_free_token, delete_tokens_by_ids, set_bot_running
)
from mirrorhub.core.db import SessionLocal
from mirrorhub.utils.keyboards import admin_menu_kb, bots_menu_kb, bot_row_kb, tokens_menu_kb
from mirrorhub.telethon_manager import TelethonManager, OWNER_ID_SETTING
from mirrorhub.mirror_runner import MirrorRunner

RUNNERS: dict[int, MirrorRunner] = {}
_WAITERS: Dict[int, asyncio.Future[Message]] = {}
r = Router()


def admin_only():
    async def _check(m: Message):
        if not m.from_user:
            return False
        if m.from_user.id in SUPERADMINS:
            return True
        with SessionLocal() as db:
            owner = get_setting(db, OWNER_ID_SETTING)
        return owner is not None and str(m.from_user.id) == owner
    return _check


@r.message(Command("start"))
async def start_cmd(m: Message):
    if m.from_user and (m.from_user.id in SUPERADMINS):
        await m.answer("Центральная админка. Нажми «🛠 Админка».", reply_markup=admin_reply_kb())
    else:
        await m.answer("Доступ запрещён.", reply_markup=ReplyKeyboardRemove())


@r.message(Command("ping"))
async def ping_cmd(m: Message):
    await m.answer("pong")

@r.message(Command("admin"), admin_only())
async def admin_menu(m: Message):
    await m.answer("Выбери раздел:", reply_markup=admin_menu_kb())
    try:
        await m.answer("Клавиатура для админа активна.", reply_markup=admin_reply_kb())
    except Exception:
        pass


async def wait_for_next_message(chat_id: int) -> Message:
    fut: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
    _WAITERS[chat_id] = fut
    return await fut


TOKEN_RE = re.compile(r'^\d{6,12}:[A-Za-z0-9_-]{30,}$')

@r.callback_query(F.data == "adm:add_tokens")
async def cb_add_tokens(c: CallbackQuery):
    await c.message.answer(
        "Пришли <b>СПИСКОМ</b> токены (по одному в строке). "
        "Лишние пустые строки игнорируются."
    )
    await c.answer()
    await wait_and_add_tokens(c.message)

async def wait_and_add_tokens(msg: Message):
    reply = await wait_for_next_message(msg.chat.id)
    lines = [ln.strip() for ln in (reply.text or "").splitlines()]

    tokens = [ln for ln in lines if TOKEN_RE.match(ln)]
    if not tokens:
        return await msg.answer("Не нашёл валидных токенов в сообщении.")
    with SessionLocal() as db:
        added, skipped = add_tokens_bulk(db, tokens)
    txt = f"Добавлено: {added}"
    if skipped:
        txt += f"\nПропущено (возможны дубли/ошибки): {len(skipped)}"
    await msg.answer(txt)

@r.callback_query(F.data.in_(("adm:tokens", "tok:refresh")))
async def cb_tokens(c: CallbackQuery):

    with SessionLocal() as db:
        toks = list_tokens(db)
        used = {b.token_id: b for b in list_bots(db)}
    if not toks:
        await c.message.answer("Токенов нет.", reply_markup=tokens_menu_kb())
        return await c.answer()

    def row_line(t: Token) -> str:
        b = used.get(t.id)
        used_by = f"used_by: #{b.id} @{b.username or '—'}" if b else "used_by: —"
        link = f" {b.link}" if b and b.link else ""
        note = f" | {t.note}" if t.note else ""
        return f"#{t.id} • {t.status} | {used_by}{link} | created: {t.created_at:%Y-%m-%d %H:%M}{note}"

    CH = 30
    header = f"Всего: {len(toks)}"
    await c.message.answer(header, reply_markup=tokens_menu_kb())
    lines = [row_line(t) for t in toks]
    for i in range(0, len(lines), CH):
        await c.message.answer("\n".join(lines[i:i+CH]))
    await c.answer()

@r.callback_query(F.data == "tok:delete")
async def tok_delete(c: CallbackQuery):
    await c.message.answer("Пришли ID токенов для удаления (через пробел/строки). В использовании — не удаляются.")
    await c.answer()
    await wait_tok_delete(c.message)

async def wait_tok_delete(msg: Message):
    reply = await wait_for_next_message(msg.chat.id)
    raw = (reply.text or "")
    ids = []
    for part in re.findall(r'\d+', raw):
        try:
            ids.append(int(part))
        except ValueError:
            pass
    if not ids:
        return await msg.answer("Не нашёл ID.")
    with SessionLocal() as db:
        deleted, skipped = delete_tokens_by_ids(db, ids)
    txt = f"Удалено: {deleted}"
    if skipped:
        txt += f"\nПропущены (in_use): {', '.join(map(str, skipped))}"
    await msg.answer(txt)


@r.callback_query(F.data == "adm:bots")
async def cb_bots(c: CallbackQuery):
    await c.message.answer("Управление ботами:", reply_markup=bots_menu_kb())
    await c.answer()

@r.callback_query(F.data == "bots:list")
async def cb_bots_list(c: CallbackQuery):
    with SessionLocal() as db:
        bots = list_bots(db)
    if not bots:
        await c.message.answer("Ботов нет.")
        return await c.answer()

    for b in bots:
        state = "🟢" if b.is_running else "🔴"
        text = f"{state} #{b.id} @{b.username or '—'} {b.link or ''}\n" \
               f"starts={b.starts}, contacts={b.contacts_clicks}"
        await c.message.answer(text, reply_markup=bot_row_kb(b.id, b.is_running))
    await c.answer()

@r.callback_query(F.data == "bots:create")
async def cb_bot_create(c: CallbackQuery):
    with SessionLocal() as db:
        tok = next_free_token(db)
        if not tok:
            await c.message.answer("Нет свободных токенов.")
            return await c.answer()
        b = create_bot_instance(db, tok)
    await c.message.answer(f"Создан бот #{b.id}. Запускаю…")
    await c.answer()
    await start_runner(b.id)

@r.callback_query(F.data == "bots:start_all")
async def cb_start_all(c: CallbackQuery):
    with SessionLocal() as db:
        for b in list_bots(db):
            await start_runner(b.id)
    await c.message.answer("Все боты запущены.")
    await c.answer()

@r.callback_query(F.data == "bots:stop_all")
async def cb_stop_all(c: CallbackQuery):
    for bid, runner in list(RUNNERS.items()):
        await runner.stop()
        RUNNERS.pop(bid, None)
    await c.message.answer("Все боты остановлены.")
    await c.answer()

@r.callback_query(F.data == "bots:delete_all")
async def cb_delete_all(c: CallbackQuery):
    # остановить всё и удалить
    for bid, runner in list(RUNNERS.items()):
        await runner.stop()
        RUNNERS.pop(bid, None)
    with SessionLocal() as db:
        bots = list_bots(db)
        for b in bots:
            delete_bot_completely(db, b.id)
    await c.message.answer("Все боты удалены.")
    await c.answer()

@r.callback_query(F.data.regexp(r"^bot:stop:(\d+)$"))
async def cb_bot_stop(c: CallbackQuery):
    bot_id = int(c.data.split(":")[2])
    rnr = RUNNERS.get(bot_id)
    if rnr:
        await rnr.stop()
        RUNNERS.pop(bot_id, None)
    with SessionLocal() as db:
        set_bot_running(db, bot_id, False, None)
    await c.message.edit_reply_markup(reply_markup=bot_row_kb(bot_id, False))
    await c.answer("Остановлен.")

@r.callback_query(F.data.regexp(r"^bot:start:(\d+)$"))
async def cb_bot_start(c: CallbackQuery):
    bot_id = int(c.data.split(":")[2])
    await start_runner(bot_id)
    await c.message.edit_reply_markup(reply_markup=bot_row_kb(bot_id, True))
    await c.answer("Запущен.")

@r.callback_query(F.data.regexp(r"^bot:delete:(\d+)$"))
async def cb_bot_delete(c: CallbackQuery):
    bot_id = int(c.data.split(":")[2])
    rnr = RUNNERS.get(bot_id)
    if rnr:
        await rnr.stop()
        RUNNERS.pop(bot_id, None)
    with SessionLocal() as db:
        delete_bot_completely(db, bot_id)
    await c.message.edit_text(f"Бот #{bot_id} удалён.")
    await c.answer()

async def start_runner(bot_id: int):
    with SessionLocal() as db:
        token_value = db.execute(
            select(Token.token)
            .join(BotInstance, BotInstance.token_id == Token.id)
            .where(BotInstance.id == bot_id)
        ).scalars().first()
        if not token_value:
            return
        token = token_value
    if bot_id in RUNNERS:
        return
    rnr = MirrorRunner(bot_id, token)
    RUNNERS[bot_id] = rnr
    await rnr.start()


@r.callback_query(F.data == "adm:template")
async def cb_template(c: CallbackQuery):

    with SessionLocal() as db:
        text = get_setting(db, "start_template_text") or ""
        photo = get_setting(db, "start_template_photo") or ""
    if photo:
        try:
            await c.message.answer_photo(FSInputFile(photo), caption=text or " ")
        except Exception:
            await c.message.answer(text or "(шаблон пуст)")
    else:
        await c.message.answer(text or "(шаблон пуст)")

    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Изменить шаблон", callback_data="adm:template:edit")
    kb.adjust(1)
    await c.message.answer("Текущий шаблон выше. Нажми, чтобы заменить:", reply_markup=kb.as_markup())
    await c.answer()

from aiogram.utils.keyboard import InlineKeyboardBuilder

@r.callback_query(F.data == "adm:template:edit")
async def cb_template_edit(c: CallbackQuery):
    await c.message.answer(
        "Отправь новый шаблон:\n"
        "— текст (HTML), либо\n"
        "— фото с подписью (caption=HTML)\n"
        "Можно без фото."
    )
    await c.answer()
    await wait_template_update(c.message)

async def wait_template_update(msg: Message):
    reply = await wait_for_next_message(msg.chat.id)

    photo_path = None

    if reply.photo:

        file = await reply.bot.get_file(reply.photo[-1].file_id)
        photo_path = f"sessions/start_photo_{file.file_unique_id}.jpg"
        await reply.bot.download_file(file.file_path, photo_path)

        text = (
            getattr(reply, "html_caption", None)
            or getattr(reply, "caption_html", None)
            or reply.caption
            or ""
        )
    else:

        text = getattr(reply, "html_text", None) or (reply.text or "")

    with SessionLocal() as db:
        set_setting(db, "start_template_text", text)
        set_setting(db, "start_template_photo", photo_path)

    await msg.answer("Шаблон и фото сохранены." if photo_path else "Шаблон сохранён.")



from aiogram.utils.keyboard import InlineKeyboardBuilder
from mirrorhub.utils.text_tools import replace_link_placeholder

REPLACE_NOTIFY_TEMPLATE_KEY = "replace_notify_text"
REPLACE_NOTIFY_DEFAULT = "Новые контакты:\n*Ссылка*"

@r.callback_query(F.data == "adm:swap_template")
async def cb_swap_template(c: CallbackQuery):

    with SessionLocal() as db:
        tmpl = get_setting(db, REPLACE_NOTIFY_TEMPLATE_KEY) or REPLACE_NOTIFY_DEFAULT

    instr = (
        "Это шаблон оповещения, которое рассылается <b>всем активным зеркалам</b> "
        "после автозамены бота при бане.\n\n"
        "• Используйте плейсхолдер <code>*Ссылка*</code> — он будет заменён на URL нового бота.\n"
        "• Поддерживается HTML-форматирование (жирный, курсив, ссылки и т.д.).\n\n"
        "<i>Пример:</i>\n"
        "Новые контакты:\n"
        "<code>*Ссылка*</code>\n"
    )

    await c.message.answer("Текущий шаблон оповещения:\n\n" + tmpl, parse_mode="HTML")
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Изменить шаблон оповещения", callback_data="adm:swap_template:edit")
    kb.adjust(1)
    await c.message.answer(instr, parse_mode="HTML", reply_markup=kb.as_markup())
    await c.answer()

@r.callback_query(F.data == "adm:swap_template:edit")
async def cb_swap_template_edit(c: CallbackQuery):
    await c.message.answer(
        "Пришли <b>новый текст</b> оповещения (HTML разрешён).\n\n"
        "Не забудь плейсхолдер <code>*Ссылка*</code> — туда подставится ссылка нового бота.",
        parse_mode="HTML"
    )
    await c.answer()
    await wait_swap_template_update(c.message)

async def wait_swap_template_update(msg: Message):
    reply = await wait_for_next_message(msg.chat.id)

    new_text = getattr(reply, "html_text", None) or (reply.text or "")
    with SessionLocal() as db:
        set_setting(db, REPLACE_NOTIFY_TEMPLATE_KEY, new_text)
    await msg.answer("Шаблон оповещения сохранён.")


@r.message(F.text == "🛠 Админка")
async def admin_button_open(m: Message):

    if not m.from_user:
        return
    if m.from_user.id in SUPERADMINS:
        return await admin_menu(m)
    with SessionLocal() as db:
        owner = get_setting(db, OWNER_ID_SETTING)
    if owner and str(m.from_user.id) == owner:
        return await admin_menu(m)
    await m.answer("Доступ запрещён.", reply_markup=ReplyKeyboardRemove())



@r.callback_query(F.data == "adm:broadcast")
async def cb_broadcast(c: CallbackQuery):
    await c.message.answer(
        "Пришли рассылку:\n"
        "— текст (HTML) или\n"
        "— фото с подписью.\n"
        "Рассылка идёт всем пользователям всех запущенных ботов."
    )
    await c.answer()
    await wait_broadcast(c.message)

async def wait_broadcast(msg: Message):

    reply = await wait_for_next_message(msg.chat.id)


    photo_path: str | None = None
    if reply.photo:

        file = await reply.bot.get_file(reply.photo[-1].file_id)
        photo_path = f"sessions/broadcast_{file.file_unique_id}.jpg"
        await reply.bot.download_file(file.file_path, photo_path)

        text = (
            getattr(reply, "html_caption", None)
            or getattr(reply, "caption_html", None)
            or reply.caption
            or ""
        )
    else:

        text = getattr(reply, "html_text", None) or (reply.text or "")


    import asyncio
    from aiogram import Bot as AioBot
    from aiogram.types import FSInputFile
    from mirrorhub.core.db import SessionLocal
    from mirrorhub.core.repo import list_bots, get_bot_users, add_broadcast_log
    from mirrorhub.core.models import Token

    total = ok = fail = 0


    with SessionLocal() as db:
        bots = list_bots(db)

    for b in bots:

        with SessionLocal() as db:
            tok_obj = db.get(Token, b.token_id) if b.token_id else None
            users = get_bot_users(db, b.id)

        if not tok_obj or not users:
            continue

        cli = AioBot(token=tok_obj.token)
        try:

            for u in users:
                total += 1
                try:
                    if photo_path:
                        await cli.send_photo(
                            chat_id=u.user_id,
                            photo=FSInputFile(photo_path),
                            caption=text,
                            parse_mode="HTML",
                        )
                    else:
                        await cli.send_message(
                            chat_id=u.user_id,
                            text=text,
                            parse_mode="HTML",
                        )
                    ok += 1
                except Exception:
                    fail += 1

                    await asyncio.sleep(0.02)
        finally:
            await cli.session.close()


    with SessionLocal() as db:
        add_broadcast_log(db, text, photo_path, total, ok, fail)

    await msg.answer(f"Готово. Отправлено: {ok}/{total}. Ошибок: {fail}.")



@r.callback_query(F.data == "adm:telethon")
async def cb_telethon(c: CallbackQuery):
    tm = TelethonManager()
    st = await tm.status()
    if st["session_exists"]:
        owner_txt = f"Владелец: <code>{st['owner_id']}</code> (@{st['username'] or '—'})\n" \
                    f"Авторизован: {'да' if st['authorized'] else 'нет'}"
        await c.message.answer("Сессия Telethon обнаружена.\n" + owner_txt)
        kb = InlineKeyboardBuilder()
        kb.button(text="🔄 Перелогиниться", callback_data="tel:login")
        kb.button(text="🗑 Удалить сессию", callback_data="tel:remove")
        kb.adjust(2)
        await c.message.answer("Действия:", reply_markup=kb.as_markup())
    else:
        await c.message.answer("Сессия Telethon не найдена.")
        kb = InlineKeyboardBuilder()
        kb.button(text="🔐 Выполнить вход", callback_data="tel:login")
        kb.adjust(1)
        await c.message.answer("Действия:", reply_markup=kb.as_markup())
    await c.answer()

@r.callback_query(F.data == "tel:login")
async def tel_login(c: CallbackQuery):
    tm = TelethonManager()
    async def wait_input():
        m = await wait_for_next_message(c.message.chat.id)
        return (m.text or "").strip()
    async def send_text(text: str):
        await c.message.answer(text)
    tm._wait_input = wait_input  # type: ignore
    await tm.login_dialog(send_text)
    await c.answer()

@r.callback_query(F.data == "tel:remove")
async def tel_remove(c: CallbackQuery):
    tm = TelethonManager()
    await tm.remove_session()
    await c.message.answer("Сессия Telethon удалена.")
    await c.answer()

@r.message()
async def _catch_any(m: Message):
    fut = _WAITERS.pop(m.chat.id, None)
    if fut and not fut.done():
        fut.set_result(m)


def _stats_period_kb(selected: str = "all"):
    kb = InlineKeyboardBuilder()
    labels = {
        "all": "📈 За всё",
        "7d": "🗓 За 7 дней",
        "1d": "📅 За день",
    }
    for key in ("all", "7d", "1d"):
        title = labels[key] + (" •" if key == selected else "")
        kb.button(text=title, callback_data=f"adm:stats:{key}")
    kb.adjust(3)
    return kb.as_markup()

def _render_stats_text(period_key: str = "all") -> str:
    days_map = {"all": None, "7d": 7, "1d": 1}
    title_map = {"all": "за всё время", "7d": "за 7 дней", "1d": "за 1 день"}
    days = days_map.get(period_key, None)
    title = title_map.get(period_key, "за всё время")

    from mirrorhub.core.repo import list_bots, get_total_users_period, get_user_counts_by_bot_period
    with SessionLocal() as db:
        bots = list_bots(db)
        total_users = get_total_users_period(db, days)
        users_by_bot = get_user_counts_by_bot_period(db, days)

    lines = [f"👥 ПОЛЬЗОВАТЕЛИ ({title}): <b>{total_users}</b>", f"🤖 Ботов: {len(bots)}"]
    if bots:
        lines.append("\nПо ботам:")
        for b in bots:
            state = "🟢" if b.is_running else "🔴"
            users = users_by_bot.get(b.id, 0)
            at = f"@{b.username}" if b.username else "—"  # БЕЗ t.me/ссылок
            lines.append(f"{state} #{b.id} {at} — ПОЛЬЗОВАТЕЛИ={users}")
    return "\n".join(lines)

@r.callback_query(F.data == "adm:stats")
async def cb_stats_default(c: CallbackQuery):
    await c.message.answer(_render_stats_text("all"), parse_mode="HTML", reply_markup=_stats_period_kb("all"))
    await c.answer()

@r.callback_query(F.data.regexp(r"^adm:stats:(all|7d|1d)$"))
async def cb_stats_period(c: CallbackQuery):
    period_key = c.data.split(":")[2]
    await c.message.answer(_render_stats_text(period_key), parse_mode="HTML", reply_markup=_stats_period_kb(period_key))
    await c.answer()


def get_dp():
    """Возвращает диспетчер для использования в unified_bot"""
    return dp

# Сохраняем оригинальный dp
original_dp = dp
