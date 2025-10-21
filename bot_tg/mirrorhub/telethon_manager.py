
import asyncio
import re
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from mirrorhub.config import SESSIONS_DIR, TELETHON_SESSION_NAME
from mirrorhub.core.repo import set_setting, get_setting
from mirrorhub.core.db import SessionLocal

API_ID_SETTING = "telethon_api_id"
API_HASH_SETTING = "telethon_api_hash"
OWNER_ID_SETTING = "telethon_owner_id"

def _session_name_str() -> str:
    return (SESSIONS_DIR / TELETHON_SESSION_NAME).as_posix()

def _session_file_path() -> Path:
    return SESSIONS_DIR / f"{TELETHON_SESSION_NAME}.session"

def _normalize_phone(raw: str) -> str:
    s = re.sub(r"\D", "", raw)
    if raw.strip().startswith("+"):
        return f"+{s}"
    return s

class TelethonManager:
    def __init__(self):
        self.client: TelegramClient | None = None

    async def login_dialog(self, send_text):
        with SessionLocal() as db:
            api_id = get_setting(db, API_ID_SETTING)
            api_hash = get_setting(db, API_HASH_SETTING)

        if not api_id:
            await send_text("Отправь <b>API_ID</b>:")
            api_id = (await self._wait_input()).strip()
        if not api_hash:
            await send_text("Отправь <b>API_HASH</b>:")
            api_hash = (await self._wait_input()).strip()

        with SessionLocal() as db:
            set_setting(db, API_ID_SETTING, api_id)
            set_setting(db, API_HASH_SETTING, api_hash)


        self.client = TelegramClient(_session_name_str(), int(api_id), api_hash)

        await send_text("Отправь <b>номер</b> (в формате +7...):")
        phone_raw = (await self._wait_input()).strip()
        phone = _normalize_phone(phone_raw)

        await self.client.connect()
        if not await self.client.is_user_authorized():
            await self.client.send_code_request(phone)
            await send_text("Пришёл код. Введи код:")
            code = (await self._wait_input()).strip()
            try:
                await self.client.sign_in(phone=phone, code=code)
            except SessionPasswordNeededError:
                await send_text("Требуется пароль 2FA. Введи пароль:")
                password = (await self._wait_input()).strip()
                await self.client.sign_in(password=password)

        me = await self.client.get_me()
        with SessionLocal() as db:
            set_setting(db, OWNER_ID_SETTING, str(me.id))


        await self.client.disconnect()

        await send_text(f"Готово. Владелец: <code>{me.id}</code> (@{me.username or '—'}).")

    async def status(self):
        p = _session_file_path()
        exists = p.exists()

        with SessionLocal() as db:
            api_id = get_setting(db, API_ID_SETTING)
            api_hash = get_setting(db, API_HASH_SETTING)

        owner_id = None
        username = None
        authorized = False

        if api_id and api_hash:
            client = TelegramClient(_session_name_str(), int(api_id), api_hash)
            await client.connect()
            try:
                authorized = await client.is_user_authorized()
                if authorized:
                    me = await client.get_me()
                    owner_id = me.id
                    username = me.username
            finally:
                await client.disconnect()

        return {
            "session_exists": exists,
            "authorized": authorized,
            "owner_id": owner_id,
            "username": username,
        }

    async def remove_session(self):
        base = _session_file_path()
        candidates = [
            base,
            Path(str(base) + "-journal"),
            Path(str(base) + "-wal"),
            Path(str(base) + "-shm"),
        ]
        for f in candidates:
            try:
                if f.exists():
                    f.unlink()
            except Exception:
                pass
        with SessionLocal() as db:
            set_setting(db, OWNER_ID_SETTING, None)


    async def _wait_input(self) -> str:
        fut = asyncio.get_running_loop().create_future()
        return await fut
