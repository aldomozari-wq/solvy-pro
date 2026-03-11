import os
import json
import logging
import asyncio

from fastapi import FastAPI, Request, HTTPException
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton

from core.config import TELEGRAM_TOKEN, ADMIN_IDS
from core.database import save_call

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

app = FastAPI()
_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=TELEGRAM_TOKEN)
    return _bot


async def notify(text: str, reply_markup=None):
    """Надіслати сповіщення всім адмінам."""
    if not ADMIN_IDS:
        return
    bot = get_bot()
    for uid in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=uid, text=text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"[notify] uid={uid} error={e}")


def _fmt_phone(p: str) -> str:
    return f"<code>{p}</code>" if p else "—"


def _fmt_dur(secs) -> str:
    try:
        s = int(secs)
        m, s = divmod(s, 60)
        return f"{m}хв {s}с" if m else f"{s}с"
    except Exception:
        return str(secs)


def _build_notification(data: dict) -> str | None:
    status = (data.get("status") or "").upper()
    call_type = (data.get("type") or "").lower()
    callerid = data.get("callerid") or data.get("caller_id") or ""
    destination = data.get("destination") or data.get("called_id") or ""
    agent_name = data.get("agentName") or data.get("agent") or ""
    duration = data.get("duration") or ""
    recording = data.get("recordingFile") or ""
    ext = data.get("extension") or ""

    is_incoming = "incoming" in call_type

    if status == "RINGING":
        if is_incoming:
            return (
                f"📞 <b>Вхідний дзвінок</b>\n"
                f"Від: {_fmt_phone(callerid)}\n"
                f"До: {_fmt_phone(destination or ext)}"
            )
        else:
            return (
                f"📤 <b>Вихідний дзвінок</b>\n"
                f"Від: {_fmt_phone(ext)} ({agent_name})\n"
                f"До: {_fmt_phone(destination)}"
            )

    if status == "ANSWERED":
        if is_incoming:
            return (
                f"✅ <b>Дзвінок прийнято</b>\n"
                f"Від: {_fmt_phone(callerid)}\n"
                f"Прийняв: {agent_name or ext}"
            )
        return None

    if status == "END":
        dur_str = _fmt_dur(duration) if duration else "—"
        rec_str = f"\n🎙 Запис: <code>{recording}</code>" if recording else ""
        if is_incoming:
            return (
                f"📋 <b>Вхідний завершено</b>\n"
                f"Від: {_fmt_phone(callerid)}\n"
                f"Тривалість: {dur_str}{rec_str}"
            )
        else:
            return (
                f"📋 <b>Вихідний завершено</b>\n"
                f"До: {_fmt_phone(destination)}\n"
                f"Агент: {agent_name or ext}\n"
                f"Тривалість: {dur_str}{rec_str}"
            )

    return None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook/koperto")
async def webhook_koperto(request: Request, secret: str = ""):
    """Приймає події від Coperato."""
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        data = await request.json()
    except Exception:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            data = {}

    logger.info(f"[webhook/koperto] {json.dumps(data)}")

    status = (data.get("status") or "").upper()

    if status == "END":
        try:
            save_call(data, source="coperato")
        except Exception as e:
            logger.error(f"[webhook/koperto] save_call error: {e}")

    return {"status": "ok"}


@app.post("/webhook/didpbx")
async def webhook_didpbx(request: Request, secret: str = ""):
    """Приймає події від DIDPBX."""
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        data = await request.json()
    except Exception:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            data = {}

    logger.info(f"[webhook/didpbx] {json.dumps(data)}")

    try:
        save_call(data, source="didpbx")
    except Exception as e:
        logger.error(f"[webhook/didpbx] save_call error: {e}")

    msg = _build_notification(data)
    if msg:
        asyncio.create_task(notify(msg))

    return {"status": "ok"}


@app.get("/webhook/koperto")
@app.get("/webhook/didpbx")
async def webhook_get(request: Request):
    """GET fallback — деякі АТС шлють GET."""
    data = dict(request.query_params)
    path = request.url.path
    source = "coperato" if "koperto" in path else "didpbx"
    logger.info(f"[webhook GET/{source}] {json.dumps(data)}")
    status = (data.get("status") or "").upper()
    if source == "didpbx" or status == "END":
        try:
            save_call(data, source=source)
        except Exception as e:
            logger.error(f"[webhook GET] save_call error: {e}")
    return {"status": "ok"}
