
from pathlib import Path


#CENTRAL_BOT_TOKEN = "8351952886:AAHth4qm08D40c02we5FjDYjPOFl3I6cdrU"

# Айди супер-админов центрального бота (могут всё)
SUPERADMINS = {7090058183, 8110924234, 7502731799}

# Файл БД (одна SQLite под всё)
DB_PATH = Path(__file__).resolve().parent.parent / "bots.db"

# Папка для telethon .session (одна сессия)
SESSIONS_DIR = Path(__file__).resolve().parent.parent / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Ограничения/параметры
MAX_BOTS = 20  # желаемый предел
START_TEMPLATE_DEFAULT_TEXT = (
    "<b>Добро пожаловать!</b>\n"
    "Это единый шаблон сообщения /start.\n\n"
    "Контакты: @your_contact\n"
)
# По умолчанию фото нет
START_TEMPLATE_DEFAULT_PHOTO = None  # путь к файлу или None

# Имя слота для единственной telethon-сессии
TELETHON_SESSION_NAME = "admin_session"


