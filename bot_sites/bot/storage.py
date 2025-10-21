import re
import json
import sqlite3
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import SQLITE_PATH

# -------- валидация / нормализация --------
DOMAIN_RE = re.compile(r"^(?!-)[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$")
LE_LIVE_DIR = Path("/etc/letsencrypt/live")


def normalize_host(raw: str) -> Optional[str]:
    """
    Превращает ввод в чистый домен:
    - режем http(s):// и путь/квери
    - punycode (idna)
    - tolower
    - проверка regex
    """
    if not raw:
        return None
    s = raw.strip()
    if "://" in s:
        try:
            u = urlparse(s)
            s = (u.hostname or "").strip()
        except Exception:
            return None
    s = s.split("/")[0].strip().strip(".")
    if not s:
        return None
    try:
        s = s.encode("idna").decode("ascii")
    except Exception:
        return None
    s = s.lower()
    return s if DOMAIN_RE.match(s) else None


def has_existing_cert(host: str) -> bool:
    """True если у certbot уже лежат файлы для домена."""
    d = LE_LIVE_DIR / host
    return (d / "fullchain.pem").exists() and (d / "privkey.pem").exists()


# -------- базовые утилиты --------

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(SQLITE_PATH)
    c.row_factory = sqlite3.Row
    return c


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -------- зеркала (домены) --------

def count_domains(status: Optional[str] = None) -> int:
    with _conn() as c:
        if status:
            row = c.execute("SELECT COUNT(*) AS n FROM domain WHERE status=?", (status,)).fetchone()
        else:
            row = c.execute("SELECT COUNT(*) AS n FROM domain").fetchone()
    return int(row["n"] if row else 0)


def list_domains(status: Optional[str] = None, offset: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
    with _conn() as c:
        if status:
            rows = c.execute(
                "SELECT id, host, status, ssl_ok FROM domain WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (status, limit, offset)
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT id, host, status, ssl_ok FROM domain ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
    return [dict(r) for r in rows]


def add_domains(hosts: List[str], status: str = "hot") -> Tuple[int, List[str], List[str], List[str]]:
    """
    Добавляет домены в БД.
    Возвращает: (added_count, accepted_hosts, rejected_inputs, already_ssl)
    """
    accepted: List[str] = []
    rejected: List[str] = []
    already_ssl: List[str] = []

    seen = set()
    for raw in hosts:
        nh = normalize_host(raw)
        if not nh:
            rejected.append(raw.strip())
            continue
        if nh in seen:
            continue
        seen.add(nh)
        accepted.append(nh)

    if not accepted:
        return 0, [], rejected, []

    added = 0
    with _conn() as c:
        cur = c.cursor()
        for h in accepted:
            ssl_flag = 1 if has_existing_cert(h) else 0
            if ssl_flag:
                already_ssl.append(h)
            cur.execute(
                "INSERT OR IGNORE INTO domain (host, status, ssl_ok, created_at) VALUES (?,?,?,?)",
                (h, status, ssl_flag, now_iso())
            )
            added += cur.rowcount
        c.commit()

    return added, accepted, rejected, already_ssl


def delete_domains(hosts: List[str]) -> int:
    hosts = [normalize_host(h) for h in hosts]
    hosts = [h for h in hosts if h]
    if not hosts:
        return 0
    with _conn() as c:
        qmarks = ",".join("?" for _ in hosts)
        cur = c.execute(f"DELETE FROM domain WHERE host IN ({qmarks})", hosts)
        c.commit()
        return cur.rowcount


def activate_hosts(hosts: List[str]) -> int:
    """
    Массово включить домены (status='active'), только для тех,
    у кого уже ssl_ok=1. Возвращает количество изменённых строк.
    """
    hosts = [normalize_host(h) for h in hosts]
    hosts = [h for h in hosts if h]
    if not hosts:
        return 0
    with _conn() as c:
        qmarks = ",".join("?" for _ in hosts)
        cur = c.execute(
            f"UPDATE domain SET status='active', updated_at=? WHERE host IN ({qmarks}) AND ssl_ok=1",
            [now_iso(), *hosts]
        )
        c.commit()
        return cur.rowcount


# -------- контент (единый для всех зеркал) --------

def get_content() -> Dict[str, Any]:
    with _conn() as c:
        row = c.execute(
            "SELECT title, subtitle, contacts, footer FROM content ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return {}
    d = dict(row)
    d["contacts"] = json.loads(d["contacts"])
    return d


def set_content(title: str, subtitle: str, contacts_json: list, footer: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO content (title, subtitle, contacts, footer, updated_at) VALUES (?,?,?,?,?)",
            (title, subtitle, json.dumps(contacts_json, ensure_ascii=False), footer, now_iso())
        )
        c.execute(
            "INSERT INTO event_log (event_type, payload, created_at) VALUES (?,?,?)",
            ("content_changed", json.dumps({"title": title}, ensure_ascii=False), now_iso())
        )
        c.commit()


def update_content_fields(**fields) -> None:
    cur = get_content() or {"title": "", "subtitle": "", "contacts": [], "footer": ""}
    new = {
        "title": fields.get("title", cur["title"]),
        "subtitle": fields.get("subtitle", cur["subtitle"]),
        "contacts": fields.get("contacts", cur["contacts"]),
        "footer": fields.get("footer", cur["footer"]),
    }
    set_content(new["title"], new["subtitle"], new["contacts"], new["footer"])


# -------- контакты / менеджеры --------

def contacts_list() -> List[Dict[str, str]]:
    return get_content().get("contacts", [])


def contacts_set(lst: List[Dict[str, str]]) -> None:
    cur = get_content() or {"title": "", "subtitle": "", "contacts": [], "footer": ""}
    update_content_fields(
        title=cur["title"], subtitle=cur["subtitle"], footer=cur["footer"], contacts=lst
    )


def contacts_add(label: str, url: str) -> None:
    lst = contacts_list()
    lst.append({"label": label, "url": url})
    contacts_set(lst)


def contacts_edit(index: int, label: Optional[str] = None, url: Optional[str] = None) -> bool:
    lst = contacts_list()
    if index < 1 or index > len(lst):
        return False
    i = index - 1
    if label is not None:
        lst[i]["label"] = label
    if url is not None:
        lst[i]["url"] = url
    contacts_set(lst)
    return True


def contacts_delete(indices: List[int]) -> int:
    lst = contacts_list()
    idx = set(i - 1 for i in indices if 1 <= i <= len(lst))
    new = [v for k, v in enumerate(lst) if k not in idx]
    contacts_set(new)
    return len(idx)


def _strip_username(s: str) -> Optional[str]:
    """
    Принимает 'newmanager' или '@newmanager' или 't.me/newmanager' — возвращает 'newmanager' (без '@').
    """
    if not s:
        return None
    s = s.strip()
    s = s.replace("https://", "").replace("http://", "")
    s = s.replace("t.me/", "").replace("telegram.me/", "").replace("telegram.dog/", "")
    s = s.lstrip("@").strip()
    return s or None


def get_managers() -> Tuple[Optional[str], Optional[str]]:
    """
    Возвращает текущие usernames без '@' для Менеджер 1 и Менеджер 2 по contacts.
    Ожидается, что ссылки вида '/go/telegram/<username>' или 'https://t.me/<username>'.
    """
    one = None
    two = None
    lst = contacts_list()
    for item in lst:
        label = (item.get("label") or "").strip().lower()
        url = (item.get("url") or "").strip()
        if label == "менеджер 1":
            u = url.replace("https://t.me/", "").replace("http://t.me/", "").replace("/go/telegram/", "")
            one = _strip_username(u)
        elif label == "менеджер 2":
            u = url.replace("https://t.me/", "").replace("http://t.me/", "").replace("/go/telegram/", "")
            two = _strip_username(u)
    return one, two


def set_manager(slot: int, username_or_at: str) -> None:
    """
    slot=1 или 2. Обновляет контакт:
      label: 'Менеджер 1/2'
      url: '/go/telegram/<username>'
    """
    username = _strip_username(username_or_at)
    if not username:
        return
    lst = contacts_list()
    label_target = f"Менеджер {slot}"
    found = False
    for item in lst:
        if (item.get("label") or "").strip().lower() == label_target.lower():
            item["url"] = f"/go/telegram/{username}"
            found = True
            break
    if not found:
        lst.append({"label": label_target, "url": f"/go/telegram/{username}"})
    contacts_set(lst)


# -------- поиск/замена конкретной строки во всём контенте --------

def find_occurrences(needle: str) -> Dict[str, Any]:
    """
    Где встречается точная подстрока needle:
    title, subtitle, footer, contacts[i].label, contacts[i].url
    """
    c = get_content() or {"title": "", "subtitle": "", "footer": "", "contacts": []}
    title_hit = needle in c["title"]
    subtitle_hit = needle in c["subtitle"]
    footer_hit = needle in c["footer"]

    label_hits: List[int] = []
    url_hits: List[int] = []
    for i, item in enumerate(c["contacts"], start=1):
        if needle in (item.get("label") or ""):
            label_hits.append(i)
        if needle in (item.get("url") or ""):
            url_hits.append(i)

    total_slots = (1 if title_hit else 0) + (1 if subtitle_hit else 0) + (1 if footer_hit else 0) \
                  + len(label_hits) + len(url_hits)

    return {
        "exists": total_slots > 0,
        "counts": {
            "title": int(title_hit),
            "subtitle": int(subtitle_hit),
            "footer": int(footer_hit),
            "contacts_label": len(label_hits),
            "contacts_url": len(url_hits),
            "total_slots": total_slots
        },
        "positions": {
            "contacts_label_idx": label_hits,
            "contacts_url_idx": url_hits
        },
        "snapshot": c
    }


def replace_exact_everywhere(old: str, new: str) -> Dict[str, Any]:
    """
    Точная замена подстроки old -> new во всех полях.
    Если вхождений нет — возвращает report.exists=False и ничего не меняет.
    """
    report = find_occurrences(old)
    if not report["exists"]:
        return report

    c = report["snapshot"]
    if old in c["title"]:
        c["title"] = c["title"].replace(old, new)
    if old in c["subtitle"]:
        c["subtitle"] = c["subtitle"].replace(old, new)
    if old in c["footer"]:
        c["footer"] = c["footer"].replace(old, new)

    new_contacts = []
    for item in c["contacts"]:
        label = (item.get("label") or "")
        url = (item.get("url") or "")
        if old in label:
            label = label.replace(old, new)
        if old in url:
            url = url.replace(old, new)
        new_contacts.append({"label": label, "url": url})

    set_content(c["title"], c["subtitle"], new_contacts, c["footer"])
    post = find_occurrences(new)
    report["applied"] = True
    report["post_counts"] = post["counts"]
    return report
