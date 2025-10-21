
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def admin_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить токены (мульти)", callback_data="adm:add_tokens")
    kb.button(text="🎫 Токены", callback_data="adm:tokens")
    kb.button(text="🤖 Боты", callback_data="adm:bots")
    kb.button(text="🖼 Шаблон /start", callback_data="adm:template")
    kb.button(text="📣 Рассылка", callback_data="adm:broadcast")
    kb.button(text="📊 Статистика", callback_data="adm:stats")
    kb.button(text="🔐 Telethon", callback_data="adm:telethon")
    kb.button(text="🔁 Шаблон оповещения", callback_data="adm:swap_template")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup()

def bots_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Создать бота из пула", callback_data="bots:create")
    kb.button(text="📃 Список ботов", callback_data="bots:list")
    kb.button(text="▶️ Запустить все", callback_data="bots:start_all")
    kb.button(text="⏹ Остановить все", callback_data="bots:stop_all")
    kb.button(text="🗑 Удалить все", callback_data="bots:delete_all")
    kb.adjust(2, 2, 1)
    return kb.as_markup()

def bot_row_kb(bot_id: int, is_running: bool):
    kb = InlineKeyboardBuilder()
    if is_running:
        kb.button(text="⏹ Стоп", callback_data=f"bot:stop:{bot_id}")
    else:
        kb.button(text="▶️ Старт", callback_data=f"bot:start:{bot_id}")
    kb.button(text="🗑 Удалить", callback_data=f"bot:delete:{bot_id}")
    kb.adjust(2)
    return kb.as_markup()

def tokens_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить", callback_data="tok:refresh")
    kb.button(text="🗑 Удалить токены (ID)", callback_data="tok:delete")
    kb.adjust(2)
    return kb.as_markup()


def admin_reply_kb() -> ReplyKeyboardMarkup:

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛠 Админка")]],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        selective=True,
        input_field_placeholder="Команды админки",
    )

def telethon_menu_kb(has_session: bool):

    kb = InlineKeyboardBuilder()
    if has_session:
        kb.button(text="🔄 Перелогиниться", callback_data="tel:login")
        kb.button(text="🗑 Удалить сессию", callback_data="tel:remove")
        kb.adjust(2)
    else:
        kb.button(text="🔐 Выполнить вход", callback_data="tel:login")
        kb.adjust(1)
    return kb.as_markup()