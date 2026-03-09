import asyncio
import html

import anthropic
import fal_client
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatAction

from core.config import ANTHROPIC_KEY, FAL_MODELS, ADMIN_IDS, check_rate_limit, is_safe_prompt
from core.database import is_blocked
from core.prompts import (
    PHOTO_ANALYSIS_PROMPT, REPLACEMENT_KEYWORDS, REPLACEMENT_ANALYSIS_PROMPT,
    PROMPT_TRANSLATE_PROMPT, PROMPT_EDIT_PROMPT,
    PROMPT_IMPROVE_BASIC, PROMPT_IMPROVE_PRO,
    TEXT_GEN_PROMPT, PHOTO_MULTI_ANALYSIS_PROMPT,
    _parse_json_response,
)

# ──────────────────────────────────────────────
# Claude helpers — аналіз та покращення промптів
# ──────────────────────────────────────────────

async def analyze_photo_request(request: str, image_count: int = 1) -> dict:
    loop = asyncio.get_running_loop()
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    req_lower = request.lower()
    if any(kw in req_lower for kw in REPLACEMENT_KEYWORDS):
        prompt_content = REPLACEMENT_ANALYSIS_PROMPT.format(request=request)
    else:
        prompt_content = PHOTO_ANALYSIS_PROMPT.format(request=request, image_count=image_count)

    response = await loop.run_in_executor(None, lambda: client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt_content}]
    ))
    return _parse_json_response(response.content[0].text)


async def translate_prompt(request: str) -> str:
    loop = asyncio.get_running_loop()
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    response = await loop.run_in_executor(None, lambda: client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": PROMPT_TRANSLATE_PROMPT.format(request=request)}]
    ))
    return response.content[0].text.strip()


async def translate_prompt_edit(original: str, changes: str) -> str:
    loop = asyncio.get_running_loop()
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    response = await loop.run_in_executor(None, lambda: client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": PROMPT_EDIT_PROMPT.format(original=original, changes=changes)}]
    ))
    return response.content[0].text.strip()


async def improve_prompt(prompt_en: str, level: str = "basic") -> dict:
    loop = asyncio.get_running_loop()
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    if level == "pro":
        content = PROMPT_IMPROVE_PRO.format(prompt=prompt_en)
        max_tok = 2048
    else:
        content = PROMPT_IMPROVE_BASIC.format(prompt=prompt_en)
        max_tok = 300

    response = await loop.run_in_executor(None, lambda: client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tok,
        messages=[{"role": "user", "content": content}]
    ))
    return _parse_json_response(response.content[0].text)


async def prepare_text_generation(request: str) -> dict:
    loop = asyncio.get_running_loop()
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    response = await loop.run_in_executor(None, lambda: client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": TEXT_GEN_PROMPT.format(request=request)}]
    ))
    return _parse_json_response(response.content[0].text)


async def analyze_photo_multi_request(request: str, count: int) -> dict:
    loop = asyncio.get_running_loop()
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    response = await loop.run_in_executor(None, lambda: client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": PHOTO_MULTI_ANALYSIS_PROMPT.format(request=request, count=count)}]
    ))
    return _parse_json_response(response.content[0].text)


# ──────────────────────────────────────────────
# fal.ai — генерація зображень
# ──────────────────────────────────────────────

async def generate_image(file_path: str | list | None, model_key: str, prompt: str) -> str:
    loop = asyncio.get_running_loop()
    model = FAL_MODELS[model_key]
    model_id = model["id"]

    def _run():
        args = {}
        if prompt:
            args["prompt"] = prompt

        if model.get("needs_image"):
            input_key = model.get("input_key", "image_url")
            paths = file_path if isinstance(file_path, list) else ([file_path] if file_path else [])
            uploaded = []
            for fp in paths:
                with open(fp, "rb") as f:
                    url = fal_client.upload(f.read(), content_type="image/jpeg")
                uploaded.append(url)

            if uploaded:
                if input_key == "image_urls" or len(uploaded) > 1:
                    args["image_urls"] = uploaded
                else:
                    args["image_url"] = uploaded[0]

        result = fal_client.run(model_id, arguments=args)

        if "images" in result and result["images"]:
            return result["images"][0]["url"]
        if "image" in result:
            img = result["image"]
            return img["url"] if isinstance(img, dict) else img
        raise ValueError(f"Unexpected fal.ai response: {result}")

    return await loop.run_in_executor(None, _run)


# ──────────────────────────────────────────────
# Клавіатури
# ──────────────────────────────────────────────

def photo_initial_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✨ Базове", callback_data="photo_improve_basic"),
            InlineKeyboardButton("🚀 Про", callback_data="photo_improve_pro"),
        ],
        [InlineKeyboardButton("✅ Згенерувати одразу", callback_data="photo_generate")],
    ])

def photo_improved_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Підтвердити", callback_data="photo_confirm_improved")],
        [
            InlineKeyboardButton("✏️ Змінити", callback_data="photo_edit_improved"),
            InlineKeyboardButton("🚀 Свій", callback_data="photo_own_improved"),
        ],
    ])

def photo_multi_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Обробити разом", callback_data="photo_multigroup")],
        [
            InlineKeyboardButton("✨ Базове", callback_data="photo_improve_basic"),
            InlineKeyboardButton("🚀 Про", callback_data="photo_improve_pro"),
        ],
    ])


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _err_text(e: Exception, user_id: int) -> str:
    text = "😔 Щось пішло не так, спробуй ще раз"
    if user_id in ADMIN_IDS:
        text += f"\n\n<code>{html.escape(str(e))}</code>"
    return text


# ──────────────────────────────────────────────
# Обробка фото — shared handlers (без credit checks)
# ──────────────────────────────────────────────

async def process_photo_group(bot, user_data: dict, chat_id: int, user_id: int):
    group = user_data.pop("photo_group", [])
    user_data.pop("photo_timer_task", None)
    is_merge = user_data.pop("photo_merge_mode", False)
    if not group:
        return

    is_multi = len(group) > 1 or is_merge
    caption = next((item["caption"] for item in group if item["caption"]), "")
    file_paths = [item["file_path"] for item in group]

    if caption and not is_safe_prompt(caption):
        await bot.send_message(chat_id=chat_id, text="🚫 Такий запит не можу обробити.")
        return

    if not caption:
        if is_multi:
            user_data["photo_group_waiting_caption"] = file_paths
            user_data["photo_merge_pending"] = is_merge
            await bot.send_message(chat_id=chat_id, text=f"📸 Отримав {len(group)} фото. Що треба з ними зробити? Опиши:")
        else:
            user_data["pending_photo_path"] = file_paths[0]
            await bot.send_message(chat_id=chat_id, text="📸 Фото отримав! Що з ним зробити?")
        return

    user_data["photo_pending"] = {
        "file_path": file_paths if is_multi else file_paths[0],
        "model": None,
        "prompt": caption,
        "raw": True,
        "is_multi": is_multi,
        "is_merge": is_merge,
    }
    user_data.pop("photo_editing", None)

    keyboard = photo_multi_keyboard() if is_multi else photo_initial_keyboard()
    await bot.send_message(
        chat_id=chat_id,
        text=f"📝 <b>Ваш запит:</b>\n<i>{html.escape(caption)}</i>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def _analyze_pending(pending: dict) -> tuple[str, str]:
    caption = pending["prompt"]
    is_multi = pending.get("is_multi", False)
    is_merge = pending.get("is_merge", False)
    file_path = pending["file_path"]
    count = len(file_path) if isinstance(file_path, list) else 1

    if is_multi or is_merge:
        analysis = await analyze_photo_multi_request(caption, count)
    else:
        analysis = await analyze_photo_request(caption)

    if is_merge:
        model_key = "gpt_edit"
    else:
        model_key = analysis.get("model", "nana" if is_multi else "xai")
    if model_key not in FAL_MODELS:
        model_key = "nana" if is_multi else "xai"
    return model_key, analysis.get("prompt_en", caption)


async def handle_photo(update: Update, context):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    caption = update.effective_message.caption or ""

    if is_blocked(user_id):
        return

    if not check_rate_limit(user_id):
        await update.effective_message.reply_text("⏳ Занадто багато запитів. Зачекай хвилину.")
        return

    if update.effective_message.photo[-1].file_size > 10 * 1024 * 1024:
        await update.effective_message.reply_text("📸 Фото занадто велике. Максимум 10MB.")
        return

    direct_action = context.user_data.pop("photo_direct_action", None)
    if direct_action:
        photo = update.effective_message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        file_path = f"/tmp/photo_{user_id}.jpg"
        await tg_file.download_to_drive(file_path)

        status_msg = await update.effective_message.reply_text("⏳ Обробляю...")

        async def keep_typing():
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        try:
            result_url = await asyncio.wait_for(
                generate_image(file_path, direct_action, ""),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            await status_msg.edit_text("⏱ Генерація зайняла занадто довго. Спробуй ще раз.")
            return
        except Exception as e:
            print(f"[ERROR] user={user_id} action={direct_action} error={e}")
            await status_msg.edit_text(_err_text(e, user_id), parse_mode="HTML")
            return
        finally:
            typing_task.cancel()

        await status_msg.delete()
        await update.effective_message.reply_photo(photo=result_url, caption="✅ Готово!")
        return

    photo = update.effective_message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    group = context.user_data.setdefault("photo_group", [])
    file_path = f"/tmp/photo_{user_id}_{len(group)}.jpg"
    await tg_file.download_to_drive(file_path)
    group.append({"file_path": file_path, "caption": caption})

    existing_task = context.user_data.pop("photo_timer_task", None)
    if existing_task:
        existing_task.cancel()

    bot = context.bot
    user_data = context.user_data

    async def timer_callback():
        await asyncio.sleep(3)
        await process_photo_group(bot, user_data, chat_id, user_id)

    context.user_data["photo_timer_task"] = asyncio.create_task(timer_callback())


async def handle_photo_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = update.effective_user.id

    if query.data == "photo_mode_edit":
        await query.edit_message_text("📷 Пришли фото з підписом — що потрібно зробити.")
        return

    if query.data == "photo_mode_create":
        context.user_data["photo_create_from_scratch"] = True
        await query.edit_message_text("✍️ Опиши що хочеш згенерувати:")
        return

    if query.data == "photo_mode_merge":
        context.user_data["photo_merge_mode"] = True
        await query.edit_message_text(
            "🔀 Надішли два фото одночасно з підписом що потрібно зробити.\n"
            "Наприклад: «Візьми обличчя з першого фото і одяг з другого»"
        )
        return

    if query.data == "photo_mode_remove_bg":
        context.user_data["photo_direct_action"] = "remove_bg"
        await query.edit_message_text("🔲 Пришли фото — видалю фон.")
        return

    if query.data == "photo_mode_upscale":
        context.user_data["photo_direct_action"] = "upscale"
        await query.edit_message_text("⬆️ Пришли фото — покращу якість.")
        return

    pending = context.user_data.get("photo_pending")

    if query.data == "photo_generate":
        if not pending:
            await query.edit_message_text("Сесія застаріла, спробуй заново.")
            return

        model_key = pending.get("model", "unknown")
        if pending.get("raw"):
            await query.edit_message_text("🔍 Аналізую запит...")
        else:
            await query.edit_message_text("🎨 Генерую зображення...")

        async def keep_typing():
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        try:
            if pending.get("raw"):
                model_key, prompt_en = await _analyze_pending(pending)
                pending["model"] = model_key
                pending["prompt"] = prompt_en
                pending.pop("raw", None)
                await query.edit_message_text("🎨 Генерую зображення...")
            result_url = await asyncio.wait_for(
                generate_image(pending["file_path"], pending["model"], pending["prompt"]),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            await query.edit_message_text("⏱ Генерація зайняла занадто довго. Спробуй ще раз.")
            return
        except Exception as e:
            print(f"[ERROR] user={user_id} model={model_key} error={e}")
            await query.edit_message_text(_err_text(e, user_id), parse_mode="HTML")
            return
        finally:
            typing_task.cancel()

        context.user_data.pop("photo_pending", None)
        await query.delete_message()
        await context.bot.send_photo(chat_id=chat_id, photo=result_url, caption="✅ Готово!")

    elif query.data in ("photo_improve_basic", "photo_improve_pro"):
        if not pending:
            await query.edit_message_text("Сесія застаріла, спробуй заново.")
            return

        model_key = pending.get("model", "unknown")
        if pending.get("raw"):
            await query.edit_message_text("🔍 Аналізую запит...")
        else:
            await query.edit_message_text("✨ Покращую промпт...")

        async def keep_typing():
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        try:
            if pending.get("raw"):
                model_key, prompt_en = await _analyze_pending(pending)
                pending["model"] = model_key
                pending["prompt"] = prompt_en
                pending.pop("raw", None)
                await query.edit_message_text("✨ Покращую промпт...")
            level = "pro" if query.data == "photo_improve_pro" else "basic"
            improved = await improve_prompt(pending["prompt"], level)
        except Exception as e:
            print(f"[ERROR] user={user_id} model={model_key} error={e}")
            await query.edit_message_text(_err_text(e, user_id), parse_mode="HTML")
            return
        finally:
            typing_task.cancel()

        context.user_data["photo_pending"]["prompt"] = improved["prompt_en"]
        prompt_uk = improved.get("prompt_uk", "")
        uk_line = f"🇺🇦 <b>Покращений промпт:</b>\n<i>{prompt_uk}</i>\n\n" if prompt_uk else ""
        await query.edit_message_text(
            f"{uk_line}🔤 <b>Prompt (EN):</b>\n<i>{improved['prompt_en']}</i>",
            parse_mode="HTML",
            reply_markup=photo_improved_keyboard()
        )

    elif query.data == "photo_confirm_improved":
        if not pending:
            await query.edit_message_text("Сесія застаріла, спробуй заново.")
            return

        model_key = pending.get("model", "unknown")
        await query.edit_message_text("⏳ Генерую зображення з покращеним промптом...")

        async def keep_typing():
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        try:
            result_url = await asyncio.wait_for(
                generate_image(pending["file_path"], pending["model"], pending["prompt"]),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            await query.edit_message_text("⏱ Генерація зайняла занадто довго. Спробуй ще раз.")
            return
        except Exception as e:
            print(f"[ERROR] user={user_id} model={model_key} error={e}")
            await query.edit_message_text(_err_text(e, user_id), parse_mode="HTML")
            return
        finally:
            typing_task.cancel()

        context.user_data.pop("photo_pending", None)
        await query.delete_message()
        await context.bot.send_photo(chat_id=chat_id, photo=result_url, caption="✅ Готово!")

    elif query.data == "photo_edit_improved":
        context.user_data["photo_editing"] = "edit"
        await query.edit_message_text("✏️ Напиши що змінити в промпті:")

    elif query.data == "photo_own_improved":
        context.user_data["photo_editing"] = "own"
        await query.edit_message_text("🚀 Напиши свій промпт з нуля:")

    elif query.data == "photo_multigroup":
        if not pending:
            await query.edit_message_text("Сесія застаріла, спробуй заново.")
            return

        model_key = pending.get("model", "unknown")
        if pending.get("raw"):
            await query.edit_message_text("🔍 Аналізую запит...")
        else:
            await query.edit_message_text("🎨 Генерую зображення...")

        async def keep_typing():
            while True:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
                await asyncio.sleep(4)

        typing_task = asyncio.create_task(keep_typing())
        try:
            if pending.get("raw"):
                model_key, prompt_en = await _analyze_pending(pending)
                pending["model"] = model_key
                pending["prompt"] = prompt_en
                pending.pop("raw", None)
                await query.edit_message_text("🎨 Генерую зображення...")
            result_url = await asyncio.wait_for(
                generate_image(pending["file_path"], pending["model"], pending["prompt"]),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            await query.edit_message_text("⏱ Генерація зайняла занадто довго. Спробуй ще раз.")
            return
        except Exception as e:
            print(f"[ERROR] user={user_id} model={model_key} error={e}")
            await query.edit_message_text(_err_text(e, user_id), parse_mode="HTML")
            return
        finally:
            typing_task.cancel()

        context.user_data.pop("photo_pending", None)
        await query.delete_message()
        await context.bot.send_photo(chat_id=chat_id, photo=result_url, caption="✅ Готово!")
