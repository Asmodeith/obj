# services/monitor.py
from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

import requests

# --- пути импорта проекта ---
import sys
from pathlib import Path as _P
PROJ_ROOT = _P(__file__).resolve().parents[1]
if str(PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJ_ROOT))

from config import SQLITE_PATH, ADMINS, BOT_TOKEN
from scripts.nginx_sync import sync_all_domains

LOG = logging.getLogger("mirrorhub.monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ------------------ НАСТРОЙКИ АНТИ-ФОЛС ------------------

CHECK_INTERVAL_SEC = 60           # раз в N секунд
HTTP_TIMEOUT = 8.0
USER_AGENT = "Mozilla/5.0 (compatible; MirrorHubMonitor/2.0)"

GRACE_MINUTES_AFTER_ACTIVATE = 20  # после перевода в active столько минут домен НЕ блокируем
FAILS_TO_BLOCK = 5                 # столько подряд неудач нужно для блокировки
FAIL_WINDOW_MIN = 60               # окно времени, в котором считаем «подряд» (если давно было ОК — обнулим)

# что считаем «страницей блокировки» (используется только совместно с кодами 4xx/5xx)
BLOCK_PATTERNS = [
    "роском", "roskom", "доступ ограничен", "доступ к ресурсу ограничен",
    "this site is blocked", "site blocked", "blocked by", "запрещено",
]

# ----------------------------------------------------------------

CREATE_MONITOR_STATE_SQL = """
CREATE TABLE IF NOT EXISTS monitor_state (
  host TEXT PRIMARY KEY,
  fail_count INTEGER NOT NULL DEFAULT 0,
  first_fail_at TEXT,
  last_fail_at TEXT,
  last_ok_at TEXT,
  last_status INTEGER,
  last_error TEXT
);
"""

@dataclass
class DomainRow:
    host: str
    status: str
    ssl_ok: int
    updated_at: Optional[str]  # iso

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(SQLITE_PATH)
    c.row_factory = sqlite3.Row
    return c

def _nowiso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def _init_db():
    with _conn() as c:
        c.executescript(CREATE_MONITOR_STATE_SQL)
        c.commit()

def send_admin_message(text: str):
    if not BOT_TOKEN or not ADMINS:
        LOG.warning("No BOT_TOKEN or ADMINS configured; skip notify")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for admin in ADMINS:
        try:
            requests.post(url, json={"chat_id": admin, "text": text, "parse_mode": "HTML"}, timeout=8)
        except Exception:
            LOG.exception("Failed to send admin message")

# ------------------ HTTP проверка ------------------

def http_check(host: str) -> Tuple[str, Optional[int], str]:
    """
    Возвращает (result, status_code, excerpt)
      result: "ok" | "maybe_block" | "fail"
      - ok: сайт открылся (2xx/3xx)
      - maybe_block: 451 или явные "блок-строки" в ответе при 4xx/5xx
      - fail: таймауты/сетевые ошибки/ssl ошибки
    """
    url = f"https://{host}/"
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        r = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT, allow_redirects=True, verify=True)
        code = r.status_code
        body = (r.text or "")[:4000]
        if 200 <= code < 400:
            return "ok", code, ""
        # 451 — почти всегда блок
        if code == 451:
            return "maybe_block", code, "451"
        # 403 НЕ считаем блоком (может быть нормальная защита/приложение)
        if code != 403:
            low = body.lower()
            if any(p in low for p in BLOCK_PATTERNS):
                return "maybe_block", code, "pattern"
        return "fail", code, "http_error"
    except requests.exceptions.ConnectTimeout:
        return "fail", None, "connect_timeout"
    except requests.exceptions.ConnectionError as e:
        return "fail", None, f"conn_err:{e.__class__.__name__}"
    except requests.exceptions.SSLError:
        # TLS ошибки часто мимолётные — считаем как обычный fail, НЕ блок
        return "fail", None, "ssl_error"
    except Exception as e:
        return "fail", None, f"err:{e.__class__.__name__}"

# ------------------ логика анти-фальс ------------------

def _get_domains_to_check() -> list[DomainRow]:
    with _conn() as c:
        rows = c.execute(
            "SELECT host, status, ssl_ok, updated_at FROM domain WHERE status='active'"
        ).fetchall()
    return [DomainRow(r["host"], r["status"], int(r["ssl_ok"]), r["updated_at"]) for r in rows]

def _get_state(host: str) -> dict:
    with _conn() as c:
        row = c.execute("SELECT * FROM monitor_state WHERE host=?", (host,)).fetchone()
    return dict(row) if row else {}

def _save_state(host: str, **fields):
    sets = ", ".join([f"{k}=?" for k in fields.keys()])
    vals = list(fields.values())

    with _conn() as c:
        exist = c.execute("SELECT 1 FROM monitor_state WHERE host=?", (host,)).fetchone()
        if exist:
            c.execute(f"UPDATE monitor_state SET {sets} WHERE host=?", [*vals, host])
        else:
            cols = ", ".join(["host"] + list(fields.keys()))
            qs = ", ".join(["?"] * (1 + len(fields)))
            c.execute(f"INSERT INTO monitor_state ({cols}) VALUES ({qs})", [host, *vals])
        c.commit()

def _reset_fail(host: str, code: Optional[int]):
    _save_state(
        host,
        fail_count=0,
        first_fail_at=None,
        last_fail_at=None,
        last_ok_at=_nowiso(),
        last_status=code or 200,
        last_error=""
    )

def _bump_fail(host: str, code: Optional[int], err: str):
    st = _get_state(host)
    now = _nowiso()
    fc = int(st.get("fail_count") or 0)
    first = st.get("first_fail_at")
    # если давно было ОК — начнём новую серию
    last_ok = _parse_iso(st.get("last_ok_at"))
    if last_ok:
        if datetime.now(timezone.utc) - last_ok > timedelta(minutes=FAIL_WINDOW_MIN):
            fc = 0
            first = None
    if not first:
        first = now
    fc += 1
    _save_state(
        host,
        fail_count=fc,
        first_fail_at=first,
        last_fail_at=now,
        last_status=code or 0,
        last_error=err
    )
    return fc

def _is_in_grace(updated_at_iso: Optional[str]) -> bool:
    if not updated_at_iso:
        return False
    dt = _parse_iso(updated_at_iso)
    if not dt:
        return False
    return datetime.now(timezone.utc) - dt < timedelta(minutes=GRACE_MINUTES_AFTER_ACTIVATE)

def _should_block(host: str, result: str, code: Optional[int]) -> bool:
    """
    Решение о блокировке:
    - игнор в grace-период;
    - на единичных фейлах — не блокируем;
    - блокируем, если fail_count >= FAILS_TO_BLOCK и
      последнее состояние было "maybe_block" или "fail" и повторялось.
    """
    st = _get_state(host)
    fc = int(st.get("fail_count") or 0)
    if fc < FAILS_TO_BLOCK:
        return False
    # если набили счётчик — проверим, что не просто «редкий 500», а именно устойчивый недоступ/блок
    # принимаем любой повторяющийся fail/maybe_block
    return True

# ------------------ действия с БД доменов ------------------

def mark_blocked(host: str, reason: str):
    with _conn() as c:
        c.execute("UPDATE domain SET status='blocked', updated_at=? WHERE host=?", (_nowiso(), host))
        c.execute(
            "INSERT INTO event_log (event_type, payload, created_at) VALUES (?,?,?)",
            ("domain_blocked", json.dumps({"host": host, "reason": reason}, ensure_ascii=False), _nowiso())
        )
        c.commit()

def pick_replacement() -> Optional[str]:
    with _conn() as c:
        row = c.execute(
            "SELECT host FROM domain WHERE status='hot' AND ssl_ok=1 ORDER BY id ASC LIMIT 1"
        ).fetchone()
    return row["host"] if row else None

def activate_host(host: str):
    with _conn() as c:
        c.execute("UPDATE domain SET status='active', updated_at=? WHERE host=?", (_nowiso(), host))
        c.execute(
            "INSERT INTO event_log (event_type, payload, created_at) VALUES (?,?,?)",
            ("domain_activated", json.dumps({"host": host}, ensure_ascii=False), _nowiso())
        )
        c.commit()

# ------------------ главный цикл ------------------

def monitor_loop():
    LOG.info(
        "Monitor started: check=%ss, grace=%smin, fails_to_block=%s",
        CHECK_INTERVAL_SEC, GRACE_MINUTES_AFTER_ACTIVATE, FAILS_TO_BLOCK
    )
    _init_db()

    while True:
        domains = _get_domains_to_check()

        for d in domains:
            host = d.host

            # grace-период после перевода в active
            if _is_in_grace(d.updated_at):
                LOG.info("GRACE %s (recently activated) — skip check", host)
                continue

            result, code, note = http_check(host)
            LOG.info("CHECK %s -> %s (code=%s, note=%s)", host, result, code, note)

            if result == "ok":
                _reset_fail(host, code)
                continue

            # fail / maybe_block -> увеличим счётчик
            fc = _bump_fail(host, code, note)
            LOG.info("FAIL %s count=%s", host, fc)

            if _should_block(host, result, code):
                reason = f"{result}:{code}:{note}"
                LOG.warning("BLOCK CONFIRMED %s reason=%s", host, reason)
                mark_blocked(host, reason)
                send_admin_message(
                    f"⚠️ <b>Домен заблокирован (подтверждено):</b> <code>{host}</code>\nПричина: {reason}\nЗапускаю автозамену…"
                )
                repl = pick_replacement()
                if repl:
                    activate_host(repl)
                    rep = sync_all_domains()
                    send_admin_message(f"✅ Автозамена: <code>{host}</code> → <code>{repl}</code>\nNginx: {rep}")
                else:
                    send_admin_message(
                        f"❌ Для <code>{host}</code> нет готовых HOT→ACTIVE доменов. Замен не найдено."
                    )
                # после блокировки сбросим счётчик, чтобы не триггерить снова
                _reset_fail(host, code)

        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    monitor_loop()
