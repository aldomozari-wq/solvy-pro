from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton

# Re-export Telegram handlers from core for use in main.py
from core.photo import handle_photo, handle_photo_callback  # noqa: F401


async def photo_command(update: Update, context):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Редагувати фото", callback_data="photo_mode_edit")],
        [InlineKeyboardButton("🔀 Об'єднати фото", callback_data="photo_mode_merge")],
        [InlineKeyboardButton("✨ Створити з нуля", callback_data="photo_mode_create")],
        [InlineKeyboardButton("🔲 Видалити фон", callback_data="photo_mode_remove_bg")],
        [InlineKeyboardButton("⬆️ Upscale", callback_data="photo_mode_upscale")],
    ])
    await update.effective_message.reply_text("Що хочеш зробити?", reply_markup=keyboard)
