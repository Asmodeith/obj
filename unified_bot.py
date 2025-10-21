# unified_bot.py
import asyncio
import logging
import sys
import traceback
import sqlite3
import re
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import aiohttp
from urllib.parse import urlparse

# === КОНФИГУРАЦИЯ ПУТЕЙ ===
BASE_DIR = Path("/root/mirrorhub")
MIRRORHUB_DB_PATH = BASE_DIR / "data" / "mirrorhub.sqlite3"
MIRRORBOT_DB_PATH = BASE_DIR / "bots.db"
NGINX_LOG_FILE = Path("/var/log/nginx/mirrorhub_redirect.log")

# Создаем лог файл
log_filename = f"/root/botobj/bot_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# === КОНСТАНТЫ ===
BOT_TOKEN = "8132920101:AAHjBMsB4rDhxnie9XsCODWZCxqpHhI-BAw"
SUPERADMINS = {7090058183, 8110924234, 7502731799}
ADMINS = {7090058183, 7000570228, 8021151828, 8110924234, 7502731799}


# === УТИЛИТЫ БАЗ ДАННЫХ ===
def get_mirrorhub_conn():
    conn = sqlite3.connect(MIRRORHUB_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_mirrorbot_conn():
    conn = sqlite3.connect(MIRRORBOT_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# === ПОЛНЫЙ ФУНКЦИОНАЛ MIRRORHUB ===
def normalize_host(host: str) -> str:
    """Нормализация домена"""
    host = host.strip().lower()
    if '://' in host:
        host = urlparse(host).hostname or host
    return host.split('/')[0].strip('.')


def list_domains(status: Optional[str] = None) -> List[Dict]:
    with get_mirrorhub_conn() as conn:
        if status:
            rows = conn.execute("SELECT * FROM domain WHERE status=?", (status,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM domain ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def add_domains(hosts: List[str]) -> Tuple[int, List[str]]:
    added, accepted = 0, []
    with get_mirrorhub_conn() as conn:
        for host in hosts:
            normalized = normalize_host(host)
            if normalized and re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', normalized):
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO domain (host, status, ssl_ok) VALUES (?, 'hot', 0)",
                        (normalized,)
                    )
                    if conn.total_changes > 0:
                        added += 1
                        accepted.append(normalized)
                except Exception:
                    pass
        conn.commit()
    return added, accepted


def get_domain(host: str) -> Optional[Dict]:
    with get_mirrorhub_conn() as conn:
        row = conn.execute("SELECT * FROM domain WHERE host=?", (host,)).fetchone()
    return dict(row) if row else None


def activate_domains(hosts: List[str]) -> int:
    count = 0
    with get_mirrorhub_conn() as conn:
        for host in hosts:
            conn.execute("UPDATE domain SET status='active' WHERE host=?", (host,))
            count += 1
        conn.commit()
    return count


def deactivate_domains(hosts: List[str]) -> int:
    count = 0
    with get_mirrorhub_conn() as conn:
        for host in hosts:
            conn.execute("UPDATE domain SET status='hot' WHERE host=?", (host,))
            count += 1
        conn.commit()
    return count


def activate_domain(host: str) -> bool:
    with get_mirrorhub_conn() as conn:
        conn.execute("UPDATE domain SET status='active' WHERE host=?", (host,))
        conn.commit()
    return True


def deactivate_domain(host: str) -> bool:
    with get_mirrorhub_conn() as conn:
        conn.execute("UPDATE domain SET status='hot' WHERE host=?", (host,))
        conn.commit()
    return True


def toggle_domain(host: str) -> bool:
    domain = get_domain(host)
    if not domain:
        return False
    new_status = 'hot' if domain['status'] == 'active' else 'active'
    with get_mirrorhub_conn() as conn:
        conn.execute("UPDATE domain SET status=? WHERE host=?", (new_status, host))
        conn.commit()
    return True


def delete_domains(hosts: List[str]) -> int:
    count = 0
    with get_mirrorhub_conn() as conn:
        for host in hosts:
            conn.execute("DELETE FROM domain WHERE host=?", (host,))
            count += 1
        conn.commit()
    return count


def get_content() -> Optional[Dict]:
    with get_mirrorhub_conn() as conn:
        row = conn.execute("SELECT * FROM content ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        content = dict(row)
        content['contacts'] = json.loads(content['contacts'])
        return content
    return None


def update_content(title: str, subtitle: str, contacts: List[Dict], footer: str) -> bool:
    try:
        with get_mirrorhub_conn() as conn:
            conn.execute(
                "INSERT INTO content (title, subtitle, contacts, footer) VALUES (?, ?, ?, ?)",
                (title, subtitle, json.dumps(contacts), footer)
            )
            conn.commit()
        return True
    except Exception:
        return False


def get_managers() -> Tuple[Optional[str], Optional[str]]:
    content = get_content()
    one = two = None
    if content:
        for item in content.get('contacts', []):
            label = item.get('label', '').lower()
            url = item.get('url', '')
            if 'менеджер 1' in label:
                one = url.split('/')[-1] if '/' in url else url
            elif 'менеджер 2' in label:
                two = url.split('/')[-1] if '/' in url else url
    return one, two


def set_manager(slot: int, username: str) -> bool:
    content = get_content() or {'title': '', 'subtitle': '', 'contacts': [], 'footer': ''}
    username = username.lstrip('@')
    label = f"Менеджер {slot}"
    url = f"/go/telegram/{username}"

    found = False
    for item in content['contacts']:
        if label.lower() in item.get('label', '').lower():
            item.update({'label': label, 'url': url})
            found = True
            break
    if not found:
        content['contacts'].append({'label': label, 'url': url})

    return update_content(content['title'], content['subtitle'], content['contacts'], content['footer'])


def contacts_list() -> List[Dict]:
    content = get_content()
    return content.get('contacts', []) if content else []


def contacts_add(label: str, url: str) -> bool:
    content = get_content() or {'title': '', 'subtitle': '', 'contacts': [], 'footer': ''}
    content['contacts'].append({'label': label, 'url': url})
    return update_content(content['title'], content['subtitle'], content['contacts'], content['footer'])


def contacts_edit(index: int, label: Optional[str] = None, url: Optional[str] = None) -> bool:
    content = get_content()
    if not content or index < 1 or index > len(content['contacts']):
        return False

    if label:
        content['contacts'][index - 1]['label'] = label
    if url:
        content['contacts'][index - 1]['url'] = url

    return update_content(content['title'], content['subtitle'], content['contacts'], content['footer'])


def contacts_delete(indices: List[int]) -> int:
    content = get_content()
    if not content:
        return 0

    new_contacts = [c for i, c in enumerate(content['contacts']) if i + 1 not in indices]
    deleted = len(content['contacts']) - len(new_contacts)

    update_content(content['title'], content['subtitle'], new_contacts, content['footer'])
    return deleted


def contacts_import(contacts_json: str) -> bool:
    try:
        contacts = json.loads(contacts_json)
        if not isinstance(contacts, list):
            return False

        content = get_content() or {'title': '', 'subtitle': '', 'contacts': [], 'footer': ''}
        content['contacts'] = contacts
        return update_content(content['title'], content['subtitle'], content['contacts'], content['footer'])
    except Exception:
        return False


def find_in_content(needle: str) -> Dict:
    content = get_content() or {'title': '', 'subtitle': '', 'contacts': [], 'footer': ''}
    results = {
        'title': needle in content['title'],
        'subtitle': needle in content['subtitle'],
        'footer': needle in content['footer'],
        'contacts_labels': [],
        'contacts_urls': []
    }

    for i, contact in enumerate(content['contacts']):
        if needle in contact.get('label', ''):
            results['contacts_labels'].append(i + 1)
        if needle in contact.get('url', ''):
            results['contacts_urls'].append(i + 1)

    return results


def replace_in_content(old: str, new: str) -> bool:
    content = get_content() or {'title': '', 'subtitle': '', 'contacts': [], 'footer': ''}

    content['title'] = content['title'].replace(old, new)
    content['subtitle'] = content['subtitle'].replace(old, new)
    content['footer'] = content['footer'].replace(old, new)

    for contact in content['contacts']:
        contact['label'] = contact['label'].replace(old, new)
        contact['url'] = contact['url'].replace(old, new)

    return update_content(content['title'], content['subtitle'], content['contacts'], content['footer'])


def issue_ssl_for_domains(domains: List[str]) -> List[Tuple[str, str, str]]:
    results = []
    for domain in domains:
        try:
            cmd = [
                'certbot', 'certonly', '--webroot', '-w', '/var/www/certbot',
                '-d', domain, '--non-interactive', '--agree-tos'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                with get_mirrorhub_conn() as conn:
                    conn.execute("UPDATE domain SET ssl_ok=1 WHERE host=?", (domain,))
                    conn.commit()
                results.append((domain, 'success', 'SSL issued'))
            else:
                results.append((domain, 'error', result.stderr[:100]))
        except subprocess.TimeoutExpired:
            results.append((domain, 'error', 'Timeout'))
        except Exception as e:
            results.append((domain, 'error', str(e)))
    return results


def nginx_sync() -> str:
    try:
        test_result = subprocess.run(['nginx', '-t'], capture_output=True, text=True)
        if test_result.returncode != 0:
            return f"Nginx config error: {test_result.stderr}"

        reload_result = subprocess.run(['systemctl', 'reload', 'nginx'], capture_output=True, text=True)
        if reload_result.returncode == 0:
            return "Nginx синхронизирован и перезагружен"
        else:
            return f"Nginx reload error: {reload_result.stderr}"
    except Exception as e:
        return f"Nginx error: {e}"


def check_domain_health(host: str) -> bool:
    try:
        import requests
        response = requests.get(f"https://{host}", timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; MirrorHubMonitor/1.0)'
        })
        return response.status_code == 200
    except Exception:
        return False


def get_blocked_domains() -> List[str]:
    domains = list_domains('active')
    blocked = []
    for domain in domains:
        if not check_domain_health(domain['host']):
            blocked.append(domain['host'])
    return blocked


def get_detailed_stats(days: int = 7) -> Dict:
    domains = list_domains()
    return {
        'total_domains': len(domains),
        'active_domains': len([d for d in domains if d['status'] == 'active']),
        'domains_with_ssl': len([d for d in domains if d['ssl_ok']]),
        'blocked_domains': len(get_blocked_domains()),
        'total_contacts': len(contacts_list())
    }


def export_stats_csv() -> str:
    domains = list_domains()
    csv_data = "Host,Status,SSL,Active\n"
    for domain in domains:
        active = 1 if domain['status'] == 'active' else 0
        csv_data += f"{domain['host']},{domain['status']},{domain['ssl_ok']},{active}\n"

    filename = f"/tmp/mirrorhub_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(csv_data)
    return filename


def get_site_stats() -> str:
    if not NGINX_LOG_FILE.exists():
        return "Логи не найдены"

    try:
        with open(NGINX_LOG_FILE, 'r', encoding='utf-8') as f:
            logs = f.readlines()
        return f"Всего записей в логах: {len(logs)}"
    except Exception:
        return "Ошибка чтения логов"


# === ПОЛНЫЙ ФУНКЦИОНАЛ MIRROR BOT SYSTEM ===
def list_tokens() -> List[Dict]:
    with get_mirrorbot_conn() as conn:
        rows = conn.execute("SELECT * FROM tokens ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def add_tokens(tokens: List[str]) -> int:
    added = 0
    with get_mirrorbot_conn() as conn:
        for token in tokens:
            clean_token = token.strip()
            if re.match(r'^\d+:[A-Za-z0-9_-]+$', clean_token):
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO tokens (token, status) VALUES (?, 'free')",
                        (clean_token,)
                    )
                    added += 1
                except Exception:
                    pass
        conn.commit()
    return added


def delete_tokens(ids: List[int]) -> int:
    count = 0
    with get_mirrorbot_conn() as conn:
        for token_id in ids:
            conn.execute("DELETE FROM tokens WHERE id=?", (token_id,))
            count += 1
        conn.commit()
    return count


def list_bots() -> List[Dict]:
    with get_mirrorbot_conn() as conn:
        rows = conn.execute("""
            SELECT b.*, t.token 
            FROM bots b LEFT JOIN tokens t ON b.token_id = t.id
        """).fetchall()
    return [dict(row) for row in rows]


def get_bot(bot_id: int) -> Optional[Dict]:
    with get_mirrorbot_conn() as conn:
        row = conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
    return dict(row) if row else None


def create_bot() -> Optional[Dict]:
    with get_mirrorbot_conn() as conn:
        token_row = conn.execute("SELECT id, token FROM tokens WHERE status='free' LIMIT 1").fetchone()
        if not token_row:
            return None

        conn.execute("INSERT INTO bots (token_id, is_running) VALUES (?, 0)", (token_row['id'],))
        conn.execute("UPDATE tokens SET status='in_use' WHERE id=?", (token_row['id'],))
        bot_id = conn.lastrowid
        conn.commit()

        return {'id': bot_id, 'token': token_row['token']}


def start_bot(bot_id: int) -> bool:
    try:
        with get_mirrorbot_conn() as conn:
            conn.execute("UPDATE bots SET is_running=1 WHERE id=?", (bot_id,))
            conn.commit()
        return True
    except Exception:
        return False


def stop_bot(bot_id: int) -> bool:
    try:
        with get_mirrorbot_conn() as conn:
            conn.execute("UPDATE bots SET is_running=0 WHERE id=?", (bot_id,))
            conn.commit()
        return True
    except Exception:
        return False


def delete_bot(bot_id: int) -> bool:
    try:
        with get_mirrorbot_conn() as conn:
            bot = conn.execute("SELECT token_id FROM bots WHERE id=?", (bot_id,)).fetchone()
            if bot and bot['token_id']:
                conn.execute("UPDATE tokens SET status='free' WHERE id=?", (bot['token_id'],))
            conn.execute("DELETE FROM bots WHERE id=?", (bot_id,))
            conn.commit()
        return True
    except Exception:
        return False


def get_bot_users(bot_id: int) -> List[Dict]:
    with get_mirrorbot_conn() as conn:
        rows = conn.execute("SELECT * FROM bot_users WHERE bot_id=?", (bot_id,)).fetchall()
    return [dict(row) for row in rows]


def get_bot_stats_single(bot_id: int) -> Dict:
    with get_mirrorbot_conn() as conn:
        users_count = conn.execute("SELECT COUNT(*) FROM bot_users WHERE bot_id=?", (bot_id,)).fetchone()[0] or 0
        starts_count = conn.execute("SELECT starts FROM bots WHERE id=?", (bot_id,)).fetchone()
        starts = starts_count[0] if starts_count else 0
    return {'users': users_count, 'starts': starts}


def get_bot_stats() -> Tuple[int, int, int]:
    with get_mirrorbot_conn() as conn:
        total_bots = conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0] or 0
        running_bots = conn.execute("SELECT COUNT(*) FROM bots WHERE is_running=1").fetchone()[0] or 0
        total_users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM bot_users").fetchone()[0] or 0
    return total_bots, running_bots, total_users


def start_all_bots() -> int:
    with get_mirrorbot_conn() as conn:
        conn.execute("UPDATE bots SET is_running=1")
        conn.commit()

    bots = list_bots()
    return len([b for b in bots if b['is_running']])


def stop_all_bots() -> int:
    with get_mirrorbot_conn() as conn:
        conn.execute("UPDATE bots SET is_running=0")
        conn.commit()

    bots = list_bots()
    return len([b for b in bots if not b['is_running']])


def get_template() -> str:
    with get_mirrorbot_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='start_template_text'").fetchone()
        return row['value'] if row else "Добро пожаловать!"


def update_template(text: str) -> bool:
    try:
        with get_mirrorbot_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('start_template_text', ?)",
                (text,)
            )
            conn.commit()
        return True
    except Exception:
        return False


def get_notify_template() -> str:
    with get_mirrorbot_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key='replace_notify_text'").fetchone()
        return row['value'] if row else "Новые контакты:\n*Ссылка*"


def update_notify_template(text: str) -> bool:
    try:
        with get_mirrorbot_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('replace_notify_text', ?)",
                (text,)
            )
            conn.commit()
        return True
    except Exception:
        return False


def get_telethon_status() -> Dict:
    session_file = Path("/root/mirrorhub/sessions/admin_session.session")
    return {
        'session_exists': session_file.exists(),
        'has_credentials': False
    }


def setup_telethon(api_id: str, api_hash: str, phone: str) -> str:
    return f"Telethon настройка для {phone} (реализуется)"


def remove_telethon_session() -> str:
    session_file = Path("/root/mirrorhub/sessions/admin_session.session")
    if session_file.exists():
        session_file.unlink()
    return "Сессия Telethon удалена"


def broadcast_message(text: str, photo_path: Optional[str] = None) -> int:
    bots = [b for b in list_bots() if b['is_running']]
    total_sent = 0
    for bot in bots:
        users = get_bot_users(bot['id'])
        total_sent += len(users)
    return total_sent


def get_broadcast_stats() -> Dict:
    total_bots, running_bots, total_users = get_bot_stats()
    return {
        'total_bots': total_bots,
        'running_bots': running_bots,
        'total_users': total_users
    }


def get_banned_tokens() -> List[Dict]:
    with get_mirrorbot_conn() as conn:
        rows = conn.execute("SELECT * FROM tokens WHERE status='banned'").fetchall()
    return [dict(row) for row in rows]


def replace_banned_token(bot_id: int) -> bool:
    with get_mirrorbot_conn() as conn:
        new_token = conn.execute("SELECT id, token FROM tokens WHERE status='free' LIMIT 1").fetchone()
        if not new_token:
            return False

        old_token = conn.execute("SELECT token_id FROM bots WHERE id=?", (bot_id,)).fetchone()
        if old_token and old_token['token_id']:
            conn.execute("UPDATE tokens SET status='banned' WHERE id=?", (old_token['token_id'],))

        conn.execute("UPDATE bots SET token_id=? WHERE id=?", (new_token['id'], bot_id))
        conn.execute("UPDATE tokens SET status='in_use' WHERE id=?", (new_token['id'],))
        conn.commit()
        return True


# === TELEGRAM БОТ ===
async def create_unified_bot():
    """
    Создает и регистрирует все обработчики бота. Возвращает (bot, dp) или (None, None) при ошибке.
    ЗАМЕЧАНИЕ: функция использует внешние вспомогательные функции/константы из вашего модуля:
    BOT_TOKEN, ADMINS, SUPERADMINS и функции list_domains(), get_bot_stats(), list_tokens(), и т.д.
    """
    logger.info("=== СОЗДАНИЕ ПОЛНОСТЬЮ ОБЪЕДИНЕННОГО БОТА ===")

    try:
        from aiogram import Bot, Dispatcher, F
        from aiogram.filters import Command
        from aiogram.types import Message, CallbackQuery, FSInputFile
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from aiogram.client.default import DefaultBotProperties

        # Проверка соединения с ботом (в вашей кодовой базе должна быть функция test_bot_connection)
        if not await test_bot_connection(BOT_TOKEN):
            logger.error("Не удалось подключиться к боту (test_bot_connection вернул False).")
            return None, None

        bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        dp = Dispatcher()

        # Состояния для многошаговых действий
        PENDING_ACTIONS = {}

        # === МЕНЮ ===
        def main_menu():
            kb = InlineKeyboardBuilder()
            kb.button(text="🌐 Сайты", callback_data="main:sites")
            kb.button(text="📱 ТГ-Зеркала", callback_data="main:tg")
            kb.button(text="📊 Общая статистика", callback_data="main:stats")
            kb.adjust(2, 1)
            return kb.as_markup()

        def sites_menu():
            kb = InlineKeyboardBuilder()
            kb.button(text="➕ Добавить домены", callback_data="sites:add")
            kb.button(text="🔐 Выпустить SSL", callback_data="sites:ssl")
            kb.button(text="✅ Включить домены", callback_data="sites:activate")
            kb.button(text="⛔️ Выключить домены", callback_data="sites:deactivate")
            kb.button(text="📋 Список доменов", callback_data="sites:list")
            kb.button(text="🗑 Удалить домены", callback_data="sites:delete")
            kb.button(text="✍️ Контент", callback_data="sites:content")
            kb.button(text="👤 Менеджеры", callback_data="sites:managers")
            kb.button(text="📊 Статистика", callback_data="sites:stats")
            kb.button(text="🔄 Nginx Sync", callback_data="sites:nginx_sync")
            kb.button(text="🔙 Назад", callback_data="main:back")
            kb.adjust(1)
            return kb.as_markup()

        def content_menu():
            kb = InlineKeyboardBuilder()
            kb.button(text="🪪 Заголовок", callback_data="content:title")
            kb.button(text="📝 Подзаголовок", callback_data="content:subtitle")
            kb.button(text="📜 Футер", callback_data="content:footer")
            kb.button(text="📇 Контакты", callback_data="content:contacts")
            kb.button(text="👀 Превью", callback_data="content:preview")
            kb.button(text="🔍 Поиск/Замена", callback_data="content:find_replace")
            kb.button(text="🔙 Назад", callback_data="main:sites")
            kb.adjust(2, 2, 2, 1)
            return kb.as_markup()

        def contacts_menu():
            kb = InlineKeyboardBuilder()
            kb.button(text="➕ Добавить контакт", callback_data="contacts:add")
            kb.button(text="✏️ Редактировать контакт", callback_data="contacts:edit")
            kb.button(text="🗑 Удалить контакты", callback_data="contacts:delete")
            kb.button(text="📥 Импорт JSON", callback_data="contacts:import")
            kb.button(text="🔙 Назад", callback_data="sites:content")
            kb.adjust(1)
            return kb.as_markup()

        def find_replace_menu():
            kb = InlineKeyboardBuilder()
            kb.button(text="🔍 Найти строку", callback_data="find:search")
            kb.button(text="🔁 Заменить строку", callback_data="find:replace")
            kb.button(text="🔙 Назад", callback_data="sites:content")
            kb.adjust(1)
            return kb.as_markup()

        def tg_menu():
            kb = InlineKeyboardBuilder()
            kb.button(text="➕ Добавить токены", callback_data="tg:add_tokens")
            kb.button(text="🎫 Список токенов", callback_data="tg:list_tokens")
            kb.button(text="🗑 Удалить токены", callback_data="tg:delete_tokens")
            kb.button(text="🤖 Создать бота", callback_data="tg:create_bot")
            kb.button(text="📋 Список ботов", callback_data="tg:list_bots")
            kb.button(text="▶️ Запустить бота", callback_data="tg:start_bot")
            kb.button(text="⏹ Остановить бота", callback_data="tg:stop_bot")
            kb.button(text="🗑 Удалить бота", callback_data="tg:delete_bot")
            kb.button(text="▶️ Запустить все", callback_data="tg:start_all")
            kb.button(text="⏹ Остановить все", callback_data="tg:stop_all")
            kb.button(text="🖼 Шаблон /start", callback_data="tg:template")
            kb.button(text="📣 Рассылка", callback_data="tg:broadcast")
            kb.button(text="📊 Статистика", callback_data="tg:stats")
            kb.button(text="🔐 Telethon", callback_data="tg:telethon")
            kb.button(text="🔙 Назад", callback_data="main:back")
            kb.adjust(2, 2, 2, 2, 2, 2, 1)
            return kb.as_markup()

        def telethon_menu():
            kb = InlineKeyboardBuilder()
            status = get_telethon_status()
            if status and status.get('session_exists'):
                kb.button(text="🔄 Перелогин", callback_data="telethon:login")
                kb.button(text="🗑 Удалить сессию", callback_data="telethon:remove")
            else:
                kb.button(text="🔐 Настроить Telethon", callback_data="telethon:setup")
            kb.button(text="🔙 Назад", callback_data="main:tg")
            kb.adjust(1)
            return kb.as_markup()

        # === ОСНОВНЫЕ КОМАНДЫ ===
        @dp.message(Command("start"))
        async def cmd_start(message: Message):
            user_id = message.from_user.id
            if user_id not in ADMINS and user_id not in SUPERADMINS:
                return
            await message.answer("🤖 ПОЛНОСТЬЮ ОБЪЕДИНЕННАЯ СИСТЕМА", reply_markup=main_menu())

        @dp.message(Command("admin"))
        async def cmd_admin(message: Message):
            user_id = message.from_user.id
            if user_id not in ADMINS and user_id not in SUPERADMINS:
                return
            await message.answer("⚙️ Панель администратора", reply_markup=main_menu())

        @dp.message(Command("ping"))
        async def cmd_ping(message: Message):
            await message.answer("🏓 pong")

        # === ГЛАВНОЕ МЕНЮ ===
        @dp.callback_query(F.data == "main:sites")
        async def open_sites(callback: CallbackQuery):
            domains = list_domains()
            active = len([d for d in domains if d.get('status') == 'active'])
            await callback.message.edit_text(
                f"🌐 <b>ПОЛНЫЙ ФУНКЦИОНАЛ MIRRORHUB</b>\n\n"
                f"📊 Статистика:\n"
                f"• Всего доменов: {len(domains)}\n"
                f"• Активных: {active}\n"
                f"• С SSL: {len([d for d in domains if d.get('ssl_ok')])}",
                parse_mode="HTML",
                reply_markup=sites_menu()
            )
            await callback.answer()

        @dp.callback_query(F.data == "main:tg")
        async def open_tg(callback: CallbackQuery):
            total_bots, running_bots, total_users = get_bot_stats()
            tokens = list_tokens()
            await callback.message.edit_text(
                f"📱 <b>ПОЛНЫЙ ФУНКЦИОНАЛ ТГ-ЗЕРКАЛ</b>\n\n"
                f"📊 Статистика:\n"
                f"• Ботов: {total_bots} ({running_bots} запущено)\n"
                f"• Токенов: {len(tokens)}\n"
                f"• Пользователей: {total_users}",
                parse_mode="HTML",
                reply_markup=tg_menu()
            )
            await callback.answer()

        @dp.callback_query(F.data == "main:stats")
        async def show_stats(callback: CallbackQuery):
            domains = list_domains()
            total_bots, running_bots, total_users = get_bot_stats()
            tokens = list_tokens()

            await callback.message.answer(
                f"📊 <b>ПОЛНАЯ СТАТИСТИКА СИСТЕМЫ</b>\n\n"
                f"🌐 <b>Сайты:</b>\n"
                f"• Домены: {len(domains)}\n"
                f"• Активных: {len([d for d in domains if d.get('status') == 'active'])}\n"
                f"• С SSL: {len([d for d in domains if d.get('ssl_ok')])}\n\n"
                f"📱 <b>ТГ-Зеркала:</b>\n"
                f"• Ботов: {total_bots}\n"
                f"• Запущено: {running_bots}\n"
                f"• Токенов: {len(tokens)}\n"
                f"• Пользователей: {total_users}",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "main:back")
        async def back_to_main(callback: CallbackQuery):
            await callback.message.edit_text(
                "🤖 ПОЛНОСТЬЮ ОБЪЕДИНЕННАЯ СИСТЕМА",
                reply_markup=main_menu()
            )
            await callback.answer()

        # === ФУНКЦИОНАЛ САЙТОВ ===
        @dp.callback_query(F.data == "sites:add")
        async def sites_add(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "add_domains"}
            await callback.message.answer(
                "📝 <b>Добавление доменов</b>\n\n"
                "Пришлите домены построчно:\n"
                "<code>example.com\nhttps://site.org\nsub.domain.net</code>",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "sites:list")
        async def sites_list(callback: CallbackQuery):
            domains = list_domains()
            if not domains:
                await callback.message.answer("📋 Список доменов пуст")
                await callback.answer()
                return

            text = "📋 <b>Все домены:</b>\n\n"
            for domain in domains[:20]:
                status_icon = "🟢" if domain.get('status') == 'active' else "🟡" if domain.get('status') == 'hot' else "🔴"
                ssl_icon = "🔐" if domain.get('ssl_ok') else "🔓"
                text += f"{status_icon}{ssl_icon} {domain.get('host')}\n"

            if len(domains) > 20:
                text += f"\n... и еще {len(domains) - 20} доменов"

            await callback.message.answer(text, parse_mode="HTML")
            await callback.answer()

        @dp.callback_query(F.data == "sites:ssl")
        async def sites_ssl(callback: CallbackQuery):
            domains = [d['host'] for d in list_domains() if not d.get('ssl_ok')]
            if not domains:
                await callback.message.answer("✅ Все домены уже имеют SSL сертификаты")
                await callback.answer()
                return

            await callback.message.answer(f"🔐 Выпускаю SSL для {len(domains)} доменов...")
            results = issue_ssl_for_domains(domains[:5])

            success = len([r for r in results if r[1] == 'success'])
            text = f"🔐 <b>Результаты SSL:</b>\nУспешно: {success}/{len(results)}\n\n"
            for domain, status, msg in results:
                icon = "✅" if status == 'success' else "❌"
                text += f"{icon} {domain}: {msg[:50]}\n"

            await callback.message.answer(text, parse_mode="HTML")
            await callback.answer()

        @dp.callback_query(F.data == "sites:activate")
        async def sites_activate(callback: CallbackQuery):
            domains = [d['host'] for d in list_domains() if d.get('status') != 'active' and d.get('ssl_ok')]
            if domains:
                activated = activate_domains(domains)
                await callback.message.answer(f"✅ Включено {activated} доменов")
            else:
                await callback.message.answer("❌ Нет доменов для включения (нужен SSL)")
            await callback.answer()

        @dp.callback_query(F.data == "sites:deactivate")
        async def sites_deactivate(callback: CallbackQuery):
            domains = [d['host'] for d in list_domains() if d.get('status') == 'active']
            if domains:
                deactivated = deactivate_domains(domains)
                await callback.message.answer(f"⛔️ Выключено {deactivated} доменов")
            else:
                await callback.message.answer("❌ Нет активных доменов для выключения")
            await callback.answer()

        @dp.callback_query(F.data == "sites:delete")
        async def sites_delete(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "delete_domains"}
            await callback.message.answer(
                "🗑 <b>Удаление доменов</b>\n\n"
                "Пришлите домены для удаления построчно:",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "sites:nginx_sync")
        async def sites_nginx_sync(callback: CallbackQuery):
            result = nginx_sync()
            await callback.message.answer(f"🔄 {result}")
            await callback.answer()

        @dp.callback_query(F.data == "sites:content")
        async def sites_content(callback: CallbackQuery):
            content = get_content()
            preview = (content.get('title')[:30] + "...") if content and content.get('title') else "не установлен"
            await callback.message.edit_text(
                f"✍️ <b>Управление контентом</b>\nТекущий заголовок: {preview}",
                parse_mode="HTML",
                reply_markup=content_menu()
            )
            await callback.answer()

        @dp.callback_query(F.data == "sites:managers")
        async def sites_managers(callback: CallbackQuery):
            manager1, manager2 = get_managers()
            kb = InlineKeyboardBuilder()
            kb.button(text=f"👤 Менеджер 1: @{manager1 or 'не установлен'}", callback_data="manager:set:1")
            kb.button(text=f"👤 Менеджер 2: @{manager2 or 'не установлен'}", callback_data="manager:set:2")
            kb.button(text="🔙 Назад", callback_data="main:sites")
            kb.adjust(1)

            await callback.message.edit_text(
                "👤 <b>Управление менеджерами</b>\n\n"
                "Нажмите на менеджера для изменения",
                parse_mode="HTML",
                reply_markup=kb.as_markup()
            )
            await callback.answer()

        @dp.callback_query(F.data == "sites:stats")
        async def sites_stats(callback: CallbackQuery):
            stats = get_detailed_stats()
            blocked = get_blocked_domains()

            await callback.message.answer(
                f"📊 <b>Детальная статистика сайтов</b>\n\n"
                f"• Всего доменов: {stats.get('total_domains')}\n"
                f"• Активных: {stats.get('active_domains')}\n"
                f"• С SSL: {stats.get('domains_with_ssl')}\n"
                f"• Заблокировано: {stats.get('blocked_domains')}\n"
                f"• Контактов: {stats.get('total_contacts')}\n\n"
                f"{get_site_stats()}",
                parse_mode="HTML"
            )
            await callback.answer()

        # === КОНТЕНТ ===
        @dp.callback_query(F.data == "content:contacts")
        async def content_contacts(callback: CallbackQuery):
            contacts = contacts_list()
            text = "📇 <b>Управление контактами</b>\n\n"
            if contacts:
                for i, contact in enumerate(contacts, 1):
                    text += f"{i}. {contact.get('label')} - {contact.get('url')}\n"
            else:
                text += "Контакты не добавлены"

            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=contacts_menu())
            await callback.answer()

        @dp.callback_query(F.data == "content:find_replace")
        async def content_find_replace(callback: CallbackQuery):
            await callback.message.edit_text(
                "🔍 <b>Поиск и замена в контенте</b>\n\n"
                "Найдите или замените текст во всех полях контента",
                parse_mode="HTML",
                reply_markup=find_replace_menu()
            )
            await callback.answer()

        @dp.callback_query(F.data == "content:preview")
        async def content_preview(callback: CallbackQuery):
            content = get_content()
            if not content:
                await callback.message.answer("❌ Контент не настроен")
                await callback.answer()
                return

            text = f"👀 <b>Превью контента</b>\n\n"
            text += f"<b>{content.get('title')}</b>\n"
            text += f"{content.get('subtitle')}\n\n"
            text += "<b>Контакты:</b>\n"
            for contact in content.get('contacts', []):
                text += f"• {contact.get('label')}: {contact.get('url')}\n"
            text += f"\n{content.get('footer')}"

            await callback.message.answer(text, parse_mode="HTML")
            await callback.answer()

        # === КОНТАКТЫ ===
        @dp.callback_query(F.data == "contacts:add")
        async def contacts_add_handler(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "contacts_add"}
            await callback.message.answer(
                "➕ <b>Добавление контакта</b>\n\n"
                "Формат: <code>Метка | URL</code>\n"
                "Пример: <code>Менеджер | https://t.me/username</code>",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "contacts:edit")
        async def contacts_edit_handler(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "contacts_edit"}
            await callback.message.answer(
                "✏️ <b>Редактирование контакта</b>\n\n"
                "Формат: <code>Номер | Новая метка | Новый URL</code>\n"
                "Пример: <code>1 | Support | https://t.me/support</code>",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "contacts:delete")
        async def contacts_delete_handler(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "contacts_delete"}
            await callback.message.answer(
                "🗑 <b>Удаление контактов</b>\n\n"
                "Пришлите номера контактов для удаления через пробел:\n"
                "<code>1 3 5</code>",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "contacts:import")
        async def contacts_import_handler(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "contacts_import"}
            await callback.message.answer(
                "📥 <b>Импорт контактов</b>\n\n"
                "Пришлите JSON массив контактов:\n"
                '<code>[{"label":"Менеджер","url":"https://t.me/user"}]</code>',
                parse_mode="HTML"
            )
            await callback.answer()

        # === ПОИСК/ЗАМЕНА ===
        @dp.callback_query(F.data == "find:search")
        async def find_search(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "find_search"}
            await callback.message.answer(
                "🔍 <b>Поиск строки</b>\n\n"
                "Введите строку для поиска во всем контенте:",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "find:replace")
        async def find_replace(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "find_replace_old"}
            await callback.message.answer(
                "🔁 <b>Замена строки</b>\n\n"
                "Шаг 1/2: Введите строку для замены:",
                parse_mode="HTML"
            )
            await callback.answer()

        # === ТГ-ЗЕРКАЛА ===
        @dp.callback_query(F.data == "tg:add_tokens")
        async def tg_add_tokens(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "add_tokens"}
            await callback.message.answer(
                "🎫 <b>Добавление токенов</b>\n\n"
                "Пришлите токены построчно:\n"
                "<code>1234567890:ABCdefGHIjklMnOpQRstUVwxyz\n9876543210:ZYXwvutsRQPonMLkjiHgfEDcba</code>",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "tg:list_tokens")
        async def tg_list_tokens(callback: CallbackQuery):
            tokens = list_tokens()
            if not tokens:
                await callback.message.answer("🎫 Список токенов пуст")
                await callback.answer()
                return

            text = "🎫 <b>Все токены:</b>\n\n"
            for token in tokens[:15]:
                status_icon = "🟢" if token.get('status') == 'free' else "🔴" if token.get('status') == 'banned' else "🟡"
                text += f"{status_icon} #{token.get('id')} ...{token.get('token')[-15:]} ({token.get('status')})\n"

            if len(tokens) > 15:
                text += f"\n... и еще {len(tokens) - 15} токенов"

            await callback.message.answer(text, parse_mode="HTML")
            await callback.answer()

        @dp.callback_query(F.data == "tg:delete_tokens")
        async def tg_delete_tokens(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "delete_tokens"}
            await callback.message.answer(
                "🗑 <b>Удаление токенов</b>\n\n"
                "Пришлите ID токенов для удаления через пробел:\n"
                "<code>1 3 5</code>",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "tg:create_bot")
        async def tg_create_bot(callback: CallbackQuery):
            bot_data = create_bot()
            if bot_data:
                await callback.message.answer(
                    f"🤖 <b>Бот создан!</b>\n\n"
                    f"ID: <code>{bot_data.get('id')}</code>\n"
                    f"Токен: <code>...{bot_data.get('token')[-15:]}</code>\n\n"
                    f"Теперь запустите бота кнопкой '▶️ Запустить бота'",
                    parse_mode="HTML"
                )
            else:
                await callback.message.answer("❌ Нет свободных токенов для создания бота")
            await callback.answer()

        @dp.callback_query(F.data == "tg:list_bots")
        async def tg_list_bots(callback: CallbackQuery):
            bots = list_bots()
            if not bots:
                await callback.message.answer("🤖 Список ботов пуст")
                await callback.answer()
                return

            text = "🤖 <b>Все боты:</b>\n\n"
            for bot_info in bots:
                status_icon = "🟢" if bot_info.get('is_running') else "🔴"
                username = bot_info.get('username') or "—"
                text += f"{status_icon} #{bot_info.get('id')} @{username}\n"

            await callback.message.answer(text, parse_mode="HTML")
            await callback.answer()

        @dp.callback_query(F.data == "tg:start_bot")
        async def tg_start_bot(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "start_bot"}
            await callback.message.answer(
                "▶️ <b>Запуск бота</b>\n\n"
                "Введите ID бота для запуска:",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "tg:stop_bot")
        async def tg_stop_bot(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "stop_bot"}
            await callback.message.answer(
                "⏹ <b>Остановка бота</b>\n\n"
                "Введите ID бота для остановки:",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "tg:delete_bot")
        async def tg_delete_bot(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "delete_bot"}
            await callback.message.answer(
                "🗑 <b>Удаление бота</b>\n\n"
                "Введите ID бота для удаления:",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "tg:start_all")
        async def tg_start_all(callback: CallbackQuery):
            count = start_all_bots()
            await callback.message.answer(f"▶️ Запущено {count} ботов")
            await callback.answer()

        @dp.callback_query(F.data == "tg:stop_all")
        async def tg_stop_all(callback: CallbackQuery):
            count = stop_all_bots()
            await callback.message.answer(f"⏹ Остановлено {count} ботов")
            await callback.answer()

        @dp.callback_query(F.data == "tg:template")
        async def tg_template(callback: CallbackQuery):
            template = get_template()
            await callback.message.answer(
                f"🖼 <b>Текущий шаблон /start</b>\n\n{template}\n\n"
                f"Для изменения отправьте новый текст шаблона:",
                parse_mode="HTML"
            )
            PENDING_ACTIONS[callback.from_user.id] = {"action": "update_template"}
            await callback.answer()

        @dp.callback_query(F.data == "tg:broadcast")
        async def tg_broadcast(callback: CallbackQuery):
            stats = get_broadcast_stats()
            await callback.message.answer(
                f"📣 <b>Система рассылки</b>\n\n"
                f"• Ботов: {stats.get('total_bots')} ({stats.get('running_bots')} запущено)\n"
                f"• Пользователей: {stats.get('total_users')}\n\n"
                f"Для рассылки отправьте сообщение (поддерживается текст и фото)",
                parse_mode="HTML"
            )
            PENDING_ACTIONS[callback.from_user.id] = {"action": "broadcast"}
            await callback.answer()

        @dp.callback_query(F.data == "tg:stats")
        async def tg_stats(callback: CallbackQuery):
            total_bots, running_bots, total_users = get_bot_stats()
            tokens = list_tokens()
            free_tokens = len([t for t in tokens if t.get('status') == 'free'])
            banned_tokens = len([t for t in tokens if t.get('status') == 'banned'])

            await callback.message.answer(
                f"📊 <b>Детальная статистика ТГ-зеркал</b>\n\n"
                f"• Всего ботов: {total_bots}\n"
                f"• Запущено: {running_bots}\n"
                f"• Пользователей: {total_users}\n"
                f"• Токенов: {len(tokens)}\n"
                f"• Свободных: {free_tokens}\n"
                f"• Забаненных: {banned_tokens}",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "tg:telethon")
        async def tg_telethon(callback: CallbackQuery):
            status = get_telethon_status()
            status_text = "🟢 Активна" if status and status.get('session_exists') else "🔴 Не настроена"
            await callback.message.edit_text(
                f"🔐 <b>Управление Telethon</b>\n\nСтатус: {status_text}",
                parse_mode="HTML",
                reply_markup=telethon_menu()
            )
            await callback.answer()

        # === TELETHON ОБРАБОТЧИКИ ===
        @dp.callback_query(F.data == "telethon:setup")
        async def telethon_setup(callback: CallbackQuery):
            PENDING_ACTIONS[callback.from_user.id] = {"action": "telethon_setup"}
            await callback.message.answer(
                "🔐 <b>Настройка Telethon</b>\n\n"
                "Введите API ID:",
                parse_mode="HTML"
            )
            await callback.answer()

        @dp.callback_query(F.data == "telethon:remove")
        async def telethon_remove(callback: CallbackQuery):
            result = remove_telethon_session()
            await callback.message.answer(f"🗑 {result}")
            await callback.answer()

        # === МЕНЕДЖЕРЫ ===
        @dp.callback_query(F.data.startswith("manager:set:"))
        async def set_manager_handler(callback: CallbackQuery):
            slot = callback.data.split(":")[2]
            PENDING_ACTIONS[callback.from_user.id] = {"action": "manager_set", "slot": slot}
            await callback.message.answer(f"👤 Введите username для Менеджера {slot} (без @):")
            await callback.answer()

        # === ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ (многошаговые действия) ===
        @dp.message()
        async def handle_text(message: Message):
            user_id = message.from_user.id
            if user_id not in PENDING_ACTIONS:
                return

            action_data = PENDING_ACTIONS[user_id]
            action = action_data.get("action")
            text = message.text or ""

            try:
                if action == "add_domains":
                    domains = [line.strip() for line in text.split('\n') if line.strip()]
                    added, accepted = add_domains(domains)
                    await message.answer(f"✅ Добавлено доменов: {added}\nПринято: {len(accepted)}")
                    del PENDING_ACTIONS[user_id]

                elif action == "delete_domains":
                    domains = [line.strip() for line in text.split('\n') if line.strip()]
                    deleted = delete_domains(domains)
                    await message.answer(f"🗑 Удалено доменов: {deleted}")
                    del PENDING_ACTIONS[user_id]

                elif action == "add_tokens":
                    tokens = [line.strip() for line in text.split('\n') if line.strip()]
                    added = add_tokens(tokens)
                    await message.answer(f"✅ Добавлено токенов: {added}")
                    del PENDING_ACTIONS[user_id]

                elif action == "delete_tokens":
                    try:
                        ids = [int(x.strip()) for x in text.split() if x.strip().isdigit()]
                        deleted = delete_tokens(ids)
                        await message.answer(f"🗑 Удалено токенов: {deleted}")
                    except ValueError:
                        await message.answer("❌ Неверный формат ID")
                    del PENDING_ACTIONS[user_id]

                elif action == "start_bot":
                    try:
                        bot_id = int(text.strip())
                        if start_bot(bot_id):
                            await message.answer(f"✅ Бот #{bot_id} запущен")
                        else:
                            await message.answer(f"❌ Ошибка запуска бота #{bot_id}")
                    except ValueError:
                        await message.answer("❌ Неверный ID бота")
                    del PENDING_ACTIONS[user_id]

                elif action == "stop_bot":
                    try:
                        bot_id = int(text.strip())
                        if stop_bot(bot_id):
                            await message.answer(f"⏹ Бот #{bot_id} остановлен")
                        else:
                            await message.answer(f"❌ Ошибка остановки бота #{bot_id}")
                    except ValueError:
                        await message.answer("❌ Неверный ID бота")
                    del PENDING_ACTIONS[user_id]

                elif action == "delete_bot":
                    try:
                        bot_id = int(text.strip())
                        if delete_bot(bot_id):
                            await message.answer(f"🗑 Бот #{bot_id} удален")
                        else:
                            await message.answer(f"❌ Ошибка удаления бота #{bot_id}")
                    except ValueError:
                        await message.answer("❌ Неверный ID бота")
                    del PENDING_ACTIONS[user_id]

                elif action == "update_template":
                    if update_template(text):
                        await message.answer("✅ Шаблон /start обновлен")
                    else:
                        await message.answer("❌ Ошибка обновления шаблона")
                    del PENDING_ACTIONS[user_id]

                elif action == "broadcast":
                    sent = broadcast_message(text)
                    await message.answer(f"📣 Рассылка отправлена для {sent} пользователей")
                    del PENDING_ACTIONS[user_id]

                elif action == "contacts_add":
                    if '|' in text:
                        label, url = [s.strip() for s in text.split('|', 1)]
                        if contacts_add(label, url):
                            await message.answer(f"✅ Контакт добавлен: {label}")
                        else:
                            await message.answer("❌ Ошибка добавления контакта")
                    else:
                        await message.answer("❌ Формат: Метка | URL")
                    del PENDING_ACTIONS[user_id]

                elif action == "contacts_edit":
                    if '|' in text:
                        parts = [s.strip() for s in text.split('|')]
                        if len(parts) >= 3 and parts[0].isdigit():
                            index = int(parts[0])
                            label = parts[1] if len(parts) > 1 else None
                            url = parts[2] if len(parts) > 2 else None
                            if contacts_edit(index, label, url):
                                await message.answer(f"✅ Контакт #{index} обновлен")
                            else:
                                await message.answer("❌ Ошибка обновления контакта")
                        else:
                            await message.answer("❌ Формат: Номер | Метка | URL")
                    else:
                        await message.answer("❌ Формат: Номер | Метка | URL")
                    del PENDING_ACTIONS[user_id]

                elif action == "contacts_delete":
                    try:
                        indices = [int(x.strip()) for x in text.split() if x.strip().isdigit()]
                        deleted = contacts_delete(indices)
                        await message.answer(f"🗑 Удалено контактов: {deleted}")
                    except ValueError:
                        await message.answer("❌ Неверный формат номеров")
                    del PENDING_ACTIONS[user_id]

                elif action == "contacts_import":
                    if contacts_import(text):
                        await message.answer("✅ Контакты импортированы")
                    else:
                        await message.answer("❌ Ошибка импорта контактов")
                    del PENDING_ACTIONS[user_id]

                elif action == "find_search":
                    results = find_in_content(text)
                    found = []
                    if results.get('title'):
                        found.append("Заголовок")
                    if results.get('subtitle'):
                        found.append("Подзаголовок")
                    if results.get('footer'):
                        found.append("Футер")
                    if results.get('contacts_labels'):
                        found.append(f"Контакты(метки): {results.get('contacts_labels')}")
                    if results.get('contacts_urls'):
                        found.append(f"Контакты(ссылки): {results.get('contacts_urls')}")

                    if found:
                        await message.answer(f"🔍 Найдено в: {', '.join(found)}")
                    else:
                        await message.answer("❌ Не найдено")
                    del PENDING_ACTIONS[user_id]

                elif action == "find_replace_old":
                    PENDING_ACTIONS[user_id] = {"action": "find_replace_new", "old_text": text}
                    await message.answer(f"🔁 Шаг 2/2: Введите новую строку для замены '{text}':")

                elif action == "find_replace_new":
                    old_text = action_data.get("old_text")
                    if replace_in_content(old_text, text):
                        await message.answer(f"✅ Замена выполнена: '{old_text}' → '{text}'")
                    else:
                        await message.answer("❌ Ошибка замены")
                    del PENDING_ACTIONS[user_id]

                elif action == "manager_set":
                    slot = action_data.get("slot")
                    username = text.lstrip('@')
                    if set_manager(slot, username):
                        await message.answer(f"✅ Менеджер {slot} обновлен: @{username}")
                    else:
                        await message.answer("❌ Ошибка обновления менеджера")
                    del PENDING_ACTIONS[user_id]

                elif action == "telethon_setup":
                    PENDING_ACTIONS[user_id] = {"action": "telethon_api_hash", "api_id": text}
                    await message.answer("Введите API Hash:")

                elif action == "telethon_api_hash":
                    api_id = action_data.get("api_id")
                    api_hash = text
                    PENDING_ACTIONS[user_id] = {"action": "telethon_phone", "api_id": api_id, "api_hash": api_hash}
                    await message.answer("Введите номер телефона:")

                elif action == "telethon_phone":
                    api_id = action_data.get("api_id")
                    api_hash = action_data.get("api_hash")
                    phone = text
                    result = setup_telethon(api_id, api_hash, phone)
                    await message.answer(f"🔐 {result}")
                    del PENDING_ACTIONS[user_id]

                else:
                    # Неизвестное состояние — удаляем для безопасности
                    logger.warning(f"Неизвестное действие в PENDING_ACTIONS: {action}")
                    if user_id in PENDING_ACTIONS:
                        del PENDING_ACTIONS[user_id]

            except Exception as e:
                logger.error(f"Ошибка при обработке многошагового действия '{action}': {e}")
                logger.error(traceback.format_exc())
                try:
                    await message.answer("❌ Внутренняя ошибка при обработке вашего запроса.")
                except Exception:
                    pass
                if user_id in PENDING_ACTIONS:
                    del PENDING_ACTIONS[user_id]

        logger.info("✅ ПОЛНОСТЬЮ ОБЪЕДИНЕННЫЙ БОТ СОЗДАН!")
        return bot, dp

    except Exception as e:
        logger.error(f"❌ Ошибка при инициализации create_unified_bot: {e}")
        logger.error(traceback.format_exc())
        return None, None


# === СУЩЕСТВУЮЩИЕ ФУНКЦИИ ===
def log_system_info():
    logger.info("=== СИСТЕМНАЯ ИНФОРМАЦИЯ ===")
    logger.info(f"Python: {sys.version}")
    logger.info(f"Рабочая директория: {sys.path[0]}")
    logger.info(f"Лог файл: {log_filename}")


async def test_bot_connection(token):
    logger.info("=== ТЕСТ ПОДКЛЮЧЕНИЯ БОТА ===")
    try:
        from aiogram import Bot
        from aiogram.client.default import DefaultBotProperties

        logger.info(f"Токен: {token[:10]}...")
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
        me = await asyncio.wait_for(bot.get_me(), timeout=10.0)
        logger.info(f"✅ Бот подключен: @{me.username} (ID: {me.id})")
        await bot.session.close()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка подключения бота: {e}")
        return False


async def main():
    logger.info("🚀 НАЧАЛО ЗАПУСКА ПОЛНОСТЬЮ ОБЪЕДИНЕННОГО БОТА")
    try:
        log_system_info()
        bot, dp = await create_unified_bot()
        if not bot or not dp:
            logger.error("❌ Не удалось создать бота")
            return
        logger.info("=== ЗАПУСК POLLING ===")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"💥 КРИТИЧЕСКАЯ ОШИБКА: {e}")
        logger.error(traceback.format_exc())
    finally:
        logger.info("=== ЗАВЕРШЕНИЕ РАБОТЫ ===")
        try:
            if 'bot' in locals():
                await bot.session.close()
                logger.info("✅ Сессия бота закрыта")
        except Exception as e:
            logger.error(f"❌ Ошибка при закрытии сессии: {e}")


if __name__ == "__main__":
    logger.info("🎯 СКРИПТ НАЧИНАЕТ РАБОТУ")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹ Остановлено пользователем (Ctrl+C)")
    except Exception as e:
        logger.error(f"💥 НЕОБРАБОТАННАЯ ОШИБКА: {e}")
        logger.error(traceback.format_exc())
    finally:
        logger.info("🎯 СКРИПТ ЗАВЕРШИЛ РАБОТУ")
        print(f"\n📁 Лог файл создан: {log_filename}")