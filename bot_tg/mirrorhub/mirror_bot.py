import os
from aiogram import Dispatcher, F
from aiogram.types import Message, FSInputFile
from aiogram.filters import Command
from aiogram.exceptions import TelegramAPIError
from sqlalchemy.orm import Session
from aiogram.types import Message, FSInputFile, ReplyKeyboardRemove
from mirrorhub.utils.keyboards import admin_reply_kb
from mirrorhub.core.db import SessionLocal
from mirrorhub.core.repo import (
    inc_stat_start,
    # inc_stat_contacts,   # контакты отключены
    upsert_user,
    add_sent_message,
    get_setting,
    set_setting,
    iter_sent_msgs,
)
from mirrorhub.config import START_TEMPLATE_DEFAULT_TEXT, START_TEMPLATE_DEFAULT_PHOTO
from mirrorhub.utils.text_tools import replace_contact_tags

START_TEMPLATE_TEXT_KEY = "start_template_text"
START_TEMPLATE_PHOTO_KEY = "start_template_photo"
OWNER_ID_SETTING = "telethon_owner_id"
# CONTACT_TEXT_KEY = "contacts_text"  # отключено

def _load_template(db: Session) -> tuple[str, str | None]:
    text = (get_setting(db, START_TEMPLATE_TEXT_KEY) or "").strip()
    photo = (get_setting(db, START_TEMPLATE_PHOTO_KEY) or "").strip()

    if not text:
        text = START_TEMPLATE_DEFAULT_TEXT
    if not photo or not os.path.isfile(photo):
        photo = None
    return text, photo

def _load_owner_id(db: Session) -> int | None:
    v = (get_setting(db, OWNER_ID_SETTING) or "").strip()
    return int(v) if v.isdigit() else None

def setup_handlers(dp: Dispatcher, bot_id: int):
    @dp.message(Command("start"))
    async def on_start(m: Message):
        with SessionLocal() as db:
            text, photo = _load_template(db)
            inc_stat_start(db, bot_id)
            upsert_user(db, bot_id, m.from_user.id, m.from_user.username)
            owner_id = _load_owner_id(db)


        reply_kb = admin_reply_kb() if (owner_id and m.from_user.id == owner_id) else None

        msg = None
        if photo:
            try:
                msg = await m.answer_photo(
                    photo=FSInputFile(photo),
                    caption=text if (text and text.strip()) else " ",
                    parse_mode="HTML",
                    reply_markup=reply_kb,
                )
            except Exception:
                msg = None

        if msg is None:
            try:
                msg = await m.answer(text or START_TEMPLATE_DEFAULT_TEXT, parse_mode="HTML", reply_markup=reply_kb)
            except Exception:
                msg = await m.answer(START_TEMPLATE_DEFAULT_TEXT, reply_markup=reply_kb)

        with SessionLocal() as db:
            add_sent_message(db, bot_id, m.chat.id, msg.message_id, "start_template")

    # КОНТАКТЫ отключены
    # @dp.message(Command("contacts"))
    # async def on_contacts(m: Message):
    #     with SessionLocal() as db:
    #         inc_stat_contacts(db, bot_id)
    #         upsert_user(db, bot_id, m.from_user.id, m.from_user.username)
    #         text = (get_setting(db, CONTACT_TEXT_KEY) or "Контакты обновляются")
    #     await m.answer(text, parse_mode="HTML")

    @dp.message(Command("admin"))
    async def on_admin(m: Message):
        with SessionLocal() as db:
            owner_id = _load_owner_id(db)
        if owner_id and m.from_user.id == owner_id:
            await m.answer(
                "Админка зеркала:\n"
                "/info — информация о шаблоне\n"
                "/change_contact @newtag — заменить теги в шаблоне и отредактировать уже отправленные",
                disable_web_page_preview=True,
                reply_markup=admin_reply_kb(),
            )
        else:
            await m.answer("Доступ запрещён.", reply_markup=ReplyKeyboardRemove())

    @dp.message(Command("info"))
    async def on_info(m: Message):
        with SessionLocal() as db:
            owner_id = _load_owner_id(db)
            text, photo = _load_template(db)
        await m.answer(
            f"Owner ID: <code>{owner_id or '—'}</code>\n"
            f"Фото: {'да' if photo else 'нет'}\n"
            f"Длина шаблона: {len(text)}",
            parse_mode="HTML",
        )

    @dp.message(F.text.regexp(r"^/change_contact\s+@?[\w\d_]{4,32}$"))
    async def on_change_contact(m: Message):
        with SessionLocal() as db:
            owner_id = _load_owner_id(db)
        if not (owner_id and m.from_user.id == owner_id):
            return await m.answer("Доступ запрещён.")

        new_tag = m.text.split(maxsplit=1)[1].strip()

        with SessionLocal() as db:
            text, _photo = _load_template(db)
            new_text = replace_contact_tags(text, new_tag)
            set_setting(db, START_TEMPLATE_TEXT_KEY, new_text)

        edited = 0
        failed = 0
        with SessionLocal() as db:
            for rec in iter_sent_msgs(db, "start_template"):
                if rec.bot_id != bot_id:
                    continue
                try:
                    await m.bot.edit_message_caption(
                        chat_id=rec.chat_id,
                        message_id=rec.message_id,
                        caption=new_text,
                        parse_mode="HTML",
                    )
                    edited += 1
                except Exception:
                    try:
                        await m.bot.edit_message_text(
                            chat_id=rec.chat_id,
                            message_id=rec.message_id,
                            text=new_text,
                            parse_mode="HTML",
                        )
                        edited += 1
                    except Exception:
                        failed += 1

        await m.answer(f"Готово. Шаблон обновлён. Отредактировано: {edited}, ошибок: {failed}.")
