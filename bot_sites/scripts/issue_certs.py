# scripts/issue_certs.py
from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Sequence, Tuple

from config import SQLITE_PATH
from scripts.nginx_sync import sync_all_domains

ACME_WEBROOT = Path("/var/www/certbot")

# ----------------- DB helpers -----------------
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(SQLITE_PATH)
    c.row_factory = sqlite3.Row
    return c

def _nowiso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ----------------- filesystem -----------------
def _ensure_webroot():
    """
    Гарантируем, что /var/www/certbot/.well-known/acme-challenge существует
    (работаем под root -> 0755 достаточно).
    """
    d1 = ACME_WEBROOT
    d2 = ACME_WEBROOT / ".well-known"
    d3 = d2 / "acme-challenge"
    for d in (d1, d2, d3):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o755)
        except Exception:
            pass

# ----------------- domain selection -----------------
def _pending_domains(limit: int | None = None) -> List[str]:
    """
    Домены без ssl_ok (active/hot) — готовим сертификаты заранее.
    """
    sql = "SELECT host FROM domain WHERE ssl_ok=0 AND status IN ('active','hot') ORDER BY id ASC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with _conn() as c:
        rows = c.execute(sql).fetchall()
    return [str(r["host"]).strip() for r in rows if str(r["host"]).strip()]

# ----------------- certbot -----------------
def _certbot_webroot(host: str, email: str | None = None, staging: bool = False) -> Tuple[bool, str]:
    """
    certbot certonly --webroot для одного домена.
    Возвращает (ok, output)
    """
    host = str(host).strip()
    cmd: List[str] = [
        "certbot", "certonly",
        "--webroot", "-w", str(ACME_WEBROOT),
        "-d", host,
        "--agree-tos",
        "--non-interactive",
        "--expand",
    ]
    if email and isinstance(email, str):
        cmd += ["-m", email]
    else:
        # запасной e-mail
        cmd += ["-m", f"admin@{host}"]
    if staging:
        cmd += ["--staging"]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return (proc.returncode == 0, out.strip())

def _mark_ssl_ok(host: str):
    with _conn() as c:
        c.execute("UPDATE domain SET ssl_ok=1, updated_at=? WHERE host=?", (_nowiso(), host))
        c.execute(
            "INSERT INTO event_log (event_type, payload, created_at) VALUES (?,?,?)",
            ("ssl_issued", f'{{"host":"{host}"}}', _nowiso())
        )
        c.commit()

# ----------------- public API -----------------
def issue_for_all(
    domains: Sequence[str] | None = None,
    pick_limit: int | None = None,
    email: str | None = None,
    staging: bool = False,
) -> Tuple[bool, List[Tuple[str, str, str]]]:
    """
    Выпускает сертификаты:
      - если передан domains -> работаем только по этому списку;
      - иначе берём из БД все ssl_ok=0 (active/hot), опционально ограничивая pick_limit.

    Возвращает:
      ok_any: были ли успешные выпуски
      details: список кортежей (domain, "ok"/"fail", message) + ("system","sync", nginx_msg)
    """
    _ensure_webroot()

    if domains:
        hosts = [str(h).strip() for h in domains if str(h).strip()]
    else:
        hosts = _pending_domains(limit=pick_limit)

    if not hosts:
        return False, [("system", "info", "no domains need ssl")]

    ok_cnt = 0
    details: List[Tuple[str, str, str]] = []

    for h in hosts:
        ok, out = _certbot_webroot(h, email=email, staging=staging)
        if ok:
            ok_cnt += 1
            _mark_ssl_ok(h)
            details.append((h, "ok", "certificate issued"))
        else:
            details.append((h, "fail", out[:400] if out else "certbot failed"))

    # пересобрать nginx после партии
    sync_msg = sync_all_domains()
    details.append(("system", "sync", sync_msg))

    return (ok_cnt > 0), details

def issue_for_domains_interactive(arg: object = None, staging: bool = False):
    """
    Совместимость со старым вызовом из бота.

    Поведение:
      - если arg — это последовательность доменов (list/tuple/set), выпускаем ТОЛЬКО для них;
      - если arg — строка (похожа на email, содержит "@"), считаем это email для certbot;
      - если arg пусто — работаем по БД (все ssl_ok=0).

    Возвращает (ok_any, details_list), где details_list — список (domain, "ok"/"fail", message).
    """
    domains: Sequence[str] | None = None
    email: str | None = None

    if isinstance(arg, (list, tuple, set)):
        domains = [str(x).strip() for x in arg if str(x).strip()]
    elif isinstance(arg, str) and "@" in arg:
        email = arg
    elif arg is None:
        pass
    else:
        # если передали что-то иное — попробуем как строку, вдруг email
        try:
            s = str(arg)
            if "@" in s:
                email = s
        except Exception:
            pass

    return issue_for_all(domains=domains, email=email, staging=staging)

# ----------------- CLI -----------------
if __name__ == "__main__":
    # Примеры:
    #   python scripts/issue_certs.py
    #   python scripts/issue_certs.py email@example.com
    #   python scripts/issue_certs.py --staging
    #   python scripts/issue_certs.py domain1.tld domain2.tld
    import sys as _sys

    arg_email: str | None = None
    arg_staging = False
    doms: List[str] = []

    for a in _sys.argv[1:]:
        if a == "--staging":
            arg_staging = True
        elif "@" in a:
            arg_email = a
        else:
            doms.append(a)

    ok, details = issue_for_all(domains=doms or None, email=arg_email, staging=arg_staging)
    # печать человекочитаемая
    ok_cnt = sum(1 for d, s, _ in details if d != "system" and s == "ok")
    print(f"requested={len([x for x in details if x[0] != 'system'])} ok={ok_cnt}")
    for d, s, m in details:
        if d == "system":
            print(f"[SYSTEM] {s}: {m}")
        else:
            tag = "OK" if s == "ok" else "FAIL"
            print(f"[{tag}] {d} :: {m}")
    _sys.exit(0 if ok else 1)
