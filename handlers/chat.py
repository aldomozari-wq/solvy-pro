import asyncio
import json
import html

import anthropic
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from telegram.constants import ChatAction

from core.config import ANTHROPIC_KEY, ADMIN_IDS, check_rate_limit, is_safe_prompt
from core.database import (
    get_user, get_history, save_message, save_user, delete_user,
    count_messages, update_mode, get_conn, is_blocked, block_user, unblock_user,
    get_coperato_stats, search_coperato_recordings,
)
from core.memory import get_memory, update_memory_background
from core.prompts import get_system_prompt
from core.utils import transcribe_voice
from core.photo import photo_initial_keyboard, photo_multi_keyboard, prepare_text_generation, translate_prompt, translate_prompt_edit, generate_image
from core.integrations.didpbx import search_recordings, get_stats
from core.integrations.voiso import get_stats as get_voiso_stats, search_voiso_recordings

# Import formatting from telephony to avoid duplication
from handlers.telephony import (
    _format_stats, _format_voiso_stats, _format_cstats,
    STATS_KEYBOARD, VSTATS_KEYBOARD, CSTATS_KEYBOARD,
    PERIOD_LABELS, PERIOD_ALIAS,
)


# ──────────────────────────────────────────────
# Basic commands
# ──────────────────────────────────────────────

async def start(update: Update, context):
    await update.effective_message.reply_text("\u200b", reply_markup=ReplyKeyboardRemove())
    user_id = update.effective_user.id
    user = get_user(user_id)

    if not user:
        context.user_data["onboarding"] = "name"
        await update.effective_message.reply_text(
            "Привет! Я AI-ассистент для ускорения бизнеса 🤖\n\n"
            "Помогу со стратегией, контентом, продажами, автоматизацией и работой с командой.\n\n"
            "Корисні команди:\n"
            "/photo — редагувати або створити зображення\n"
            "/help — що вмію\n\n"
            "Як тебе звати?"
        )
    else:
        await update.effective_message.reply_text(
            f"С возвращением, {user[1]}! 👋 Пиши — помогу."
        )


async def reset(update: Update, context):
    user_id = update.effective_user.id
    delete_user(user_id)
    context.user_data.clear()
    context.user_data["onboarding"] = "name"
    await update.effective_message.reply_text(
        "Всё сброшено! Начнём заново 🔄\n\nКак тебя зовут?"
    )


async def help_command(update: Update, context):
    await update.effective_message.reply_text(
        "<b>Чим можу допомогти:</b>\n\n"
        "📱 Соціальні мережі — контент, стратегія, ріст аудиторії\n"
        "💰 Монетизація — як заробляти на своїй аудиторії\n"
        "📈 Бізнес — продажі, автоматизація, команда\n"
        "🎯 Особистий розвиток — цілі, навички, мотивація\n"
        "💬 Просто поговорити — я завжди поруч\n\n"
        "🖼 <b>/photo</b> — редагування та генерація зображень:\n"
        "  ✏️ Редагувати фото\n"
        "  🔀 Об'єднати фото\n"
        "  ✨ Створити з нуля\n"
        "  🔲 Видалити фон\n"
        "  ⬆️ Upscale\n\n"
        "📊 <b>/history</b> — що бот пам'ятає про тебе\n"
        "❓ <b>/help</b> — це повідомлення\n"
        "/reset — почати заново\n\n"
        "Пишу коротко і по суті. Пам'ятаю всю нашу історію 🧠",
        parse_mode='HTML',
    )


async def history_command(update: Update, _context):
    user_id = update.effective_user.id
    memory_summary, _ = get_memory(user_id)

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages WHERE user_id = %s", (user_id,))
        total = cur.fetchone()[0]
        cur.execute("SELECT created_at FROM messages WHERE user_id = %s ORDER BY created_at ASC LIMIT 1", (user_id,))
        first = cur.fetchone()
        since = first[0].strftime("%d.%m.%Y") if first else "сьогодні"
    finally:
        conn.close()

    mem_text = (
        f"🧠 <b>Що я про тебе пам'ятаю:</b>\n{memory_summary}"
        if memory_summary
        else "🧠 Ще збираю інформацію про тебе..."
    )
    await update.effective_message.reply_text(
        f"📊 <b>Твоя статистика:</b>\n\n"
        f"💬 Повідомлень: {total}\n"
        f"📅 З нами з: {since}\n\n"
        f"{mem_text}",
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────
# Main message handler
# ──────────────────────────────────────────────

async def handle_message(update: Update, context):
    user_id = update.effective_user.id
    text = update.effective_message.text
    chat_id = update.effective_chat.id

    if is_blocked(user_id):
        return

    if not check_rate_limit(user_id):
        await update.effective_message.reply_text("⏳ Занадто багато запитів. Зачекай хвилину.")
        return

    # Admin панель — ввід параметрів
    if user_id in ADMIN_IDS and context.user_data.get("admin_action"):
        action = context.user_data.pop("admin_action")
        parts = text.strip().split(None, 1)
        try:
            if action == "admin_block":
                target_id = int(parts[0])
                reason = parts[1] if len(parts) > 1 else ""
                block_user(target_id, reason)
                await update.effective_message.reply_text(f"🚫 <code>{target_id}</code> заблокований", parse_mode="HTML", reply_markup=_admin_keyboard())
            elif action == "admin_unblock":
                target_id = int(parts[0])
                unblock_user(target_id)
                await update.effective_message.reply_text(f"✅ <code>{target_id}</code> розблокований", parse_mode="HTML", reply_markup=_admin_keyboard())
        except (ValueError, IndexError):
            await update.effective_message.reply_text("❌ Невірний формат", reply_markup=_admin_keyboard())
        return

    # Онбординг
    if context.user_data.get("onboarding") == "name":
        context.user_data["name"] = text
        context.user_data["onboarding"] = "niche"
        await update.effective_message.reply_text(
            f"Приятно познакомиться, {text}! 👋\n\nЧем занимаешься? Расскажи про себя — работа, бизнес, интересы:"
        )
        return

    if context.user_data.get("onboarding") == "niche":
        context.user_data["niche"] = text
        context.user_data["onboarding"] = "goal"
        await update.effective_message.reply_text(
            "Отлично! Чего хочешь достичь? Какая главная цель прямо сейчас? 🎯"
        )
        return

    if context.user_data.get("onboarding") == "goal":
        save_user(user_id, context.user_data["name"], context.user_data["niche"], text)
        context.user_data["onboarding"] = None
        await update.effective_message.reply_text(
            "Всё, запомнил! 🧠 Теперь пиши — помогу."
        )
        return

    # Одиночне фото без підпису
    if context.user_data.get("pending_photo_path"):
        if not is_safe_prompt(text):
            await update.effective_message.reply_text("🚫 Такий запит не можу обробити.")
            return
        file_path = context.user_data.pop("pending_photo_path")
        context.user_data["photo_pending"] = {
            "file_path": file_path,
            "model": None,
            "prompt": text,
            "raw": True,
            "is_multi": False,
            "is_merge": False,
        }
        context.user_data.pop("photo_editing", None)
        await update.effective_message.reply_text(
            f"📝 <b>Ваш запит:</b>\n<i>{html.escape(text)}</i>",
            parse_mode="HTML",
            reply_markup=photo_initial_keyboard(),
        )
        return

    # Підпис для групи фото
    if context.user_data.get("photo_group_waiting_caption"):
        if not is_safe_prompt(text):
            await update.effective_message.reply_text("🚫 Такий запит не можу обробити.")
            return
        file_paths = context.user_data.pop("photo_group_waiting_caption")
        is_merge = context.user_data.pop("photo_merge_pending", False)
        context.user_data["photo_pending"] = {
            "file_path": file_paths,
            "model": None,
            "prompt": text,
            "raw": True,
            "is_multi": True,
            "is_merge": is_merge,
        }
        context.user_data.pop("photo_editing", None)
        await update.effective_message.reply_text(
            f"📝 <b>Ваш запит:</b>\n<i>{html.escape(text)}</i>",
            parse_mode="HTML",
            reply_markup=photo_multi_keyboard(),
        )
        return

    # Генерація з нуля через /photo → "Створити з нуля"
    if context.user_data.get("photo_create_from_scratch"):
        context.user_data.pop("photo_create_from_scratch")
        status_msg = await update.effective_message.reply_text("🔍 Готую промпт...")

        async def keep_typing():
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        try:
            result = await prepare_text_generation(text)
        except Exception as e:
            print(f"[ERROR] user={user_id} prepare_text_generation error={e}")
            await status_msg.edit_text("😔 Щось пішло не так, спробуй ще раз")
            return
        finally:
            typing_task.cancel()

        prompt_en = result.get("prompt_en", text)
        prompt_uk = result.get("prompt_uk", "")
        context.user_data["photo_pending"] = {
            "file_path": None,
            "model": "seedream_gen",
            "prompt": prompt_en,
        }
        context.user_data.pop("photo_editing", None)
        uk_line = f"🇺🇦 <b>Що буде намальовано:</b>\n<i>{prompt_uk}</i>\n\n" if prompt_uk else ""
        await status_msg.edit_text(
            f"{uk_line}🔤 <b>Prompt (EN):</b>\n<i>{prompt_en}</i>",
            parse_mode="HTML",
            reply_markup=photo_initial_keyboard()
        )
        return

    # Edit або Own — кастомний промпт, одразу генеруємо
    photo_editing = context.user_data.get("photo_editing")
    if photo_editing in ("edit", "own") and context.user_data.get("photo_pending"):
        context.user_data.pop("photo_editing")
        pending = context.user_data["photo_pending"]
        model_key = pending.get("model", "unknown")

        status_msg = await update.effective_message.reply_text("⏳ Перекладаю промпт...")

        async def keep_typing():
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        try:
            if photo_editing == "edit":
                prompt_en = await translate_prompt_edit(pending["prompt"], text)
            else:
                prompt_en = await translate_prompt(text)
            await status_msg.edit_text("🎨 Генерую зображення...")
            result_url = await asyncio.wait_for(
                generate_image(pending["file_path"], pending["model"], prompt_en),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            await status_msg.edit_text("⏱ Генерація зайняла занадто довго. Спробуй ще раз.")
            return
        except Exception as e:
            print(f"[ERROR] user={user_id} model={model_key} error={e}")
            err_text = "😔 Щось пішло не так, спробуй ще раз"
            if user_id in ADMIN_IDS:
                err_text += f"\n\n<code>{html.escape(str(e))}</code>"
            await status_msg.edit_text(err_text, parse_mode="HTML")
            return
        finally:
            typing_task.cancel()

        context.user_data.pop("photo_pending", None)
        await status_msg.delete()
        await update.effective_message.reply_photo(photo=result_url, caption="✅ Готово!")
        return

    # Обычный чат с Claude
    user = get_user(user_id)
    history = get_history(user_id)
    history.append({"role": "user", "content": text})
    save_message(user_id, "user", text)

    memory_summary, _ = get_memory(user_id)
    total_messages = count_messages(user_id)
    if total_messages > 0 and total_messages % 50 == 0:
        asyncio.create_task(update_memory_background(user_id, history, memory_summary))

    async def keep_typing():
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        loop = asyncio.get_running_loop()
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        response = await loop.run_in_executor(None, lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=get_system_prompt(user, memory_summary, include_photo=True, bot_name="Solvy Pro", include_pbx=True),
            messages=history
        ))
    finally:
        typing_task.cancel()

    reply = response.content[0].text

    # Детект JSON actions
    try:
        parsed = json.loads(reply.strip())

        if isinstance(parsed, dict) and parsed.get("action") == "generate_image":
            prompt_en = parsed.get("prompt_en", "")
            prompt_uk = parsed.get("prompt_uk", "")
            context.user_data["photo_pending"] = {
                "file_path": None,
                "model": "seedream_gen",
                "prompt": prompt_en,
            }
            context.user_data.pop("photo_editing", None)
            save_message(user_id, "assistant", f"[Генерую зображення: {prompt_uk}]")
            uk_line = f"🇺🇦 <b>Що буде намальовано:</b>\n<i>{prompt_uk}</i>\n\n" if prompt_uk else ""
            await update.effective_message.reply_text(
                f"{uk_line}🔤 <b>Prompt (EN):</b>\n<i>{prompt_en}</i>",
                parse_mode="HTML",
                reply_markup=photo_initial_keyboard()
            )
            return

        # Voiso actions
        if isinstance(parsed, dict) and parsed.get("action", "").startswith("voiso_"):
            action = parsed["action"]

            if action == "voiso_stats":
                raw_period = parsed.get("period", "today")
                period = PERIOD_ALIAS.get(raw_period.lower(), "today")
                status_msg = await update.effective_message.reply_text("📊 Збираю статистику Voiso...")
                try:
                    stats = await get_voiso_stats(period)
                except Exception as e:
                    print(f"[ERROR] voiso_stats {e}")
                    await status_msg.edit_text("😔 Помилка при отриманні статистики Voiso")
                    return
                await status_msg.edit_text(_format_voiso_stats(stats, PERIOD_LABELS.get(period, period)), parse_mode="HTML", reply_markup=VSTATS_KEYBOARD)
                return

            if action == "voiso_records":
                phone = parsed.get("phone", "")
                days = int(parsed.get("days", 30))
                status_msg = await update.effective_message.reply_text(f"🔍 Шукаю дзвінки Voiso для {phone}...")
                try:
                    records = await search_voiso_recordings(phone, days)
                except Exception as e:
                    print(f"[ERROR] voiso_records {e}")
                    await status_msg.edit_text("😔 Помилка при пошуку")
                    return
                if not records:
                    await status_msg.edit_text(f"📭 Дзвінків для {phone} не знайдено у Voiso за {days} днів.")
                    return
                msg_text = f"🎙 <b>Знайдено {len(records)} дзвінків Voiso для {phone}:</b>\n\n"
                buttons = []
                for i, rec in enumerate(records[:10]):
                    ts = (rec.get("timestamp") or "")[:16].replace("T", " ")
                    dur = rec.get("duration") or "—"
                    disp = rec.get("disposition") or "—"
                    agent = rec.get("agent") or "—"
                    uuid = rec.get("uuid") or ""
                    disp_icon = {"answered": "✅", "no_answer": "⏱", "missed": "📵", "busy": "🔴"}.get(disp, "❓")
                    msg_text += f"{disp_icon} {ts} | {dur} | {agent}\n📞 {rec.get('from','—')} → {rec.get('to','—')}\n\n"
                    if uuid and disp == "answered":
                        buttons.append([
                            InlineKeyboardButton(f"⬇️ Завантажити #{i+1}", callback_data=f"vdl_rec:{uuid[:60]}"),
                            InlineKeyboardButton(f"📝 Транскрипція #{i+1}", callback_data=f"vtr_rec:{uuid[:60]}"),
                        ])
                if len(records) > 10:
                    msg_text += f"<i>...та ще {len(records)-10} дзвінків</i>"
                await status_msg.edit_text(msg_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
                return

        # Coperato actions
        if isinstance(parsed, dict) and parsed.get("action") == "coperato_records":
            phone = parsed.get("phone", "")
            days = int(parsed.get("days", 30))
            status_msg = await update.effective_message.reply_text(f"🔍 Шукаю записи Coperato для {phone}...")
            records = search_coperato_recordings(phone, days)
            if not records:
                await status_msg.edit_text(f"📭 Записів Coperato для {phone} не знайдено за {days} днів.")
                return
            msg_text = f"🎙 <b>Знайдено {len(records)} записів Coperato для {phone}:</b>\n\n"
            buttons = []
            for i, rec in enumerate(records[:10]):
                date = str(rec.get("call_date") or "")[:16]
                dur = int(rec.get("duration") or 0)
                dur_str = f"{dur//60}хв {dur%60}с" if dur >= 60 else f"{dur}с"
                rec_url = rec.get("recording_file") or ""
                msg_text += f"📞 {date} | {dur_str}\n{rec.get('caller_id','—')} → {rec.get('called_id','—')}\n\n"
                if rec_url:
                    buttons.append([InlineKeyboardButton(f"▶️ Запис #{i+1}", url=rec_url)])
            if len(records) > 10:
                msg_text += f"<i>...та ще {len(records)-10} записів</i>"
            await status_msg.edit_text(msg_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
            return

        # PBX (DIDPBX) actions
        if isinstance(parsed, dict) and parsed.get("action", "").startswith("pbx_"):
            action = parsed["action"]

            if action == "pbx_records":
                phone = parsed.get("phone", "")
                days = int(parsed.get("days", 30))
                status_msg = await update.effective_message.reply_text(f"🔍 Шукаю записи для {phone}...")
                try:
                    recordings = await search_recordings(phone, days)
                except Exception as e:
                    print(f"[ERROR] pbx_records {e}")
                    await status_msg.edit_text("😔 Помилка при пошуку записів")
                    return
                if not recordings:
                    await status_msg.edit_text(f"📭 Записів для {phone} не знайдено за {days} днів.")
                    return
                msg_text = f"🎙 <b>Знайдено {len(recordings)} записів для {phone}:</b>\n\n"
                buttons = []
                for i, rec in enumerate(recordings[:10]):
                    date = (rec.get("MSG_DATE") or "")[:16]
                    dur = int(rec.get("DURATION") or 0)
                    dur_str = f"{dur//60}хв {dur%60}с" if dur >= 60 else f"{dur}с"
                    msg_text += f"🎙 {date} — {dur_str}\n"
                    fn = rec.get("FILE_NAME") or ""
                    if fn:
                        buttons.append([
                            InlineKeyboardButton(f"⬇️ #{i+1}", callback_data=f"dl_rec:{fn[:60]}"),
                            InlineKeyboardButton(f"📝 #{i+1}", callback_data=f"tr_rec:{fn[:60]}"),
                        ])
                await status_msg.edit_text(msg_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
                return

            if action == "pbx_stats":
                raw_period = parsed.get("period", "today")
                period = PERIOD_ALIAS.get(raw_period.lower(), "today")
                status_msg = await update.effective_message.reply_text("📊 Збираю статистику...")
                try:
                    stats = await get_stats(period)
                except Exception as e:
                    print(f"[ERROR] pbx_stats {e}")
                    await status_msg.edit_text("😔 Помилка при отриманні статистики")
                    return
                await status_msg.edit_text(_format_stats(stats, PERIOD_LABELS.get(period, period)), parse_mode="HTML", reply_markup=STATS_KEYBOARD)
                return

            if action == "pbx_translate":
                language = parsed.get("language", "english")
                transcript = context.user_data.get("last_transcript")
                if not transcript:
                    await update.effective_message.reply_text("😔 Немає транскрипту для перекладу. Спочатку зроби транскрипцію запису.")
                    return
                status_msg = await update.effective_message.reply_text(f"🔄 Перекладаю на {language}...")
                tr_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
                tr_response = await asyncio.get_running_loop().run_in_executor(None, lambda: tr_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2000,
                    messages=[{"role": "user", "content": f"Translate this to {language}. Return only the translation, no comments:\n\n{transcript}"}]
                ))
                await status_msg.edit_text(f"🌐 <b>Переклад ({language}):</b>\n\n{html.escape(tr_response.content[0].text)}", parse_mode="HTML")
                return

            if action == "pbx_analyze":
                transcript = context.user_data.get("last_transcript")
                if not transcript:
                    await update.effective_message.reply_text("😔 Немає транскрипту для аналізу. Спочатку зроби транскрипцію запису.")
                    return
                status_msg = await update.effective_message.reply_text("🧠 Аналізую розмову...")
                an_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
                sys_prompt = get_system_prompt(user, memory_summary, include_photo=False, bot_name="Solvy Pro")
                an_response = await asyncio.get_running_loop().run_in_executor(None, lambda: an_client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=2000,
                    system=sys_prompt,
                    messages=[{"role": "user", "content": f"Проаналізуй цю розмову. Визнач: тон, настрій клієнта, ключові теми, якість обслуговування, рекомендації:\n\n{transcript}"}]
                ))
                analysis = an_response.content[0].text
                save_message(user_id, "assistant", analysis)
                try:
                    await status_msg.edit_text(analysis, parse_mode="HTML")
                except Exception:
                    await status_msg.edit_text(analysis)
                return

    except (json.JSONDecodeError, TypeError, AttributeError, KeyError):
        pass

    save_message(user_id, "assistant", reply)

    try:
        await update.effective_message.reply_text(reply, parse_mode='HTML')
    except Exception:
        await update.effective_message.reply_text(reply)


async def handle_voice(update: Update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if is_blocked(user_id):
        return
    if not check_rate_limit(user_id):
        await update.effective_message.reply_text("⏳ Занадто багато запитів. Зачекай хвилину.")
        return

    status_msg = await update.effective_message.reply_text("🎙️ Розпізнаю голос...")

    voice = update.effective_message.voice
    tg_file = await context.bot.get_file(voice.file_id)
    file_path = f"/tmp/voice_{user_id}.ogg"
    await tg_file.download_to_drive(file_path)

    try:
        text, lang = await transcribe_voice(file_path)
    except Exception as e:
        print(f"[ERROR] transcribe user={user_id} error={e}")
        await status_msg.edit_text("😔 Не вдалося розпізнати голос, спробуй ще раз.")
        return

    LANG_FLAG = {"uk": "🇺🇦", "ru": "🇷🇺", "en": "🇬🇧", "de": "🇩🇪", "pl": "🇵🇱"}
    flag = LANG_FLAG.get(lang, "🌐")
    await status_msg.edit_text(f"🎙️ {flag} <i>{html.escape(text)}</i>", parse_mode="HTML")

    user = get_user(user_id)
    history = get_history(user_id)
    history.append({"role": "user", "content": text})
    save_message(user_id, "user", text)

    async def keep_typing():
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        memory_summary, _ = get_memory(user_id)
        loop = asyncio.get_running_loop()
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        response = await loop.run_in_executor(None, lambda: client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=get_system_prompt(user, memory_summary, include_photo=True, bot_name="Solvy Pro"),
            messages=history[-50:]
        ))
    finally:
        typing_task.cancel()

    reply = response.content[0].text
    save_message(user_id, "assistant", reply)

    try:
        await update.effective_message.reply_text(reply, parse_mode="HTML")
    except Exception:
        await update.effective_message.reply_text(reply)


# ──────────────────────────────────────────────
# Admin
# ──────────────────────────────────────────────

def _admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [
            InlineKeyboardButton("🚫 Блок", callback_data="admin_block"),
            InlineKeyboardButton("✅ Розблок", callback_data="admin_unblock"),
        ],
    ])


async def admin_command(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.effective_message.reply_text("🛠 Адмін панель:", reply_markup=_admin_keyboard())


async def handle_admin_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id not in ADMIN_IDS:
        return

    if query.data == "admin_stats":
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(DISTINCT user_id) FROM users")
            total_users = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM messages")
            total_messages = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM blocked_users")
            total_blocked = cur.fetchone()[0]
            cur.execute(
                "SELECT user_id, COUNT(*) as cnt FROM messages "
                "GROUP BY user_id ORDER BY cnt DESC LIMIT 5"
            )
            top_users = cur.fetchall()
        finally:
            conn.close()

        top_text = "\n".join(f"  {uid}: {cnt} повід." for uid, cnt in top_users)
        await query.edit_message_text(
            f"📊 <b>Статистика бота:</b>\n\n"
            f"👤 Юзерів: {total_users}\n"
            f"💬 Повідомлень: {total_messages}\n"
            f"🚫 Заблокованих: {total_blocked}\n\n"
            f"<b>Топ-5 активних:</b>\n{top_text}",
            parse_mode="HTML",
            reply_markup=_admin_keyboard(),
        )
    elif query.data == "admin_block":
        context.user_data["admin_action"] = "admin_block"
        await query.edit_message_text("✏️ Введи: <code>user_id [причина]</code>", parse_mode="HTML")
    elif query.data == "admin_unblock":
        context.user_data["admin_action"] = "admin_unblock"
        await query.edit_message_text("✏️ Введи: <code>user_id</code>", parse_mode="HTML")


async def block_user_command(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.effective_message.reply_text("Використання: /block <user_id> [причина]")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Невірний user_id")
        return
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    block_user(target_id, reason)
    await update.effective_message.reply_text(f"🚫 Юзер {target_id} заблокований.")


async def unblock_user_command(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    if not context.args:
        await update.effective_message.reply_text("Використання: /unblock <user_id>")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("❌ Невірний user_id")
        return
    unblock_user(target_id)
    await update.effective_message.reply_text(f"✅ Юзер {target_id} розблокований.")


async def admin_stats_command(update: Update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT user_id) FROM users")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM messages")
        total_messages = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM blocked_users")
        total_blocked = cur.fetchone()[0]
        cur.execute(
            "SELECT user_id, COUNT(*) as cnt FROM messages "
            "GROUP BY user_id ORDER BY cnt DESC LIMIT 5"
        )
        top_users = cur.fetchall()
    finally:
        conn.close()

    top_text = "\n".join(f"  {uid}: {cnt} повід." for uid, cnt in top_users)
    await update.effective_message.reply_text(
        f"📊 <b>Статистика бота:</b>\n\n"
        f"👤 Юзерів: {total_users}\n"
        f"💬 Повідомлень: {total_messages}\n"
        f"🚫 Заблокованих: {total_blocked}\n\n"
        f"<b>Топ-5 активних:</b>\n{top_text}",
        parse_mode="HTML",
    )
