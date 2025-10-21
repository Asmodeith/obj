# /root/mirrorhub/services/stats.py
from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional, Set

from config import NGINX_LOG_FILE, SQLITE_PATH

SKIP_PREFIXES = (
    "/.well-known/",
    "/favicon.ico",
    "/robots.txt",
)
CLICK_PREFIX = "/go/telegram/"

BOT_HINTS = (
    "bot", "crawler", "spider", "pingdom", "gtmetrix", "uptime",
    "ahrefs", "semrush", "mj12", "yandex", "google-inspectiontool",
    "bingbot", "duckduckbot", "baiduspider", "applebot", "petalbot",
    "cloudflare-health-check",
)

@dataclass
class LogRec:
    time: Optional[datetime]
    host: str
    uri: str
    method: str
    status: int
    referer: str
    ua: str

def _db_hosts() -> Set[str]:
    """Домены из БД, которые считаем зеркалами (active/hot)."""
    s: Set[str] = set()
    conn = sqlite3.connect(SQLITE_PATH)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT host FROM domain WHERE status IN ('active','hot')").fetchall()
        for r in rows:
            h = (r["host"] or "").strip().lower()
            if h:
                s.add(h)
    finally:
        conn.close()
    return s

def _iter_json_lines(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

def _parse_time(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def _is_bot(ua: str) -> bool:
    u = (ua or "").lower()
    return any(h in u for h in BOT_HINTS)

def _within_days(dt: Optional[datetime], days: Optional[int]) -> bool:
    if days is None or dt is None:
        return True
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff

def build_stats(days: Optional[int] = None) -> str:
    allowed_hosts = _db_hosts()

    visits = 0
    clicks = 0
    by_host = Counter()
    by_host_bad = Counter()

    for obj in _iter_json_lines(Path(NGINX_LOG_FILE)):
        host = (obj.get("host") or "").strip().lower()
        if not host or host not in allowed_hosts:
            continue

        rec = LogRec(
            time=_parse_time(obj.get("time")),
            host=host,
            uri=str(obj.get("uri") or ""),
            method=str(obj.get("method") or "").upper(),
            status=int(obj.get("status") or 0),
            referer=str(obj.get("referer") or ""),
            ua=str(obj.get("user_agent") or ""),
        )
        if not _within_days(rec.time, days):
            continue

        if rec.uri.startswith(CLICK_PREFIX):
            clicks += 1
            by_host[rec.host] += 1
            continue

        if rec.method == "GET" and not _is_bot(rec.ua):
            if any(rec.uri.startswith(p) for p in SKIP_PREFIXES):
                continue
            if 200 <= rec.status < 400:
                visits += 1
                by_host[rec.host] += 1
            else:
                by_host_bad[rec.host] += 1

    # отчёт
    top_lines = [f"• {h} — {c}" for h, c in by_host.most_common(10)]
    anti_lines = [f"• {h} — {c}" for h, c in by_host_bad.most_common(10)]

    hdr = f"📊 Статистика {'за ' + str(days) + ' дн.' if days else 'за всё время'}\n"
    hdr += f"Всего визитов: {visits} | кликов: {clicks}\n\n"

    hdr += "🔥 Топ зеркал:\n" + ("\n".join(top_lines) if top_lines else "— нет данных —")
    hdr += "\n\n"
    hdr += "🧊 Анти-топ:\n" + ("\n".join(anti_lines) if anti_lines else "— нет данных —")

    return hdr

def export_stats_csv(days: Optional[int] = None) -> Path:
    allowed_hosts = _db_hosts()
    out_rows = []

    for obj in _iter_json_lines(Path(NGINX_LOG_FILE)):
        host = (obj.get("host") or "").strip().lower()
        if not host or host not in allowed_hosts:
            continue

        rec_time = _parse_time(obj.get("time"))
        if not _within_days(rec_time, days):
            continue

        uri = str(obj.get("uri") or "")
        if uri.startswith(CLICK_PREFIX):
            out_rows.append([
                rec_time.isoformat() if rec_time else "",
                host,
                uri,
                str(obj.get("referer") or ""),
                str(obj.get("user_agent") or ""),
                int(obj.get("status") or 0),
            ])

    out_dir = Path("./var")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / ("mirrorhub_clicks.csv" if days is None else f"mirrorhub_clicks_{days}d.csv")

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "host", "uri", "referer", "user_agent", "status"])
        w.writerows(out_rows)

    return out_path