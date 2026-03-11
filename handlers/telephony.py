import asyncio
import html
from io import BytesIO

import aiohttp
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton

from core.config import ADMIN_IDS, COPERATO_BASE_URL, CROCO_API_KEY
from core.database import get_coperato_stats, search_coperato_recordings, debug_coperato_db
from core.integrations.didpbx import search_recordings, download_recording, get_stats
from core.integrations.voiso import (
    get_stats as get_voiso_stats, voiso_raw,
    VOISO_CLUSTER, VOISO_API_KEY,
    search_voiso_recordings, download_voiso_recording, debug_recording_urls,
)
from core.integrations.crococalls import (
    get_stats as get_croco_stats,
    search_recordings as search_croco_recordings,
    download_recording as download_croco_recording,
    croco_raw,
    CROCO_BASE_URL,
)
from core.integrations.coperato import download_recording as download_coperato_recording
from core.utils import transcribe_voice

# ──────────────────────────────────────────────
# Keyboards & labels
# ──────────────────────────────────────────────

STATS_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("Сьогодні", callback_data="stats:today"),
    InlineKeyboardButton("Вчора",    callback_data="stats:yesterday"),
    InlineKeyboardButton("Тиждень",  callback_data="stats:week"),
    InlineKeyboardButton("Місяць",   callback_data="stats:month"),
]])

VSTATS_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("Сьогодні", callback_data="vstats:today"),
    InlineKeyboardButton("Вчора",    callback_data="vstats:yesterday"),
    InlineKeyboardButton("Тиждень",  callback_data="vstats:week"),
    InlineKeyboardButton("Місяць",   callback_data="vstats:month"),
]])

CSTATS_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("Сьогодні", callback_data="cstats:today"),
    InlineKeyboardButton("Вчора",    callback_data="cstats:yesterday"),
    InlineKeyboardButton("Тиждень",  callback_data="cstats:week"),
    InlineKeyboardButton("Місяць",   callback_data="cstats:month"),
]])

KSTATS_KEYBOARD = InlineKeyboardMarkup([[
    InlineKeyboardButton("Сьогодні", callback_data="kstats:today"),
    InlineKeyboardButton("Вчора",    callback_data="kstats:yesterday"),
    InlineKeyboardButton("Тиждень",  callback_data="kstats:week"),
    InlineKeyboardButton("Місяць",   callback_data="kstats:month"),
]])

STATUS_LABELS = {
    "ANSWER":     ("✅", "Відповіли"),
    "CANCEL":     ("📵", "Скасовано"),
    "NOANSWER":   ("⏱", "Не відповіли"),
    "BUSY":       ("🔒", "Зайнято"),
    "CONGESTION": ("❌", "Перевантаження"),
}

VOISO_STATUS_LABELS = {
    "answered":         ("✅", "Відповіли"),
    "missed":           ("📵", "Пропущено"),
    "no_answer":        ("⏱", "Не відповіли"),
    "busy":             ("🔒", "Зайнято"),
    "abandoned":        ("🚶", "Покинуто"),
    "dialer_abandoned": ("📵", "Дайлер скасовано"),
    "machine_answered": ("🤖", "Автовідповідь"),
    "failed":           ("❌", "Помилка"),
    "rejected":         ("🚫", "Відхилено"),
    "system_abandoned": ("💨", "Системно скасовано"),
    "system_reject":    ("🚫", "Системний відмов"),
    "answered_by_vm":   ("📨", "Голосова пошта"),
}

PERIOD_LABELS = {
    "today":     "сьогодні",
    "yesterday": "вчора",
    "week":      "поточний тиждень",
    "month":     "поточний місяць",
}

PERIOD_ALIAS = {
    "сьогодні": "today", "сегодня": "today", "день": "today", "today": "today",
    "вчора": "yesterday", "вчера": "yesterday", "yesterday": "yesterday",
    "тиждень": "week", "неделя": "week", "week": "week",
    "місяць": "month", "месяц": "month", "month": "month",
}

# ──────────────────────────────────────────────
# Formatting helpers
# ──────────────────────────────────────────────

def _fmt_dur(secs: int) -> str:
    m, s = divmod(secs, 60)
    return f"{m:02d}:{s:02d}"


def _format_stats(stats: dict, period_label: str) -> str:
    total = stats["total"]
    by_status = stats["by_status"]
    by_duration = stats.get("by_duration", {})

    pct = lambda n: f"{round(n / total * 100)}%" if total > 0 else "0%"

    lines = [f"📊 <b>Статистика — {period_label}:</b>\n", f"📞 Всього дзвінків: <b>{total}</b>\n"]

    for key in ("ANSWER", "CANCEL", "NOANSWER", "BUSY", "CONGESTION"):
        count = by_status.get(key, 0)
        if count or key == "ANSWER":
            icon, label = STATUS_LABELS[key]
            dur = by_duration.get(key, 0)
            dur_str = f" — {_fmt_dur(dur)}" if dur > 0 else ""
            lines.append(f"{icon} {label}: {count} ({pct(count)}){dur_str}")

    known = set(STATUS_LABELS.keys())
    for key, count in by_status.items():
        if key not in known and count:
            dur = by_duration.get(key, 0)
            dur_str = f" — {_fmt_dur(dur)}" if dur > 0 else ""
            lines.append(f"❓ {key}: {count} ({pct(count)}){dur_str}")

    return "\n".join(lines)


def _format_voiso_stats(stats: dict, period_label: str) -> str:
    total = stats["total"]
    by_status = stats["by_status"]
    by_duration = stats.get("by_duration", {})

    pct = lambda n: f"{round(n / total * 100)}%" if total > 0 else "0%"

    lines = [f"📊 <b>Voiso — {period_label}:</b>\n", f"📞 Всього дзвінків: <b>{total}</b>\n"]

    for key in ("answered", "missed", "no_answer", "busy", "abandoned",
                "dialer_abandoned", "machine_answered", "failed", "rejected",
                "system_abandoned", "system_reject", "answered_by_vm"):
        count = by_status.get(key, 0)
        if count or key == "answered":
            icon, label = VOISO_STATUS_LABELS[key]
            dur = by_duration.get(key, 0)
            dur_str = f" — {_fmt_dur(dur)}" if dur > 0 else ""
            lines.append(f"{icon} {label}: {count} ({pct(count)}){dur_str}")

    known = set(VOISO_STATUS_LABELS.keys())
    for key, count in by_status.items():
        if key not in known and count:
            dur = by_duration.get(key, 0)
            dur_str = f" — {_fmt_dur(dur)}" if dur > 0 else ""
            lines.append(f"❓ {key}: {count} ({pct(count)}){dur_str}")

    return "\n".join(lines)


def _format_cstats(stats: dict, period_label: str) -> str:
    total = stats["total"]
    by_status = stats["by_status"]
    by_duration = stats.get("by_duration", {})
    by_type = stats.get("by_type", {})

    pct = lambda n: f"{round(n / total * 100)}%" if total > 0 else "0%"

    incoming = by_type.get("incoming", 0)
    outgoing = by_type.get("outgoing", 0)
    answered = by_status.get("answered", 0)
    missed = by_status.get("missed", 0)
    ans_dur = by_duration.get("answered", 0)

    lines = [
        f"📊 <b>Coperato — {period_label}:</b>\n",
        f"📞 Всього дзвінків: <b>{total}</b>",
        f"📥 Вхідні: {incoming}  📤 Вихідні: {outgoing}\n",
        f"✅ Відповіли: {answered} ({pct(answered)})" + (f" — {_fmt_dur(ans_dur)}" if ans_dur else ""),
        f"📵 Пропущено: {missed} ({pct(missed)})",
    ]

    known = {"answered", "missed"}
    for key, count in by_status.items():
        if key not in known and count:
            dur = by_duration.get(key, 0)
            dur_str = f" — {_fmt_dur(dur)}" if dur > 0 else ""
            lines.append(f"❓ {key}: {count} ({pct(count)}){dur_str}")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# Stats commands
# ──────────────────────────────────────────────

async def stats_command(update: Update, context):
    """/stats [today|yesterday|week|month]"""
    arg = context.args[0] if context.args else "today"
    period = PERIOD_ALIAS.get(arg, "today")
    status_msg = await update.effective_message.reply_text("📊 Збираю статистику...")
    try:
        stats = await get_stats(period)
    except Exception as e:
        print(f"[ERROR] get_stats error={e}")
        await status_msg.edit_text("😔 Щось пішло не так, спробуй ще раз")
        return
    await status_msg.edit_text(_format_stats(stats, PERIOD_LABELS.get(period, period)), parse_mode="HTML", reply_markup=STATS_KEYBOARD)


async def vstats_command(update: Update, context):
    """/vstats [today|yesterday|week|month] — Voiso статистика"""
    arg = context.args[0] if context.args else "today"
    period = PERIOD_ALIAS.get(arg, "today")
    status_msg = await update.effective_message.reply_text("📊 Збираю статистику Voiso...")
    try:
        stats = await get_voiso_stats(period)
    except Exception as e:
        print(f"[ERROR] get_voiso_stats error={e}")
        await status_msg.edit_text(f"😔 Помилка: <code>{html.escape(str(e))}</code>", parse_mode="HTML")
        return
    await status_msg.edit_text(_format_voiso_stats(stats, PERIOD_LABELS.get(period, period)), parse_mode="HTML", reply_markup=VSTATS_KEYBOARD)


async def cstats_command(update: Update, context):
    """/cstats [today|yesterday|week|month] — Coperato статистика"""
    arg = context.args[0] if context.args else "today"
    period = PERIOD_ALIAS.get(arg, "today")
    status_msg = await update.effective_message.reply_text("📊 Збираю статистику Coperato...")
    try:
        stats = get_coperato_stats(period)
    except Exception as e:
        print(f"[ERROR] get_coperato_stats error={e}")
        await status_msg.edit_text(f"😔 Помилка: <code>{html.escape(str(e))}</code>", parse_mode="HTML")
        return
    await status_msg.edit_text(_format_cstats(stats, PERIOD_LABELS.get(period, period)), parse_mode="HTML", reply_markup=CSTATS_KEYBOARD)


async def handle_stats_callback(update: Update, context):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("stats:"):
        period = query.data.split(":", 1)[1]
        try:
            stats = await get_stats(period)
        except Exception as e:
            print(f"[ERROR] get_stats callback error={e}")
            await query.edit_message_text("😔 Щось пішло не так")
            return
        await query.edit_message_text(_format_stats(stats, PERIOD_LABELS.get(period, period)), parse_mode="HTML", reply_markup=STATS_KEYBOARD)

    elif query.data.startswith("cstats:"):
        period = query.data.split(":", 1)[1]
        try:
            stats = get_coperato_stats(period)
        except Exception as e:
            print(f"[ERROR] get_coperato_stats callback error={e}")
            await query.edit_message_text("😔 Щось пішло не так")
            return
        await query.edit_message_text(_format_cstats(stats, PERIOD_LABELS.get(period, period)), parse_mode="HTML", reply_markup=CSTATS_KEYBOARD)

    elif query.data.startswith("kstats:"):
        period = query.data.split(":", 1)[1]
        try:
            stats = await get_croco_stats(period)
        except Exception as e:
            await query.edit_message_text("😔 Щось пішло не так")
            return
        await query.edit_message_text(
            _format_croco_stats(stats, PERIOD_LABELS.get(period, period)),
            parse_mode="HTML",
            reply_markup=KSTATS_KEYBOARD,
        )

    elif query.data.startswith("vstats:"):
        period = query.data.split(":", 1)[1]
        try:
            stats = await get_voiso_stats(period)
        except Exception as e:
            print(f"[ERROR] get_voiso_stats callback error={e}")
            await query.edit_message_text("😔 Щось пішло не так")
            return
        await query.edit_message_text(_format_voiso_stats(stats, PERIOD_LABELS.get(period, period)), parse_mode="HTML", reply_markup=VSTATS_KEYBOARD)

    elif query.data.startswith("dl_rec:"):
        recording = query.data.split(":", 1)[1]
        await query.answer("⬇️ Завантажую запис...")
        try:
            audio_bytes = await download_recording(recording)
            print(f"[DEBUG] recording size={len(audio_bytes)} first_bytes={audio_bytes[:8].hex()}")
            if len(audio_bytes) < 100:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"😔 API повернув {len(audio_bytes)} байт:\n<code>{html.escape(audio_bytes.decode('utf-8', errors='replace'))}</code>",
                    parse_mode="HTML",
                )
                return
            await context.bot.send_audio(
                chat_id=query.message.chat_id,
                audio=BytesIO(audio_bytes),
                filename="recording.mp3",
                caption="🎙️ Запис дзвінка",
            )
        except Exception as e:
            print(f"[ERROR] download recording={e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"😔 Помилка завантаження: <code>{html.escape(str(e))}</code>",
                parse_mode="HTML",
            )

    elif query.data.startswith("tr_rec:"):
        recording = query.data.split(":", 1)[1]
        await query.answer("📝 Транскрибую...")
        try:
            audio_bytes = await download_recording(recording)
            tmp_path = f"/tmp/rec_{query.from_user.id}.mp3"
            with open(tmp_path, "wb") as f:
                f.write(audio_bytes)
            text, lang = await transcribe_voice(tmp_path)
            context.user_data["last_transcript"] = text
            context.user_data["last_transcript_lang"] = lang
            LANG_FLAG = {"uk": "🇺🇦", "ru": "🇷🇺", "en": "🇬🇧", "de": "🇩🇪", "pl": "🇵🇱"}
            flag = LANG_FLAG.get(lang, "🌐")
            lang_names = {"uk": "українська", "ru": "русский", "en": "english", "de": "deutsch", "pl": "polski"}
            lang_name = lang_names.get(lang, lang)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"📝 <b>Транскрипція:</b> {flag} <i>{lang_name}</i>\n\n"
                    f"{html.escape(text)}\n\n"
                    f"<i>Напиши «переклади на англійську», «проаналізуй розмову» або будь-що інше</i>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"[ERROR] transcription={e}")
            await context.bot.send_message(chat_id=query.message.chat_id, text="😔 Не вдалося транскрибувати")

    elif query.data.startswith("vdl_rec:"):
        uuid = query.data.split(":", 1)[1]
        await query.answer("⬇️ Завантажую запис Voiso...")
        try:
            status_code, audio_bytes = await download_voiso_recording(uuid)
            is_html = audio_bytes[:20].lstrip().lower().startswith(b"<!doctype") or audio_bytes[:10].lstrip().lower().startswith(b"<html")
            print(f"[DEBUG] voiso recording uuid={uuid} status={status_code} size={len(audio_bytes)} is_html={is_html}")
            if status_code != 200 or len(audio_bytes) < 100 or is_html:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"😔 Voiso API повернув HTTP {status_code} ({len(audio_bytes)} байт):\n<code>{html.escape(audio_bytes[:300].decode('utf-8', errors='replace'))}</code>",
                    parse_mode="HTML",
                )
                return
            await context.bot.send_audio(
                chat_id=query.message.chat_id,
                audio=BytesIO(audio_bytes),
                filename="voiso_recording.mp3",
                caption="🎙️ Запис дзвінка (Voiso)",
            )
        except Exception as e:
            print(f"[ERROR] voiso download recording={e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"😔 Помилка завантаження: <code>{html.escape(str(e))}</code>",
                parse_mode="HTML",
            )

    elif query.data.startswith("vtr_rec:"):
        uuid = query.data.split(":", 1)[1]
        await query.answer("📝 Транскрибую Voiso запис...")
        try:
            status_code, audio_bytes = await download_voiso_recording(uuid)
            if status_code != 200 or len(audio_bytes) < 100:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"😔 Voiso API повернув HTTP {status_code} ({len(audio_bytes)} байт)",
                )
                return
            tmp_path = f"/tmp/vrec_{query.from_user.id}.mp3"
            with open(tmp_path, "wb") as f:
                f.write(audio_bytes)
            text, lang = await transcribe_voice(tmp_path)
            context.user_data["last_transcript"] = text
            context.user_data["last_transcript_lang"] = lang
            LANG_FLAG = {"uk": "🇺🇦", "ru": "🇷🇺", "en": "🇬🇧", "de": "🇩🇪", "pl": "🇵🇱"}
            flag = LANG_FLAG.get(lang, "🌐")
            lang_names = {"uk": "українська", "ru": "русский", "en": "english", "de": "deutsch", "pl": "polski"}
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"📝 <b>Транскрипція Voiso:</b> {flag} <i>{lang_names.get(lang, lang)}</i>\n\n"
                    f"{html.escape(text)}\n\n"
                    f"<i>Напиши «переклади на англійську», «проаналізуй розмову» або будь-що інше</i>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"[ERROR] voiso transcription={e}")
            await context.bot.send_message(chat_id=query.message.chat_id, text="😔 Не вдалося транскрибувати")

    elif query.data.startswith("kdl_rec:"):
        idx = query.data.split(":", 1)[1]
        audio_url = context.user_data.get("krec_urls", {}).get(idx)
        await query.answer("⬇️ Завантажую запис CrocoCalls...")
        if not audio_url:
            await context.bot.send_message(chat_id=query.message.chat_id, text="😔 URL запису не знайдено, спробуй /krec знову")
            return
        try:
            status_code, audio_bytes = await download_croco_recording(audio_url)
            if status_code != 200 or len(audio_bytes) < 100:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"😔 CrocoCalls повернув HTTP {status_code} ({len(audio_bytes)} байт)",
                )
                return
            await context.bot.send_audio(
                chat_id=query.message.chat_id,
                audio=BytesIO(audio_bytes),
                filename="croco_recording.mp3",
                caption="🎙️ Запис дзвінка (CrocoCalls)",
            )
        except Exception as e:
            print(f"[ERROR] croco download={e}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"😔 Помилка завантаження: <code>{html.escape(str(e))}</code>",
                parse_mode="HTML",
            )

    elif query.data.startswith("ktr_rec:"):
        idx = query.data.split(":", 1)[1]
        audio_url = context.user_data.get("krec_urls", {}).get(idx)
        await query.answer("📝 Транскрибую CrocoCalls запис...")
        if not audio_url:
            await context.bot.send_message(chat_id=query.message.chat_id, text="😔 URL запису не знайдено, спробуй /krec знову")
            return
        try:
            status_code, audio_bytes = await download_croco_recording(audio_url)
            if status_code != 200 or len(audio_bytes) < 100:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"😔 CrocoCalls повернув HTTP {status_code} ({len(audio_bytes)} байт)",
                )
                return
            tmp_path = f"/tmp/krec_{query.from_user.id}.mp3"
            with open(tmp_path, "wb") as f:
                f.write(audio_bytes)
            text, lang = await transcribe_voice(tmp_path)
            context.user_data["last_transcript"] = text
            context.user_data["last_transcript_lang"] = lang
            LANG_FLAG = {"uk": "🇺🇦", "ru": "🇷🇺", "en": "🇬🇧", "de": "🇩🇪", "pl": "🇵🇱"}
            flag = LANG_FLAG.get(lang, "🌐")
            lang_names = {"uk": "українська", "ru": "русский", "en": "english", "de": "deutsch", "pl": "polski"}
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"📝 <b>Транскрипція CrocoCalls:</b> {flag} <i>{lang_names.get(lang, lang)}</i>\n\n"
                    f"{html.escape(text)}\n\n"
                    f"<i>Напиши «переклади на англійську», «проаналізуй розмову» або будь-що інше</i>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"[ERROR] croco transcription={e}")
            await context.bot.send_message(chat_id=query.message.chat_id, text="😔 Не вдалося транскрибувати")

    elif query.data.startswith("cdl_rec:"):
        idx = query.data.split(":", 1)[1]
        audio_url = context.user_data.get("crec_urls", {}).get(idx)
        await query.answer("⬇️ Завантажую запис Coperato...")
        if not audio_url:
            await context.bot.send_message(chat_id=query.message.chat_id, text="😔 URL запису не знайдено, спробуй /crec знову")
            return
        try:
            print(f"[DEBUG] coperato download url={audio_url}")
            status_code, audio_bytes = await download_coperato_recording(audio_url)
            if status_code != 200 or len(audio_bytes) < 100:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"😔 Coperato повернув HTTP {status_code} ({len(audio_bytes)} байт)\n<code>{html.escape(audio_url)}</code>",
                    parse_mode="HTML",
                )
                return
            await context.bot.send_audio(
                chat_id=query.message.chat_id,
                audio=BytesIO(audio_bytes),
                filename="coperato_recording.wav",
                caption="🎙️ Запис дзвінка (Coperato)",
            )
        except Exception as e:
            print(f"[ERROR] coperato download={e} url={audio_url}")
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"😔 Помилка: <code>{html.escape(str(e))}</code>\nURL: <code>{html.escape(audio_url)}</code>",
                parse_mode="HTML",
            )
            return

    elif query.data.startswith("ctr_rec:"):
        idx = query.data.split(":", 1)[1]
        audio_url = context.user_data.get("crec_urls", {}).get(idx)
        await query.answer("📝 Транскрибую Coperato запис...")
        if not audio_url:
            await context.bot.send_message(chat_id=query.message.chat_id, text="😔 URL запису не знайдено, спробуй /crec знову")
            return
        try:
            status_code, audio_bytes = await download_coperato_recording(audio_url)
            if status_code != 200 or len(audio_bytes) < 100:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"😔 Coperato повернув HTTP {status_code} ({len(audio_bytes)} байт)",
                )
                return
            tmp_path = f"/tmp/crec_{query.from_user.id}.wav"
            with open(tmp_path, "wb") as f:
                f.write(audio_bytes)
            text, lang = await transcribe_voice(tmp_path)
            context.user_data["last_transcript"] = text
            context.user_data["last_transcript_lang"] = lang
            LANG_FLAG = {"uk": "🇺🇦", "ru": "🇷🇺", "en": "🇬🇧", "de": "🇩🇪", "pl": "🇵🇱"}
            flag = LANG_FLAG.get(lang, "🌐")
            lang_names = {"uk": "українська", "ru": "русский", "en": "english", "de": "deutsch", "pl": "polski"}
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=(
                    f"📝 <b>Транскрипція Coperato:</b> {flag} <i>{lang_names.get(lang, lang)}</i>\n\n"
                    f"{html.escape(text)}\n\n"
                    f"<i>Напиши «переклади на англійську», «проаналізуй розмову» або будь-що інше</i>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            print(f"[ERROR] coperato transcription={e}")
            await context.bot.send_message(chat_id=query.message.chat_id, text="😔 Не вдалося транскрибувати")


# ──────────────────────────────────────────────
# Recording search commands
# ──────────────────────────────────────────────

async def record_command(update: Update, context):
    """/record +380501234567 [days]"""
    if not context.args:
        await update.effective_message.reply_text("Використання: /record +380501234567 [кількість_днів]")
        return
    phone = context.args[0]
    days = int(context.args[1]) if len(context.args) > 1 else 30
    status_msg = await update.effective_message.reply_text(f"🔍 Шукаю дзвінки для {phone}...")
    try:
        recordings = await search_recordings(phone, days)
    except Exception as e:
        print(f"[ERROR] search_recordings error={e}")
        await status_msg.edit_text("😔 Щось пішло не так, спробуй ще раз")
        return
    if not recordings:
        await status_msg.edit_text(f"📭 Записів для {phone} не знайдено за {days} днів.")
        return
    text = f"🎙 <b>Знайдено {len(recordings)} записів для {phone}:</b>\n\n"
    buttons = []
    for i, rec in enumerate(recordings[:10]):
        date = (rec.get("MSG_DATE") or "")[:16]
        duration = int(rec.get("DURATION") or 0)
        dur_str = f"{duration//60}хв {duration%60}с" if duration >= 60 else f"{duration}с"
        text += f"🎙 {date} — {dur_str}\n"
        filename = rec.get("FILE_NAME") or ""
        if filename:
            buttons.append([
                InlineKeyboardButton(f"⬇️ Завантажити #{i+1}", callback_data=f"dl_rec:{filename[:60]}"),
                InlineKeyboardButton(f"📝 Транскрипція #{i+1}", callback_data=f"tr_rec:{filename[:60]}"),
            ])
    if len(recordings) > 10:
        text += f"\n<i>...та ще {len(recordings)-10} записів</i>"
    await status_msg.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)


async def vrec_command(update: Update, context):
    """/vrec +380501234567 [days] — пошук записів по номеру у Voiso"""
    if not context.args:
        await update.effective_message.reply_text("Використання: /vrec +380501234567 [кількість_днів]")
        return
    phone = context.args[0]
    days = int(context.args[1]) if len(context.args) > 1 else 30
    status_msg = await update.effective_message.reply_text(f"🔍 Шукаю дзвінки Voiso для {phone}...")
    try:
        records = await search_voiso_recordings(phone, days)
    except Exception as e:
        print(f"[ERROR] search_voiso_recordings error={e}")
        await status_msg.edit_text("😔 Щось пішло не так, спробуй ще раз")
        return
    if not records:
        await status_msg.edit_text(f"📭 Дзвінків для {phone} не знайдено у Voiso за {days} днів.")
        return
    text = f"🎙 <b>Знайдено {len(records)} дзвінків Voiso для {phone}:</b>\n\n"
    buttons = []
    for i, rec in enumerate(records[:10]):
        ts = (rec.get("timestamp") or "")[:16].replace("T", " ")
        dur = rec.get("duration") or "—"
        disp = rec.get("disposition") or "—"
        agent = rec.get("agent") or "—"
        uuid = rec.get("uuid") or ""
        disp_icon = {"answered": "✅", "no_answer": "⏱", "missed": "📵", "busy": "🔴"}.get(disp, "❓")
        text += f"{disp_icon} {ts} | {dur} | {agent}\n📞 {rec.get('from','—')} → {rec.get('to','—')}\n\n"
        if uuid and disp == "answered":
            buttons.append([
                InlineKeyboardButton(f"⬇️ Завантажити #{i+1}", callback_data=f"vdl_rec:{uuid[:60]}"),
                InlineKeyboardButton(f"📝 Транскрипція #{i+1}", callback_data=f"vtr_rec:{uuid[:60]}"),
            ])
    if len(records) > 10:
        text += f"<i>...та ще {len(records)-10} дзвінків</i>"
    await status_msg.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)


async def crec_command(update: Update, context):
    """/crec +380501234567 [days] — пошук записів Coperato по номеру"""
    if not context.args:
        await update.effective_message.reply_text("Використання: /crec +380501234567 [кількість_днів]")
        return
    phone = context.args[0]
    days = int(context.args[1]) if len(context.args) > 1 else 30
    status_msg = await update.effective_message.reply_text(f"🔍 Шукаю записи Coperato для {phone}...")
    records = search_coperato_recordings(phone, days)
    if not records:
        await status_msg.edit_text(f"📭 Записів Coperato для {phone} не знайдено за {days} днів.")
        return
    text = f"🎙 <b>Знайдено {len(records)} записів Coperato для {phone}:</b>\n\n"
    buttons = []
    context.user_data["crec_urls"] = {}
    for i, rec in enumerate(records[:10]):
        date = str(rec.get("call_date") or "")[:16]
        dur = int(rec.get("duration") or 0)
        dur_str = f"{dur//60}хв {dur%60}с" if dur >= 60 else f"{dur}с"
        rec_url = rec.get("recording_file") or ""
        text += f"📞 {date} | {dur_str}\n{rec.get('caller_id','—')} → {rec.get('called_id','—')}\n\n"
        if rec_url:
            if rec_url.startswith("/") and COPERATO_BASE_URL:
                rec_url = COPERATO_BASE_URL + rec_url
            if rec_url.startswith("http"):
                context.user_data["crec_urls"][str(i)] = rec_url
                buttons.append([
                    InlineKeyboardButton(f"⬇️ Завантажити #{i+1}", callback_data=f"cdl_rec:{i}"),
                    InlineKeyboardButton(f"📝 Транскрипція #{i+1}", callback_data=f"ctr_rec:{i}"),
                ])
    if len(records) > 10:
        text += f"<i>...та ще {len(records)-10} записів</i>"
    await status_msg.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)


# ──────────────────────────────────────────────
# Debug commands (admin only)
# ──────────────────────────────────────────────

async def debug_pbx_command(update: Update, context):
    """/debug_pbx — сирий JSON від API (тільки адмін)"""
    if update.effective_user.id not in ADMIN_IDS:
        return

    from core.integrations.didpbx import DIDPBX_URL, DIDPBX_PHONE, DIDPBX_PASSWORD
    from datetime import datetime, timedelta
    import aiohttp as _aio

    await update.effective_message.reply_text(
        f"🔧 <b>Config:</b>\n"
        f"URL: <code>{DIDPBX_URL}</code>\n"
        f"PHONE: <code>{DIDPBX_PHONE or '❌ EMPTY'}</code>\n"
        f"PASSWORD: <code>{'✅ встановлено' if DIDPBX_PASSWORD else '❌ EMPTY'}</code>",
        parse_mode="HTML",
    )

    to_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    from_time = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    phone_variants = [DIDPBX_PHONE]
    if DIDPBX_PHONE and "/" in DIDPBX_PHONE:
        phone_variants.append(DIDPBX_PHONE.split("/", 1)[1])

    for ph in phone_variants:
        label_suffix = f"(phone=<code>{html.escape(ph)}</code>)"
        try:
            async with _aio.ClientSession() as sess:
                async with sess.get(DIDPBX_URL, params={
                    "phone": ph, "pw": DIDPBX_PASSWORD,
                    "action": "vb_list", "df": "json",
                }, headers={"Accept-Encoding": "identity"}) as resp:
                    raw = await resp.read()
            preview = html.escape(raw[:600].decode("utf-8", errors="replace")) if raw else "(порожньо)"
            await update.effective_message.reply_text(
                f"🔑 <b>vb_list</b> {label_suffix} HTTP {resp.status} ({len(raw)} байт)\n<pre>{preview}</pre>",
                parse_mode="HTML",
            )
        except Exception as e:
            await update.effective_message.reply_text(f"❌ vb_list {label_suffix}: {html.escape(str(e))}", parse_mode="HTML")

        try:
            import json as _json
            async with _aio.ClientSession() as sess:
                async with sess.get(DIDPBX_URL, params={
                    "phone": ph, "pw": DIDPBX_PASSWORD,
                    "action": "cdr_list", "from_time": from_time, "to_time": to_time, "df": "json",
                }, headers={"Accept-Encoding": "identity"}) as resp:
                    raw = await resp.read()
            preview = html.escape(raw[:600].decode("utf-8", errors="replace")) if raw else "(порожньо)"
            await update.effective_message.reply_text(
                f"📋 <b>cdr_list</b> {label_suffix} HTTP {resp.status} ({len(raw)} байт)\n<pre>{preview}</pre>",
                parse_mode="HTML",
            )
            try:
                async with _aio.ClientSession() as sess:
                    async with sess.get(DIDPBX_URL, params={
                        "phone": ph, "pw": DIDPBX_PASSWORD,
                        "action": "msg_list", "from_time": from_time, "to_time": to_time, "df": "json",
                    }, headers={"Accept-Encoding": "identity"}) as resp3:
                        raw3 = await resp3.read()
                preview3 = html.escape(raw3[:800].decode("utf-8", errors="replace")) if raw3 else "(порожньо)"
                await update.effective_message.reply_text(
                    f"🎙 <b>msg_list</b> HTTP {resp3.status} ({len(raw3)} байт)\n<pre>{preview3}</pre>",
                    parse_mode="HTML",
                )
            except Exception as ex:
                await update.effective_message.reply_text(f"❌ msg_list: {html.escape(str(ex))}", parse_mode="HTML")
        except Exception as e:
            await update.effective_message.reply_text(f"❌ cdr_list {label_suffix}: {html.escape(str(e))}", parse_mode="HTML")


async def debug_voiso_command(update: Update, context):
    """/debug_voiso — сирий response від Voiso CDR API"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    key_preview = (VOISO_API_KEY or "")[:8] + "..." if VOISO_API_KEY else "❌ EMPTY"
    await update.effective_message.reply_text(
        f"🔧 <b>Voiso config:</b>\n"
        f"Cluster: <code>{VOISO_CLUSTER or '❌ EMPTY'}</code>\n"
        f"API key: <code>{key_preview}</code>\n"
        f"URL: <code>https://{VOISO_CLUSTER}.voiso.com/api/v2/cdr</code>",
        parse_mode="HTML",
    )
    status, text = await voiso_raw({"start_date": today, "end_date": today})
    preview = html.escape(text[:1200])
    await update.effective_message.reply_text(
        f"📋 HTTP {status} ({len(text)} bytes):\n<pre>{preview}</pre>",
        parse_mode="HTML",
    )


async def debug_vrec_command(update: Update, context):
    """/debug_vrec <uuid> — пробує всі URL варіанти для Voiso запису"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.effective_message.reply_text("Використання: /debug_vrec <uuid>")
        return
    uuid = context.args[0]
    await update.effective_message.reply_text(f"🔍 Тестую URL для UUID: <code>{uuid}</code>...", parse_mode="HTML")
    results = await debug_recording_urls(uuid)
    lines = []
    for r in results:
        if "error" in r:
            lines.append(f"❌ {html.escape(r['url'])}\n   error: {html.escape(r['error'])}")
        else:
            lines.append(
                f"HTTP {r['status']} | {r['size']}b | {html.escape(r['content_type'])}\n"
                f"   <code>{html.escape(r['url'])}</code>\n"
                f"   <i>{html.escape(r['preview'])}</i>"
            )
    await update.effective_message.reply_text("\n\n".join(lines), parse_mode="HTML")


async def debug_coperato_command(update: Update, context):
    """/debug_coperato — діагностика Coperato записів в БД"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        info = debug_coperato_db()
    except Exception as e:
        await update.effective_message.reply_text(f"❌ DB error: {html.escape(str(e))}", parse_mode="HTML")
        return
    lines = [
        f"<b>Coperato DB діагностика</b>",
        f"Всього дзвінків: <b>{info['total']}</b>",
        f"З записом: <b>{info['with_recording']}</b>",
    ]
    if info["recent"]:
        lines.append("\n<b>Останні 5 записів:</b>")
        for r in info["recent"]:
            rec = r["recording_file"] or "—"
            lines.append(
                f"📞 {html.escape(r['caller_id'] or '?')} → {html.escape(r['called_id'] or '?')}\n"
                f"   {r['call_date']}\n"
                f"   <code>{html.escape(rec[:80])}</code>"
            )
    else:
        lines.append("\nЗаписів не знайдено взагалі.")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def debug_crec_url_command(update: Update, context):
    """/debug_crec_url <url> — тест скачування URL напряму і через проксі"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.effective_message.reply_text("Використання: /debug_crec_url &lt;url&gt;", parse_mode="HTML")
        return

    from core.integrations.coperato import _normalize_url, _encode_proxy_url, COPERATO_PROXY
    import requests as _requests

    raw_url = context.args[0]
    norm_url = _normalize_url(raw_url)
    await update.effective_message.reply_text(
        f"🔍 Тестую URL:\n<code>{html.escape(norm_url)}</code>\nПроксі: <code>{html.escape(COPERATO_PROXY or 'немає')}</code>",
        parse_mode="HTML",
    )

    def _test(label, proxies):
        try:
            r = _requests.get(norm_url, proxies=proxies, allow_redirects=True, timeout=10)
            return f"✅ {label}: HTTP {r.status_code}, {len(r.content)} байт, {r.headers.get('content-type','?')}"
        except Exception as e:
            return f"❌ {label}: {str(e)[:200]}"

    loop = asyncio.get_event_loop()
    direct = await loop.run_in_executor(None, _test, "Напряму", {})
    lines = [direct]
    if COPERATO_PROXY:
        enc = _encode_proxy_url(COPERATO_PROXY)
        via_proxy = await loop.run_in_executor(None, _test, "Через проксі", {"http": enc, "https": enc})
        lines.append(via_proxy)
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


# ──────────────────────────────────────────────
# CrocoCalls
# ──────────────────────────────────────────────

CROCO_STATUS_LABELS = {
    "answer":      ("✅", "Відповіли"),
    "busy":        ("🔒", "Зайнято"),
    "noanswer":    ("⏱", "Не відповіли"),
    "cancel":      ("📵", "Скасовано"),
    "congestion":  ("❌", "Перевантаження"),
    "chanunavail": ("📵", "Недоступний"),
    "callflow":    ("🔄", "Кинув до з'єднання"),
    "unknown":     ("❓", "Невідомо"),
}


def _format_croco_stats(stats: dict, period_label: str) -> str:
    total = stats["total"]
    by_status = stats["by_status"]
    by_duration = stats.get("by_duration", {})
    by_type = stats.get("by_type", {})

    pct = lambda n: f"{round(n / total * 100)}%" if total > 0 else "0%"

    incoming = by_type.get("inbound", 0)
    outgoing = by_type.get("outbound", 0)

    lines = [
        f"📊 <b>CrocoCalls — {period_label}:</b>\n",
        f"📞 Всього дзвінків: <b>{total}</b>",
        f"📥 Вхідні: {incoming}  📤 Вихідні: {outgoing}\n",
    ]

    for key in ("answer", "cancel", "noanswer", "busy", "congestion", "chanunavail", "callflow"):
        count = by_status.get(key, 0)
        if count or key == "answer":
            icon, label = CROCO_STATUS_LABELS[key]
            dur = by_duration.get(key, 0)
            dur_str = f" — {_fmt_dur(dur)}" if dur > 0 else ""
            lines.append(f"{icon} {label}: {count} ({pct(count)}){dur_str}")

    known = set(CROCO_STATUS_LABELS.keys())
    for key, count in by_status.items():
        if key not in known and count:
            dur = by_duration.get(key, 0)
            dur_str = f" — {_fmt_dur(dur)}" if dur > 0 else ""
            lines.append(f"❓ {key}: {count} ({pct(count)}){dur_str}")

    return "\n".join(lines)


async def kstats_command(update: Update, context):
    """/kstats [today|yesterday|week|month] — CrocoCalls статистика"""
    arg = context.args[0] if context.args else "today"
    period = PERIOD_ALIAS.get(arg, "today")
    status_msg = await update.effective_message.reply_text("📊 Збираю статистику CrocoCalls...")
    try:
        stats = await get_croco_stats(period)
    except Exception as e:
        await status_msg.edit_text(f"😔 Помилка: <code>{html.escape(str(e))}</code>", parse_mode="HTML")
        return
    await status_msg.edit_text(
        _format_croco_stats(stats, PERIOD_LABELS.get(period, period)),
        parse_mode="HTML",
        reply_markup=KSTATS_KEYBOARD,
    )


async def krec_command(update: Update, context):
    """/krec +380501234567 [days] — пошук записів CrocoCalls по номеру"""
    if not context.args:
        await update.effective_message.reply_text("Використання: /krec +380501234567 [кількість_днів]")
        return
    phone = context.args[0]
    days = int(context.args[1]) if len(context.args) > 1 else 30
    status_msg = await update.effective_message.reply_text(f"🔍 Шукаю дзвінки CrocoCalls для {phone}...")
    try:
        records = await search_croco_recordings(phone, days)
    except Exception as e:
        await status_msg.edit_text(f"😔 Помилка: <code>{html.escape(str(e))}</code>", parse_mode="HTML")
        return
    if not records:
        await status_msg.edit_text(f"📭 Записів CrocoCalls для {phone} не знайдено за {days} днів.")
        return
    text = f"🎙 <b>Знайдено {len(records)} записів CrocoCalls для {phone}:</b>\n\n"
    buttons = []
    context.user_data["krec_urls"] = {}
    for i, rec in enumerate(records[:10]):
        date = (rec.get("starttime") or "")[:16]
        dur = int(rec.get("duration_sec") or 0)
        dur_str = f"{dur//60}хв {dur%60}с" if dur >= 60 else f"{dur}с"
        direction = rec.get("direction") or "—"
        status = rec.get("status") or "—"
        caller = (rec.get("caller") or {}).get("cid") or (rec.get("caller") or {}).get("number") or "—"
        callee = (rec.get("callee") or {}).get("number") or "—"
        dir_icon = "📥" if direction == "inbound" else "📤"
        text += f"{dir_icon} {date} | {dur_str} | {status}\n{caller} → {callee}\n\n"
        audio_url = rec.get("audio_url") or ""
        if audio_url:
            context.user_data["krec_urls"][str(i)] = audio_url
            buttons.append([
                InlineKeyboardButton(f"⬇️ Завантажити #{i+1}", callback_data=f"kdl_rec:{i}"),
                InlineKeyboardButton(f"📝 Транскрипція #{i+1}", callback_data=f"ktr_rec:{i}"),
            ])
    if len(records) > 10:
        text += f"<i>...та ще {len(records)-10} записів</i>"
    await status_msg.edit_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def debug_croco_command(update: Update, context):
    """/debug_croco — діагностика CrocoCalls API"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    key_preview = (CROCO_API_KEY or "")[:8] + "..." if CROCO_API_KEY else "❌ EMPTY"
    await update.effective_message.reply_text(
        f"🔧 <b>CrocoCalls config:</b>\n"
        f"Base URL: <code>{CROCO_BASE_URL}</code>\n"
        f"API key: <code>{key_preview}</code>",
        parse_mode="HTML",
    )
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.ipify.org") as r:
                server_ip = await r.text()
        await update.effective_message.reply_text(f"🌐 Зовнішній IP сервера: <code>{html.escape(server_ip)}</code>", parse_mode="HTML")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ IP check failed: {e}")

    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%dT00:00:00")
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    status, text = await croco_raw({"limit": 5, "filter": f"starttime>{today},starttime<{now}"})
    preview = html.escape(text[:1200])
    await update.effective_message.reply_text(
        f"📋 HTTP {status} ({len(text)} bytes):\n<pre>{preview}</pre>",
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────
# Bulk recording search
# ──────────────────────────────────────────────

async def _search_all_for_phone(phone: str, days: int = 90) -> dict:
    """Шукає записи по всіх телефоніях для одного номера."""
    loop = asyncio.get_running_loop()

    didpbx_task = asyncio.create_task(_safe(search_recordings(phone, days)))
    voiso_task  = asyncio.create_task(_safe(search_voiso_recordings(phone, days)))
    croco_task  = asyncio.create_task(_safe(search_croco_recordings(phone, days)))
    coperato    = await loop.run_in_executor(None, lambda: _safe_sync(search_coperato_recordings, phone, days))

    didpbx = await didpbx_task
    voiso  = await voiso_task
    croco  = await croco_task

    return {"didpbx": didpbx, "voiso": voiso, "croco": croco, "coperato": coperato}


async def _safe(coro):
    try:
        return await coro
    except Exception as e:
        print(f"[bulk] search error: {e}")
        return []


def _safe_sync(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        print(f"[bulk] sync search error: {e}")
        return []


async def handle_bulk_recs(query, context):
    phones = context.user_data.get("bulk_phones", [])
    if not phones:
        await query.edit_message_text("😔 Список номерів не знайдено, надішли знову")
        return

    await query.edit_message_text(f"🔍 Шукаю записи для {len(phones)} номерів у всіх телефоніях...")

    bulk_urls = {}
    no_results = []

    for pi, phone in enumerate(phones):
        results = await _search_all_for_phone(phone)
        didpbx_recs  = results["didpbx"]
        voiso_recs   = results["voiso"]
        croco_recs   = results["croco"]
        coperato_recs = results["coperato"]

        total = len(didpbx_recs) + len(voiso_recs) + len(croco_recs) + len(coperato_recs)
        if total == 0:
            no_results.append(phone)
            continue

        text = f"📞 <b>{html.escape(phone)}</b>\n"
        text += f"• DIDPBX: {len(didpbx_recs)} зап.\n" if didpbx_recs else "• DIDPBX: немає\n"
        text += f"• Voiso: {len(voiso_recs)} зап.\n"   if voiso_recs  else "• Voiso: немає\n"
        text += f"• Coperato: {len(coperato_recs)} зап.\n" if coperato_recs else "• Coperato: немає\n"
        text += f"• CrocoCalls: {len(croco_recs)} зап.\n"  if croco_recs  else "• CrocoCalls: немає\n"

        buttons = []
        bulk_urls[pi] = {}
        btn_count = 0

        # DIDPBX buttons
        bulk_urls[pi]["didpbx"] = {}
        for i, rec in enumerate(didpbx_recs[:3]):
            fname = rec.get("FILE_NAME") or ""
            if fname and btn_count < 5:
                bulk_urls[pi]["didpbx"][str(i)] = fname
                dur = int(rec.get("DURATION") or 0)
                dur_str = f"{dur//60}:{dur%60:02d}"
                buttons.append([InlineKeyboardButton(f"⬇️ DIDPBX #{i+1} ({dur_str})", callback_data=f"bdl:{pi}:didpbx:{i}")])
                btn_count += 1

        # Voiso buttons
        bulk_urls[pi]["voiso"] = {}
        for i, rec in enumerate(voiso_recs[:3]):
            uuid = rec.get("uuid") or ""
            disp = rec.get("disposition") or ""
            if uuid and disp == "answered" and btn_count < 5:
                bulk_urls[pi]["voiso"][str(i)] = uuid
                dur = rec.get("duration") or "—"
                buttons.append([InlineKeyboardButton(f"⬇️ Voiso #{i+1} ({dur})", callback_data=f"bdl:{pi}:voiso:{i}")])
                btn_count += 1

        # Coperato buttons
        bulk_urls[pi]["coperato"] = {}
        for i, rec in enumerate(coperato_recs[:3]):
            rec_url = rec.get("recording_file") or ""
            if rec_url and btn_count < 5:
                if rec_url.startswith("/") and COPERATO_BASE_URL:
                    rec_url = COPERATO_BASE_URL + rec_url
                if rec_url.startswith("http"):
                    bulk_urls[pi]["coperato"][str(i)] = rec_url
                    dur = int(rec.get("duration") or 0)
                    dur_str = f"{dur//60}:{dur%60:02d}"
                    buttons.append([InlineKeyboardButton(f"⬇️ Coperato #{i+1} ({dur_str})", callback_data=f"bdl:{pi}:coperato:{i}")])
                    btn_count += 1

        # CrocoCalls buttons
        bulk_urls[pi]["croco"] = {}
        for i, rec in enumerate(croco_recs[:3]):
            audio_url = rec.get("audio_url") or ""
            if audio_url and btn_count < 5:
                bulk_urls[pi]["croco"][str(i)] = audio_url
                dur = int(rec.get("duration_sec") or 0)
                dur_str = f"{dur//60}:{dur%60:02d}"
                buttons.append([InlineKeyboardButton(f"⬇️ CrocoCalls #{i+1} ({dur_str})", callback_data=f"bdl:{pi}:croco:{i}")])
                btn_count += 1

        await query.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        )

    context.user_data["bulk_urls"] = bulk_urls

    if no_results:
        no_list = "\n".join(f"• {p}" for p in no_results)
        await query.message.reply_text(f"📭 <b>Без записів:</b>\n{no_list}", parse_mode="HTML")


async def handle_bulk_callback(update: Update, context):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("bulk_recs:"):
        await handle_bulk_recs(query, context)
        return

    if query.data.startswith("bdl:"):
        # bdl:{phone_idx}:{source}:{rec_idx}
        parts = query.data.split(":")
        if len(parts) != 4:
            return
        _, pi, source, ri = parts
        bulk_urls = context.user_data.get("bulk_urls", {})
        url_or_id = bulk_urls.get(int(pi), {}).get(source, {}).get(ri)
        if not url_or_id:
            await query.message.reply_text("😔 Запис не знайдено, спробуй /bulk знову")
            return

        await query.answer("⬇️ Завантажую...")
        try:
            if source == "didpbx":
                audio_bytes = await download_recording(url_or_id)
                fname = "didpbx_rec.mp3"
            elif source == "voiso":
                status_code, audio_bytes = await download_voiso_recording(url_or_id)
                if status_code != 200 or len(audio_bytes) < 100:
                    await query.message.reply_text(f"😔 Voiso HTTP {status_code}")
                    return
                fname = "voiso_rec.mp3"
            elif source == "coperato":
                status_code, audio_bytes = await download_coperato_recording(url_or_id)
                if status_code != 200 or len(audio_bytes) < 100:
                    await query.message.reply_text(f"😔 Coperato HTTP {status_code}")
                    return
                fname = "coperato_rec.wav"
            elif source == "croco":
                status_code, audio_bytes = await download_croco_recording(url_or_id)
                if status_code != 200 or len(audio_bytes) < 100:
                    await query.message.reply_text(f"😔 CrocoCalls HTTP {status_code}")
                    return
                fname = "croco_rec.mp3"
            else:
                return

            await context.bot.send_audio(
                chat_id=query.message.chat_id,
                audio=BytesIO(audio_bytes),
                filename=fname,
            )
        except Exception as e:
            print(f"[ERROR] bulk download source={source} err={e}")
            await query.message.reply_text(
                f"😔 Помилка: <code>{html.escape(str(e))}</code>", parse_mode="HTML"
            )
