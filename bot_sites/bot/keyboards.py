# bot/keyboards.py
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def admin_reply_kb() -> ReplyKeyboardMarkup:
    # Кнопка админки на нижней панели (постоянно)
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛠 Админка")]],
        resize_keyboard=True
    )


def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="🌐 Зеркала", callback_data="mir:menu")
    kb.button(text="✍️ Контент", callback_data="cnt:menu")
    kb.button(text="📈 Статистика", callback_data="st:menu")
    kb.adjust(2, 1)
    return kb.as_markup()


# -------- Зеркала --------

def mirrors_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить домены", callback_data="mir:add")
    kb.button(text="🔐 Выпустить SSL (все без SSL)", callback_data="mir:ssl_all")
    kb.button(text="✅ Включить ВСЕ", callback_data="mir:enable_all")
    kb.button(text="⛔️ Выключить ВСЕ", callback_data="mir:disable_all")
    kb.button(text="📋 Список", callback_data="mir:list")
    kb.adjust(1, 1, 2, 1)
    return kb.as_markup()


def domain_row(host: str, status: str, ssl_ok: bool):
    kb = InlineKeyboardBuilder()
    state = "🟢 ON" if status == "active" else ("🟡 Ожидает" if status == "hot" else "🔴 OFF")
    ssl = "🔐" if ssl_ok else "🧩"
    kb.button(text=f"{ssl} {host} — {state}", callback_data="noop")
    if status == "active":
        kb.button(text="⛔️ Выключить", callback_data=f"mir:disable:{host}")
    else:
        kb.button(text="✅ Включить", callback_data=f"mir:enable:{host}")
    kb.button(text="🗑 Удалить", callback_data=f"mir:del:{host}")
    kb.adjust(1, 2)
    return kb.as_markup()


# -------- Контент --------

def content_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="👤 Менеджеры", callback_data="cnt:managers")
    kb.button(text="👀 Превью", callback_data="cnt:preview")
    kb.adjust(1, 1)
    return kb.as_markup()



def contacts_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить", callback_data="cnt:contacts:add")
    kb.button(text="✏️ Изменить", callback_data="cnt:contacts:edit")
    kb.button(text="🗑 Удалить", callback_data="cnt:contacts:del")
    kb.button(text="📤 Экспорт", callback_data="cnt:contacts:export")
    kb.button(text="📥 Импорт (JSON)", callback_data="cnt:contacts:import")
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def managers_menu(current_one: str | None, current_two: str | None):
    """
    Меню менеджеров: две кнопки для замены username.
    current_one/two — текущее значение без '@' (или None).
    """
    one_disp = f"@{current_one}" if current_one else "—"
    two_disp = f"@{current_two}" if current_two else "—"
    kb = InlineKeyboardBuilder()
    kb.button(text=f"👤 Менеджер 1: {one_disp}", callback_data="cnt:man:set:1")
    kb.button(text=f"👤 Менеджер 2: {two_disp}", callback_data="cnt:man:set:2")
    kb.adjust(1)
    return kb.as_markup()


# -------- Статистика --------

def stats_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 7 дней", callback_data="st:range:7")
    kb.button(text="🗓 30 дней", callback_data="st:range:30")
    kb.button(text="🧮 Всё", callback_data="st:range:all")
    kb.button(text="📤 Экспорт CSV", callback_data="st:export")
    kb.adjust(2, 2)
    return kb.as_markup()
