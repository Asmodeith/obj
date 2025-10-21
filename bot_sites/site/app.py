# mirrorhub/site/app.py

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from db.db import get_conn, one
from config import TIMEZONE

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"])
)

app = FastAPI(title="Mirrors (SQLite)")
conn = get_conn()

def _fetch_content():
    row = one(conn, """
        SELECT title, subtitle, contacts, footer
        FROM content
        ORDER BY id DESC LIMIT 1
    """)
    return dict(row) if row else None

def _get_domain_status(host: str):
    row = one(conn, "SELECT status, ssl_ok FROM domain WHERE host=?", (host,))
    return dict(row) if row else None

@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    host_hdr = request.headers.get("host", "")
    host = host_hdr.split(":")[0].lower().strip()
    if not host:
        raise HTTPException(status_code=400, detail="Bad host")

    status = _get_domain_status(host)
    if not status:
        raise HTTPException(status_code=404, detail="Unknown mirror")

    if status["status"] != "active":
        raise HTTPException(status_code=403, detail=f"Mirror status: {status['status']}")

    content = _fetch_content()
    if content:
        contacts = json.loads(content["contacts"])
        ctx = {
            "title": content["title"],
            "subtitle": content["subtitle"],
            "contacts": contacts,
            "footer": content["footer"],
            "now": datetime.now(ZoneInfo(TIMEZONE)),
        }
    else:
        ctx = {
            "title": "Контакты",
            "subtitle": "Свяжитесь с нами:",
            "contacts": [],
            "footer": "",
            "now": datetime.now(ZoneInfo(TIMEZONE)),
        }

    template = jinja_env.get_template("index.html")
    return template.render(**ctx)

# трекинг кликов — видно в Nginx JSON-логе по path=/go/telegram/<username>
@app.get("/go/telegram/{username}")
def go_telegram(username: str):
    return RedirectResponse(url=f"https://t.me/{username}", status_code=302)
