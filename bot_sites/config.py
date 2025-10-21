# mirrorhub/config.py
from pathlib import Path

# === ОБЩЕЕ ===
PROJECT_NAME = "MirrorHub"
TIMEZONE = "Europe/Moscow"  # внутреннее форматирование времени в уведомлениях

# === АДМИНЫ БОТА ===
ADMINS = {
    7090058183, 7000570228, 8021151828, 8110924234, 7502731799   # ← замени на свои user_id
}

# === TELEGRAM ===
BOT_TOKEN = "8132920101:AAHjBMsB4rDhxnie9XsCODWZCxqpHhI-BAw"     # ← замени на рабочий токен

# === БЭКЕНД САЙТА (ВАЖНО) ===
# На этот адрес Nginx будет проксировать ЗЕРКАЛА в режиме "active".
# Если uvicorn/fastapi у тебя слушает :8080 — оставь по умолчанию.
# Если у тебя порт 8000, поменяй на "http://127.0.0.1:8000".
BACKEND_UPSTREAM = "http://127.0.0.1:8080"

# (Необязательно, но удобно для unit-файла сервиса сайта)
SITE_HOST = "127.0.0.1"
SITE_PORT = 8080

# === ПУТИ / ФАЙЛЫ ПРОЕКТА ===
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

SQLITE_PATH = DATA_DIR / "mirrorhub.sqlite3"

# === NGINX (ТОЛЬКО ДЛЯ MIRRORHUB; ОСНОВНОЙ ПРОЕКТ НЕ ТРОГАЕМ) ===
NGINX_SITE_MAIN        = Path("/etc/nginx/sites-available/mirrorhub.conf")
NGINX_SITE_LINK        = Path("/etc/nginx/sites-enabled/mirrorhub.conf")
NGINX_PER_DOMAIN_DIR   = Path("/etc/nginx/sites-mirrorhub.d")
NGINX_AUX_DIR          = Path("/etc/nginx/mirrorhub")
NGINX_DOMAINS_MAP_FILE = NGINX_AUX_DIR / "domains_map.conf"  # строки вида: "example.com 1;"

# Отдельный JSON-лог только для зеркал MirrorHub
NGINX_LOG_FILE     = Path("/var/log/nginx/mirrorhub_redirect.log")
NGINX_REDIRECT_LOG = NGINX_LOG_FILE  # алиас для совместимости c кодом статистики

# Опциональный сниппет с TLS-параметрами (если существует — будет подключён)
NGINX_SSL_PARAMS_SNIPPET = Path("/etc/nginx/snippets/ssl-params.conf")

# === CERTBOT ===
LETSENCRYPT_EMAIL = "natarusk@gmail.com"   # ← укажи свою почту

# === МОНИТОРИНГ ===
BLOCK_FAIL_THRESHOLD = 3        # после N подряд фейлов считаем зеркало заблокированным
MONITOR_INTERVAL_SEC = 300      # 5 минут

# === ДЕФОЛТ-КОНТЕНТ ЛЕНДИНГА ===
DEFAULT_CONTENT = {
    "title": "Наши контакты",
    "subtitle": "Свяжитесь с нами в Telegram:",
    "contacts": [
        {"label": "Менеджер 1", "url": "https://t.me/manager_one"},
        {"label": "Менеджер 2", "url": "https://t.me/manager_two"},
    ],
    "footer": "© 2025. Все права защищены.",
}

# === ЭКСПОРТ СТАТИСТИКИ ===
EXPORT_DIR = BASE_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
