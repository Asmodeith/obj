# mirrorhub/db/db.py

import sqlite3
from pathlib import Path
from typing import Iterable, Any, Optional, Dict

from config import SQLITE_PATH

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(SQLITE_PATH), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        schema = Path(__file__).resolve().parent / "schema.sql"
        conn.executescript(schema.read_text(encoding="utf-8"))

def q(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()):
    return conn.execute(sql, params)

def one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    cur = conn.execute(sql, params)
    return cur.fetchone()

def all(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()):
    cur = conn.execute(sql, params)
    return cur.fetchall()
