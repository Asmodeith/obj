from typing import Optional, Iterable, Sequence
from sqlalchemy import select, update, delete, func
from sqlalchemy.orm import Session
from mirrorhub.core.models import Base, Setting, Token, BotInstance, BotUser, SentMessage, BroadcastLog
from mirrorhub.core.db import engine
from mirrorhub.core.models import BotUser
from datetime import datetime, timedelta
from sqlalchemy import select, update, delete, func, text as sql_text


def init_db():
    Base.metadata.create_all(bind=engine)


    with engine.begin() as conn:
        try:
            rows = conn.exec_driver_sql("PRAGMA table_info(sent_messages)").fetchall()
            colnames = {row[1] for row in rows}  # row[1] = имя колонки
        except Exception:
            colnames = set()

        if "created_at" not in colnames:

            conn.exec_driver_sql("ALTER TABLE sent_messages ADD COLUMN created_at TEXT")

            conn.exec_driver_sql(
                "UPDATE sent_messages SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"
            )



def get_setting(db: Session, key: str) -> Optional[str]:
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    return row.value if row else None

def set_setting(db: Session, key: str, value: Optional[str]):
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if not row:
        row = Setting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()


def add_token(db: Session, token: str, note: Optional[str] = None):
    t = Token(token=token.strip(), status="free", note=note)
    db.add(t); db.commit(); db.refresh(t)
    return t

def add_tokens_bulk(db: Session, tokens: Sequence[str]) -> tuple[int, list[str]]:
    added = 0
    skipped: list[str] = []
    for t in tokens:
        s = t.strip()
        if not s:
            continue
        try:
            db.add(Token(token=s, status="free"))
            added += 1
        except Exception:
            skipped.append(s)
    db.commit()
    return added, skipped

def list_tokens(db: Session):
    return db.execute(select(Token).order_by(Token.id)).scalars().all()

def next_free_token(db: Session) -> Optional[Token]:
    return db.execute(
        select(Token).where(Token.status == "free").order_by(Token.id).limit(1)
    ).scalars().first()

def mark_token_status(db: Session, token_id: int, status: str):
    db.execute(update(Token).where(Token.id == token_id).values(status=status))
    db.commit()

def delete_tokens_by_ids(db: Session, ids: Sequence[int]) -> tuple[int, list[int]]:
    ids = list(set(int(i) for i in ids if isinstance(i, (int, str)) and str(i).isdigit()))
    if not ids:
        return 0, []
    in_use_rows = db.execute(
        select(Token.id).where(Token.id.in_(ids), Token.status == "in_use")
    ).scalars().all()
    to_delete = [i for i in ids if i not in in_use_rows]
    if to_delete:
        db.execute(delete(Token).where(Token.id.in_(to_delete)))
        db.commit()
    return len(to_delete), in_use_rows


def create_bot_instance(db: Session, token: Token) -> BotInstance:
    mark_token_status(db, token.id, "in_use")
    b = BotInstance(token_id=token.id)
    db.add(b); db.commit(); db.refresh(b)
    return b

def list_bots(db: Session):
    return db.execute(select(BotInstance).order_by(BotInstance.id)).scalars().all()

def get_bot(db: Session, bot_id: int) -> Optional[BotInstance]:
    return db.get(BotInstance, bot_id)

def delete_bot_completely(db: Session, bot_id: int):
    b = db.get(BotInstance, bot_id)
    if not b:
        return
    if b.token_id:
        mark_token_status(db, b.token_id, "free")
    db.execute(delete(BotInstance).where(BotInstance.id == bot_id))
    db.commit()

def set_bot_meta(db: Session, bot_id: int, username: str, link: str):
    db.execute(update(BotInstance).where(BotInstance.id == bot_id).values(username=username, link=link))
    db.commit()

def set_bot_running(db: Session, bot_id: int, is_running: bool, last_error: Optional[str] = None):
    db.execute(update(BotInstance).where(BotInstance.id == bot_id).values(is_running=is_running, last_error=last_error))
    db.commit()

def inc_stat_start(db: Session, bot_id: int):
    db.execute(update(BotInstance).where(BotInstance.id == bot_id).values(starts=BotInstance.starts + 1))
    db.commit()

def inc_stat_contacts(db: Session, bot_id: int):
    db.execute(update(BotInstance).where(BotInstance.id == bot_id).values(contacts_clicks=BotInstance.contacts_clicks + 1))
    db.commit()


def upsert_user(db: Session, bot_id: int, user_id: int, username: Optional[str]):
    row = db.execute(select(BotUser).where(BotUser.bot_id == bot_id, BotUser.user_id == user_id)).scalar_one_or_none()
    if row:
        row.username = username
    else:
        row = BotUser(bot_id=bot_id, user_id=user_id, username=username)
        db.add(row)
    db.commit()

def get_bot_users(db: Session, bot_id: int) -> list[BotUser]:
    return db.execute(select(BotUser).where(BotUser.bot_id == bot_id)).scalars().all()


def add_sent_message(db: Session, bot_id: int, chat_id: int, message_id: int, kind: str):
    db.add(SentMessage(bot_id=bot_id, chat_id=chat_id, message_id=message_id, kind=kind))
    db.commit()

def iter_sent_msgs(db: Session, kind: str) -> Iterable[SentMessage]:
    return db.execute(select(SentMessage).where(SentMessage.kind == kind)).scalars().all()


def add_broadcast_log(db: Session, text: Optional[str], photo_path: Optional[str], total: int, ok: int, fail: int):
    db.add(BroadcastLog(text=text, photo_path=photo_path, total=total, ok=ok, fail=fail))
    db.commit()


def aggregate_stats(db: Session):
    row = db.execute(
        select(
            func.count(BotInstance.id),
            func.coalesce(func.sum(BotInstance.starts), 0),
            func.coalesce(func.sum(BotInstance.contacts_clicks), 0),
        )
    ).first()
    total_bots, total_starts, total_contacts = row or (0, 0, 0)
    return int(total_bots or 0), int(total_starts or 0), int(total_contacts or 0)

def get_total_users(db: Session) -> int:

    val = db.execute(select(func.count(func.distinct(BotUser.user_id)))).scalar()
    return int(val or 0)

def get_user_counts_by_bot(db: Session) -> dict[int, int]:

    rows = db.execute(
        select(BotUser.bot_id, func.count(func.distinct(BotUser.user_id)))
        .group_by(BotUser.bot_id)
    ).all()
    return {int(bot_id): int(cnt) for bot_id, cnt in rows}


def get_total_users_period(db: Session, days: int | None) -> int:
    q = select(func.count(func.distinct(SentMessage.chat_id))).where(
        SentMessage.kind == "start_template"
    )
    if days is not None:
        since = datetime.utcnow() - timedelta(days=days)
        # если колонка есть — фильтруем по дате
        if hasattr(SentMessage, "created_at"):
            q = q.where(SentMessage.created_at >= since)
    val = db.execute(q).scalar()
    return int(val or 0)

def get_user_counts_by_bot_period(db: Session, days: int | None) -> dict[int, int]:
    q = (
        select(
            SentMessage.bot_id,
            func.count(func.distinct(SentMessage.chat_id))
        )
        .where(SentMessage.kind == "start_template")
        .group_by(SentMessage.bot_id)
    )
    if days is not None and hasattr(SentMessage, "created_at"):
        since = datetime.utcnow() - timedelta(days=days)
        q = q.where(SentMessage.created_at >= since)
    rows = db.execute(q).all()
    return {int(bot_id): int(cnt) for bot_id, cnt in rows}