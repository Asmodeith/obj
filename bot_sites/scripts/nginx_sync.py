# scripts/nginx_sync.py
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from config import (
    SQLITE_PATH,
    BACKEND_UPSTREAM,
    NGINX_PER_DOMAIN_DIR,   # Path("/etc/nginx/sites-mirrorhub.d")
    NGINX_AUX_DIR,          # Path("/etc/nginx/mirrorhub")
    NGINX_LOG_FILE,         # Path("/var/log/nginx/mirrorhub_redirect.log")
)

LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")
LOG_FORMAT_NAME = "json_mirrorhub_v1"  # объявлен в /etc/nginx/sites-available/mirrorhub.conf
ACME_WEBROOT = Path("/var/www/certbot")  # для --webroot

# ============ helpers ============

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(SQLITE_PATH)
    c.row_factory = sqlite3.Row
    return c

def _cert_paths(host: str) -> Tuple[Path, Path]:
    base = LETSENCRYPT_LIVE / host
    return base / "fullchain.pem", base / "privkey.pem"

def _has_certs(host: str) -> bool:
    fullchain, key = _cert_paths(host)
    return fullchain.exists() and key.exists()

def _ensure_dirs():
    # каталоги для nginx-конфигов
    NGINX_PER_DOMAIN_DIR.mkdir(parents=True, exist_ok=True)
    NGINX_AUX_DIR.mkdir(parents=True, exist_ok=True)

    # ACME webroot (под root — достаточно прав 0755)
    acme_dir = ACME_WEBROOT / ".well-known" / "acme-challenge"
    acme_dir.mkdir(parents=True, exist_ok=True)
    # безопасные права (root → читает nginx без проблем)
    try:
        os.chmod(ACME_WEBROOT, 0o755)
        os.chmod(ACME_WEBROOT / ".well-known", 0o755)
        os.chmod(acme_dir, 0o755)
    except Exception:
        pass

def _nginx_test_reload() -> Tuple[bool, str]:
    test = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    out = (test.stdout or "") + (test.stderr or "")
    if test.returncode != 0:
        return False, "nginx -t FAILED:\n" + out.strip()
    reload_ = subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, text=True)
    if reload_.returncode != 0:
        return False, "nginx reload FAILED:\n" + ((reload_.stderr or "") or (reload_.stdout or "")).strip()
    return True, "nginx reload: OK"

def _write(path: Path, content: str):
    path.write_text(content, encoding="utf-8")

# ============ renderers ============

def _render_http_server(host: str, status: str, ssl_ok: int) -> str:
    """
    :80
      - всегда access_log
      - всегда location /.well-known/acme-challenge/ на ACME_WEBROOT
      - если ssl_ok=1: 301 → https
      - если ssl_ok=0: всё остальное 404 (чтобы certbot мог пройти)
    """
    access_log_line = f"    access_log {str(NGINX_LOG_FILE)} {LOG_FORMAT_NAME};"
    acme = (
        "    location /.well-known/acme-challenge/ {\n"
        f"        root {ACME_WEBROOT};\n"
        "        try_files $uri =404;\n"
        "    }\n"
    )

    if ssl_ok:
        return (
            f"server {{\n"
            f"    listen 80;\n"
            f"    server_name {host};\n"
            f"{access_log_line}\n"
            f"{acme}"
            f"    return 301 https://$host$request_uri;\n"
            f"}}\n"
        )
    else:
        return (
            f"server {{\n"
            f"    listen 80;\n"
            f"    server_name {host};\n"
            f"{access_log_line}\n"
            f"{acme}"
            f"    location / {{ return 404; }}\n"
            f"}}\n"
        )

def _render_https_server_active(host: str) -> str:
    """
    :443 ssl — полноценный прокси на BACKEND_UPSTREAM
    """
    fullchain, key = _cert_paths(host)
    access_log_line = f"    access_log {str(NGINX_LOG_FILE)} {LOG_FORMAT_NAME};"
    return (
        f"server {{\n"
        f"    listen 443 ssl http2;\n"
        f"    server_name {host};\n"
        f"{access_log_line}\n"
        f"    ssl_certificate {fullchain};\n"
        f"    ssl_certificate_key {key};\n"
        f"    include /etc/letsencrypt/options-ssl-nginx.conf;\n"
        f"    ssl_stapling on;\n"
        f"    ssl_stapling_verify on;\n"
        f"\n"
        f"    location / {{\n"
        f"        proxy_pass {BACKEND_UPSTREAM};\n"
        f"        proxy_set_header Host $host;\n"
        f"        proxy_set_header X-Real-IP $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"        proxy_buffering off;\n"
        f"        proxy_read_timeout 60s;\n"
        f"    }}\n"
        f"}}\n"
    )

def _render_https_server_forbidden(host: str) -> str:
    """
    :443 ssl — для hot/blocked делаем 403 (если серт уже есть)
    """
    fullchain, key = _cert_paths(host)
    access_log_line = f"    access_log {str(NGINX_LOG_FILE)} {LOG_FORMAT_NAME};"
    if not _has_certs(host):
        return ""
    return (
        f"server {{\n"
        f"    listen 443 ssl http2;\n"
        f"    server_name {host};\n"
        f"{access_log_line}\n"
        f"    ssl_certificate {fullchain};\n"
        f"    ssl_certificate_key {key};\n"
        f"    include /etc/letsencrypt/options-ssl-nginx.conf;\n"
        f"    return 403;\n"
        f"}}\n"
    )

def _render_domain_conf(host: str, status: str, ssl_ok: int) -> str:
    parts: List[str] = []
    parts.append(_render_http_server(host, status, ssl_ok))
    if ssl_ok and _has_certs(host):
        if status == "active":
            parts.append(_render_https_server_active(host))
        else:
            parts.append(_render_https_server_forbidden(host))
    return "\n".join([p for p in parts if p])

# ============ main sync ============

def _load_domains() -> List[Dict]:
    with _conn() as c:
        rows = c.execute("SELECT host, status, ssl_ok FROM domain ORDER BY id ASC").fetchall()
    return [dict(r) for r in rows]

def _write_domains_map(hosts: List[str]):
    dst = NGINX_AUX_DIR / "domains_map.conf"
    lines = [f"{h} 1;" for h in sorted(set(hosts))]
    dst.parent.mkdir(parents=True, exist_ok=True)
    _write(dst, "\n".join(lines) + ("\n" if lines else ""))

def sync_all_domains() -> str:
    """
    Генерирует все per-domain конфиги, domains_map.conf,
    гарантирует ACME webroot, валидирует и перезагружает nginx.
    """
    _ensure_dirs()

    domains = _load_domains()
    if not domains:
        _write_domains_map([])
        return "no domains in DB"

    written = 0
    hosts_for_map: List[str] = []

    for d in domains:
        host = d["host"].strip()
        status = (d["status"] or "hot").strip()
        ssl_ok = int(d.get("ssl_ok", 0))
        if not host:
            continue

        conf_text = _render_domain_conf(host, status, ssl_ok)
        dst = NGINX_PER_DOMAIN_DIR / f"{host}.conf"
        _write(dst, conf_text)
        written += 1
        hosts_for_map.append(host)

    _write_domains_map(hosts_for_map)

    ok, msg = _nginx_test_reload()
    header = f"generated: {written} files, domains_map: {len(hosts_for_map)}"
    return f"{header}\n{msg}"

# ============ CLI ============

if __name__ == "__main__":
    print(sync_all_domains())
