# bot/main.py
import asyncio
import json
import logging
import sqlite3
from typing import Dict, List

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile

from config import BOT_TOKEN, ADMINS, SQLITE_PATH, NGINX_LOG_FILE, DEFAULT_CONTENT
from .keyboards import (
    admin_reply_kb, main_menu,
    mirrors_menu, domain_row,
    content_menu, contacts_menu, managers_menu,
    stats_menu
)
from .storage import (
    # mirrors
    list_domains, count_domains, add_domains, delete_domains, activate_hosts,
    # content
    get_content, update_content_fields,
    contacts_list, contacts_add, contacts_edit, contacts_delete, contacts_set,
    # managers
    get_managers, set_manager,
    # find/replace
    find_occurrences, replace_exact_everywhere,
)
from services.stats import build_stats, export_stats_csv
from scripts.issue_certs import issue_for_domains_interactive
from scripts.nginx_sync import sync_all_domains

original_dp = dp

# -------------------- ЛОГИ --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
log = logging.getLogger("mirrorhub.bot")


# -------------------- АВТО-ИНИЦИАЛИЗАЦИЯ БД --------------------
def _ensure_db():
    conn = sqlite3.connect(SQLITE_PATH)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("""CREATE TABLE IF NOT EXISTS domain (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        host TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('hot','active','blocked')),
        ssl_ok INTEGER NOT NULL DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    );""")
    c.execute("""CREATE TABLE IF NOT EXISTS content (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        subtitle TEXT,
        contacts TEXT NOT NULL DEFAULT '[]',
        footer TEXT,
        updated_at TEXT
    );""")
    c.execute("""CREATE TABLE IF NOT EXISTS event_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT,
        payload TEXT,
        created_at TEXT
    );""")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_domain_host ON domain(host);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_domain_status ON domain(status);")
    # стартовый контент
    cnt = c.execute("SELECT COUNT(1) FROM content").fetchone()[0]
    if cnt == 0:
        dc = DEFAULT_CONTENT or {"title": "", "subtitle": "", "contacts": [], "footer": ""}
        c.execute(
            "INSERT INTO content (title, subtitle, contacts, footer, updated_at) VALUES (?,?,?,?,datetime('now'))",
            (dc.get("title",""), dc.get("subtitle",""), json.dumps(dc.get("contacts",[]), ensure_ascii=False), dc.get("footer",""))
        )
        c.execute(
            "INSERT INTO event_log (event_type, payload, created_at) VALUES (?,?,datetime('now'))",
            ("bootstrap_content", json.dumps({"via":"bot_start"}, ensure_ascii=False))
        )
    conn.commit()
    conn.close()
    log.info("DB ensured at %s", SQLITE_PATH)

_ensure_db()


# -------------------- БОТ --------------------
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

def is_admin(uid: int) -> bool:
    return uid in ADMINS

# простое состояние многошаговых действий
PENDING: Dict[int, Dict] = {}  # chat_id -> {"action": str, "step": int, "data": {...}}

def _set_state(chat_id: int, action: str, step: int = 1, data: Dict | None = None):
    PENDING[chat_id] = {"action": action, "step": step, "data": data or {}}

def _clear_state(chat_id: int):
    PENDING.pop(chat_id, None)


# ===================== КОМАНДЫ =====================

@dp.message(CommandStart())
async def cmd_start(m: Message):
    # ТИХИЙ /start — ничего не отвечаем никому
    return

@dp.message(Command("admin"))
async def cmd_admin(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer("Главное меню:", reply_markup=main_menu())


@dp.message(F.text == "🛠 Админка")
async def open_admin_button(m: Message):
    if not is_admin(m.from_user.id):
        return
    # отправляем только 1 сообщение — главное меню которое содержит разделы
    await m.answer("Главное меню:", reply_markup=main_menu())


@dp.message(Command("help"))
async def cmd_help(m: Message):
    if not is_admin(m.from_user.id):
        return
    await m.answer(
        "• 🌐 Зеркала — добавить домены, авто-SSL и включение\n"
        "• ✍️ Контент — заголовок/подзаголовок/футер/контакты/менеджеры\n"
        "• 🔎/🔁 — поиск/замена строки во всех полях\n"
        "• 📈 Статистика — отчёты и экспорт",
        reply_markup=main_menu()
    )


# ===================== ЗЕРКАЛА =====================

@dp.callback_query(F.data == "mir:menu")
async def mir_menu(c: CallbackQuery):
    await c.message.answer(
        "🌐 Управление зеркалами:\n"
        "• ➕ Добавить — вставь домены построчно → бот сам выпустит SSL и включит рабочие\n"
        "• ✅/⛔️ — включить/выключить все\n"
        "• 📋 Список — кликабельные строки (вкл/выкл/удалить)",
        reply_markup=mirrors_menu()
    )
    await c.answer()


@dp.callback_query(F.data == "mir:add")
async def mir_add(c: CallbackQuery):
    _set_state(c.message.chat.id, "mir_add")
    await c.message.answer(
        "Вставь домены построчно. Можно кидать и ссылки — я всё почищу до домена.\n"
        "Пример:\n<code>\nabrakabrafunniestday.shop\nhttps://abrakabrafunniestday.shop/\n</code>"
    )
    await c.answer()


@dp.callback_query(F.data == "mir:ssl_all")
async def mir_ssl_all(c: CallbackQuery):
    with sqlite3.connect(SQLITE_PATH) as conn:
        conn.row_factory = sqlite3.Row
        doms = [r["host"] for r in conn.execute("SELECT host FROM domain WHERE ssl_ok=0 ORDER BY id ASC")]
    if not doms:
        await c.message.answer("Все домены уже с SSL.")
        return await c.answer()
    await c.message.answer(f"🔐 Выпускаю SSL для {len(doms)} доменов...")
    ok, report = issue_for_domains_interactive(doms)
    ok_cnt = sum(1 for d, s, _ in report if d != "system" and s == "ok")
    await c.message.answer(f"SSL готово: {ok_cnt}/{len(doms)}")

    activated = activate_hosts(doms)
    await c.message.answer(f"✅ Авто-включено доменов: {activated}")

    rep = sync_all_domains()
    await c.message.answer(f"🔁 Nginx после SSL/активации:\n{rep}")
    await c.answer()


@dp.callback_query(F.data == "mir:list")
async def mir_list(c: CallbackQuery):
    rows = list_domains(None, 0, 200)
    if not rows:
        await c.message.answer("Список пуст.")
        return await c.answer()
    await c.message.answer("📋 Список зеркал (кликабельно):")
    for r in rows:
        await c.message.answer(".", reply_markup=domain_row(r["host"], r["status"], bool(r["ssl_ok"])))
    await c.answer()


@dp.callback_query(F.data == "mir:enable_all")
async def mir_enable_all(c: CallbackQuery):
    with sqlite3.connect(SQLITE_PATH) as conn:
        conn.execute("UPDATE domain SET status='active' WHERE ssl_ok=1")
        conn.commit()
    await c.message.answer("✅ Все домены с SSL включены (active).")
    await c.answer()


@dp.callback_query(F.data == "mir:disable_all")
async def mir_disable_all(c: CallbackQuery):
    with sqlite3.connect(SQLITE_PATH) as conn:
        conn.execute("UPDATE domain SET status='hot' WHERE status='active'")
        conn.commit()
    await c.message.answer("⛔️ Все активные переведены в режим ожидания (hot).")
    await c.answer()


@dp.callback_query(F.data.startswith("mir:enable:"))
async def mir_enable_one(c: CallbackQuery):
    host = c.data.split(":", 2)[2]
    with sqlite3.connect(SQLITE_PATH) as conn:
        conn.execute("UPDATE domain SET status='active' WHERE host=? AND ssl_ok=1", (host,))
        conn.commit()
    await c.message.edit_text(f"✅ <code>{host}</code> включён.", reply_markup=None)
    await c.answer()


@dp.callback_query(F.data.startswith("mir:disable:"))
async def mir_disable_one(c: CallbackQuery):
    host = c.data.split(":", 2)[2]
    with sqlite3.connect(SQLITE_PATH) as conn:
        conn.execute("UPDATE domain SET status='hot' WHERE host=?", (host,))
        conn.commit()
    await c.message.edit_text(f"⛔️ <code>{host}</code> выключен.", reply_markup=None)
    await c.answer()


@dp.callback_query(F.data.startswith("mir:del:"))
async def mir_delete_one(c: CallbackQuery):
    host = c.data.split(":", 2)[2]
    delete_domains([host])
    rep = sync_all_domains()
    await c.message.edit_text(f"🗑 Удалён <code>{host}</code>\nNginx: {rep}", reply_markup=None)
    await c.answer()


# ===================== КОНТЕНТ =====================

@dp.callback_query(F.data == "cnt:menu")
async def cnt_menu(c: CallbackQuery):
    await c.message.answer(
        "✍️ Изменение контента для ВСЕХ зеркал сразу:\n"
        "• 🪪 Заголовок / 📝 Подзаголовок / 📜 Футер — пришлёшь текст → обновим\n"
        "• 📇 Контакты — классическое управление списком\n"
        "• 👤 Менеджеры — быстрые кнопки для @manager_one/@manager_two\n"
        "• 🔎 Найти строку / 🔁 Заменить строку\n"
        "• 👀 Превью — показать текущий контент",
        reply_markup=content_menu()
    )
    await c.answer()


@dp.callback_query(F.data == "cnt:title")
async def cnt_title(c: CallbackQuery):
    _set_state(c.message.chat.id, "set_title")
    await c.message.answer("🪪 Пришли новый <b>Заголовок</b> (одной строкой).")
    await c.answer()

@dp.callback_query(F.data == "cnt:subtitle")
async def cnt_subtitle(c: CallbackQuery):
    _set_state(c.message.chat.id, "set_subtitle")
    await c.message.answer("📝 Пришли новый <b>Подзаголовок</b> (одной строкой).")
    await c.answer()

@dp.callback_query(F.data == "cnt:footer")
async def cnt_footer(c: CallbackQuery):
    _set_state(c.message.chat.id, "set_footer")
    await c.message.answer("📜 Пришли новый <b>Футер</b> (одной строкой).")
    await c.answer()


# --- контакты (общие) ---

@dp.callback_query(F.data == "cnt:contacts")
async def cnt_contacts(c: CallbackQuery):
    lst = contacts_list()
    lines = ["📇 Текущие контакты:"]
    if not lst:
        lines.append("— пусто —")
    else:
        for i, item in enumerate(lst, start=1):
            lines.append(f"{i}. {item.get('label','')} — {item.get('url','')}")
    await c.message.answer("\n".join(lines), reply_markup=contacts_menu())
    await c.answer()

@dp.callback_query(F.data == "cnt:contacts:add")
async def cnt_contacts_add(c: CallbackQuery):
    _set_state(c.message.chat.id, "contact_add")
    await c.message.answer("➕ Пришли контакт в формате:\n<code>Метка | https://ссылка</code>")
    await c.answer()

@dp.callback_query(F.data == "cnt:contacts:edit")
async def cnt_contacts_edit(c: CallbackQuery):
    _set_state(c.message.chat.id, "contact_edit")
    await c.message.answer("✏️ Пришли правку в формате:\n<code>Номер | Новая метка | https://новая_ссылка</code>")
    await c.answer()

@dp.callback_query(F.data == "cnt:contacts:del")
async def cnt_contacts_del(c: CallbackQuery):
    _set_state(c.message.chat.id, "contact_del")
    await c.message.answer("🗑 Пришли номера для удаления (через пробел). Пример: <code>2 5 6</code>")
    await c.answer()

@dp.callback_query(F.data == "cnt:contacts:export")
async def cnt_contacts_export(c: CallbackQuery):
    lst = contacts_list()
    await c.message.answer(f"<code>{json.dumps(lst, ensure_ascii=False, indent=2)}</code>")
    await c.answer()

@dp.callback_query(F.data == "cnt:contacts:import")
async def cnt_contacts_import(c: CallbackQuery):
    _set_state(c.message.chat.id, "contacts_import")
    await c.message.answer("📥 Пришли JSON списка контактов. Пример:\n<code>[{\"label\":\"Менеджер\",\"url\":\"https://t.me/user\"}]</code>")
    await c.answer()


# --- менеджеры (быстрая замена) ---

@dp.callback_query(F.data == "cnt:managers")
async def cnt_managers(c: CallbackQuery):
    one, two = get_managers()
    await c.message.answer(
        "👤 Быстрая замена менеджеров.\n"
        "Нажми на нужного → пришли username в виде <code>newmanager</code> или <code>@newmanager</code>.\n"
        "Ссылка будет вида <code>/go/telegram/USERNAME</code>, текст — <code>@USERNAME</code>.",
        reply_markup=managers_menu(one, two)
    )
    await c.answer()

@dp.callback_query(F.data == "cnt:man:set:1")
async def cnt_man_set_one(c: CallbackQuery):
    _set_state(c.message.chat.id, "set_manager_1")
    await c.message.answer("👤 Менеджер 1 → пришли username (например, <code>manager_one</code> или <code>@manager_one</code>).")
    await c.answer()

@dp.callback_query(F.data == "cnt:man:set:2")
async def cnt_man_set_two(c: CallbackQuery):
    _set_state(c.message.chat.id, "set_manager_2")
    await c.message.answer("👤 Менеджер 2 → пришли username (например, <code>manager_two</code> или <code>@manager_two</code>).")
    await c.answer()


# --- find / replace ---

@dp.callback_query(F.data == "cnt:find")
async def cnt_find(c: CallbackQuery):
    _set_state(c.message.chat.id, "find_string")
    await c.message.answer("🔎 Введи точную строку, которую искать по всем полям (заголовок/подзаголовок/футер/контакты).")
    await c.answer()

@dp.callback_query(F.data == "cnt:replace")
async def cnt_replace(c: CallbackQuery):
    _set_state(c.message.chat.id, "replace_old", step=1, data={})
    await c.message.answer("🔁 Шаг 1/2. Пришли строку, которую надо заменить (точное совпадение).")
    await c.answer()


@dp.callback_query(F.data == "cnt:preview")
async def cnt_preview(c: CallbackQuery):
    cont = get_content()
    if not cont:
        await c.message.answer("— контент ещё не задан —")
        return await c.answer()
    lines = [
        "👀 <b>Превью</b>",
        f"<b>{cont.get('title','')}</b>",
        cont.get("subtitle", ""),
        "",
        "Контакты:",
    ]
    for item in cont.get("contacts", []):
        lines.append(f"• {item.get('label','')}: {item.get('url','')}")
    lines.append("")
    lines.append(cont.get("footer", ""))
    await c.message.answer("\n".join(lines))
    await c.answer()


# ===================== СТАТИСТИКА =====================

@dp.callback_query(F.data == "st:menu")
async def st_menu(c: CallbackQuery):
    await c.message.answer(f"📈 Статистика. Источник логов: <code>{NGINX_LOG_FILE}</code>", reply_markup=stats_menu())
    await c.answer()

@dp.callback_query(F.data == "st:export")
async def st_export(c: CallbackQuery):
    path = export_stats_csv()
    await c.message.answer_document(FSInputFile(str(path)), caption="📤 Экспорт CSV")
    await c.answer()

@dp.callback_query(F.data.startswith("st:range:"))
async def st_range(c: CallbackQuery):
    rng = c.data.split(":")[2]
    days = None if rng == "all" else int(rng)
    await c.message.answer(build_stats(days=days))
    await c.answer()


# ===================== ОБРАБОТКА ТЕКСТА (многошаговые сценарии) =====================

@dp.message()
async def text_flow(m: Message):
    if not is_admin(m.from_user.id):
        return

    st = PENDING.get(m.chat.id)
    if not st:
        return

    action = st["action"]
    step = st.get("step", 1)
    data = st.get("data", {})

    # --- добавление доменов: полностью автоматический цикл ---
    if action == "mir_add":
        raw_hosts = [h.strip() for h in (m.text or "").splitlines() if h.strip()]
        added, accepted, rejected, already_ssl = add_domains(raw_hosts, status="hot")

        lines: List[str] = [f"➕ Добавлено в БД: {added}"]
        if accepted:
            lines.append("✅ Приняты:")
            lines += [f"• {h}" for h in accepted]
        if rejected:
            lines.append("")
            lines.append("🚫 Отброшены (не похожи на домены):")
            lines += [f"• {h}" for h in rejected]
        if already_ssl:
            lines.append("")
            lines.append("🔐 Уже есть SSL (помечены ssl_ok=1):")
            lines += [f"• {h}" for h in already_ssl]

        await m.answer("\n".join(lines) if lines else "Нечего добавить.")

        rep = sync_all_domains()
        await m.answer(f"🔄 Nginx:\n{rep}")

        need_issue = [h for h in accepted if h not in already_ssl]
        ok_domains: List[str] = []
        if need_issue:
            await m.answer(f"🔐 Выпускаю SSL для {len(need_issue)} доменов…")
            ok, report = issue_for_domains_interactive(need_issue)
            ok_cnt = sum(1 for d, s, _ in report if d != "system" and s == "ok")
            await m.answer(f"SSL готово: {ok_cnt}/{len(need_issue)}")
            ok_domains = [d for (d, s, msg) in report if d != "system" and s == "ok"]

        to_activate = sorted(set(ok_domains + already_ssl))
        if to_activate:
            activated = activate_hosts(to_activate)
            await m.answer(f"✅ Авто-включено доменов: {activated}")
        else:
            await m.answer("ℹ️ Нет доменов для авто-включения.")

        rep2 = sync_all_domains()
        await m.answer(f"🔁 Готово. Nginx после активации:\n{rep2}")

        _clear_state(m.chat.id)
        return

    # --- простые поля (title/subtitle/footer) ---
    if action == "set_title":
        update_content_fields(title=m.text or "")
        await m.answer("🪪 Заголовок обновлён.")
        _clear_state(m.chat.id)
        return

    if action == "set_subtitle":
        update_content_fields(subtitle=m.text or "")
        await m.answer("📝 Подзаголовок обновлён.")
        _clear_state(m.chat.id)
        return

    if action == "set_footer":
        update_content_fields(footer=m.text or "")
        await m.answer("📜 Футер обновлён.")
        _clear_state(m.chat.id)
        return

    # --- контакты ---
    if action == "contact_add":
        raw = (m.text or "")
        if "|" not in raw:
            await m.answer("❌ Формат: <code>Метка | https://ссылка</code>")
            return
        label, url = [s.strip() for s in raw.split("|", 1)]
        contacts_add(label, url)
        await m.answer("✅ Контакт добавлен.")
        _clear_state(m.chat.id)
        return

    if action == "contact_edit":
        raw = (m.text or "")
        parts = [s.strip() for s in raw.split("|")]
        if len(parts) < 3 or not parts[0].isdigit():
            await m.answer("❌ Формат: <code>Номер | Новая метка | https://новая_ссылка</code>")
            return
        idx = int(parts[0])
        label = parts[1]
        url = parts[2]
        ok = contacts_edit(idx, label=label, url=url)
        await m.answer("✅ Изменено." if ok else "❌ Неверный номер.")
        _clear_state(m.chat.id)
        return

    if action == "contact_del":
        nums = [(t.strip()) for t in (m.text or "").split()]
        indices = [int(t) for t in nums if t.isdigit()]
        if not indices:
            await m.answer("❌ Пришли номера (например: <code>2 5 6</code>).")
            return
        cnt = contacts_delete(indices)
        await m.answer(f"🗑 Удалено: {cnt}")
        _clear_state(m.chat.id)
        return

    if action == "contacts_import":
        try:
            lst = json.loads(m.text or "[]")
            assert isinstance(lst, list)
            for x in lst:
                assert "label" in x and "url" in x
            contacts_set(lst)
            await m.answer("📥 Импорт выполнен.")
        except Exception as e:
            await m.answer(f"❌ Ошибка JSON: <code>{e}</code>")
        _clear_state(m.chat.id)
        return

    # --- менеджеры ---
    if action == "set_manager_1":
        set_manager(1, m.text or "")
        await m.answer("✅ Менеджер 1 обновлён (ссылка: <code>/go/telegram/USERNAME</code>, текст: <code>@USERNAME</code>).")
        _clear_state(m.chat.id)
        return

    if action == "set_manager_2":
        set_manager(2, m.text or "")
        await m.answer("✅ Менеджер 2 обновлён (ссылка: <code>/go/telegram/USERNAME</code>, текст: <code>@USERNAME</code>).")
        _clear_state(m.chat.id)
        return

    # --- поиск/замена ---
    if action == "find_string":
        needle = (m.text or "").strip()
        if not needle:
            await m.answer("❌ Пустая строка. Отменено.")
            _clear_state(m.chat.id)
            return
        rep = find_occurrences(needle)
        if not rep["exists"]:
            await m.answer(f"🙅‍♂️ Строка <code>{needle}</code> нигде не найдена.")
        else:
            cnt = rep["counts"]; pos = rep["positions"]
            lines = [
                f"🔍 Найдено для <code>{needle}</code>:",
                f"• Заголовок: {cnt['title']}",
                f"• Подзаголовок: {cnt['subtitle']}",
                f"• Футер: {cnt['footer']}",
                f"• Контакты (метки): {cnt['contacts_label']} — индексы: {pos['contacts_label_idx'] or '-'}",
                f"• Контакты (ссылки): {cnt['contacts_url']} — индексы: {pos['contacts_url_idx'] or '-'}",
                f"Итого полей с вхождениями: {cnt['total_slots']}",
            ]
            await m.answer("\n".join(lines))
        _clear_state(m.chat.id)
        return

    if action == "replace_old":
        old = (m.text or "").strip()
        if not old:
            await m.answer("❌ Пустая строка. Отменено.")
            _clear_state(m.chat.id); return
        _set_state(m.chat.id, "replace_new", step=2, data={"old": old})
        await m.answer(f"Шаг 2/2. Пришли <b>новую</b> строку для замены «<code>{old}</code>».")
        return

    if action == "replace_new":
        new = (m.text or "").strip()
        old = PENDING.get(m.chat.id, {}).get("data", {}).get("old")
        if not old:
            await m.answer("❌ Не вижу предыдущего шага. Начни снова: «🔁 Заменить строку».")
            _clear_state(m.chat.id); return
        if not new:
            await m.answer("❌ Пустая новая строка. Отменено.")
            _clear_state(m.chat.id); return

        pre = find_occurrences(old)
        if not pre["exists"]:
            await m.answer(f"🙅‍♂️ Строка <code>{old}</code> нигде не найдена. Ничего не меняю.")
            _clear_state(m.chat.id); return

        rep = replace_exact_everywhere(old, new)
        cnt = rep["counts"]; post = rep.get("post_counts", {})
        lines = [
            "✅ Готово. Отчёт по замене:",
            f"Искал: <code>{old}</code>",
            f"Заменил на: <code>{new}</code>",
            "",
            "🔹 До:",
            f"• Title: {cnt['title']} | Subtitle: {cnt['subtitle']} | Footer: {cnt['footer']}",
            f"• Contacts label: {cnt['contacts_label']} | url: {cnt['contacts_url']}",
        ]
        if post:
            lines += [
                "",
                "🔹 После:",
                f"• Title: {post['title']} | Subtitle: {post['subtitle']} | Footer: {post['footer']}",
                f"• Contacts label: {post['contacts_label']} | url: {post['contacts_url']}",
            ]
        await m.answer("\n".join(lines))
        _clear_state(m.chat.id)
        return


# ===================== ЗАПУСК =====================

def main():
    """Оригинальная функция запуска (для standalone режима)"""
    log.info("Bot is starting…")
    asyncio.run(dp.start_polling(bot))

# Сохраняем оригинальные объекты для импорта
bot = bot
dp = dp

if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
