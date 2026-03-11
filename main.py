import asyncio
import os

import uvicorn
from telegram import BotCommand
from telegram.ext import (
    Application, MessageHandler, CommandHandler, CallbackQueryHandler, filters
)

from core.config import TELEGRAM_TOKEN
from core.database import init_db
from core.photo import handle_photo, handle_photo_callback
from handlers import (
    start, reset, help_command, history_command,
    photo_command, handle_message, handle_voice,
    admin_command, handle_admin_callback,
    block_user_command, unblock_user_command, admin_stats_command,
    record_command, vrec_command, crec_command, krec_command,
    stats_command, vstats_command, cstats_command, kstats_command,
    handle_stats_callback, handle_bulk_callback,
    debug_pbx_command, debug_voiso_command, debug_vrec_command, debug_coperato_command,
    debug_croco_command,
)
from webhook_server import app as webhook_app


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("photo",   "Фото: редагування, генерація, upscale, видалення фону"),
        BotCommand("history", "Статистика та що бот пам'ятає про тебе"),
        BotCommand("help",    "Що вміє бот і всі команди"),
    ])


def build_bot_app():
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",           start))
    app.add_handler(CommandHandler("reset",           reset))
    app.add_handler(CommandHandler("help",            help_command))
    app.add_handler(CommandHandler("history",         history_command))
    app.add_handler(CommandHandler("photo",           photo_command))
    app.add_handler(CommandHandler("admin",           admin_command))
    app.add_handler(CommandHandler("block",           block_user_command))
    app.add_handler(CommandHandler("unblock",         unblock_user_command))
    app.add_handler(CommandHandler("bstats",          admin_stats_command))
    app.add_handler(CommandHandler("record",          record_command))
    app.add_handler(CommandHandler("vrec",            vrec_command))
    app.add_handler(CommandHandler("crec",            crec_command))
    app.add_handler(CommandHandler("debug_pbx",       debug_pbx_command))
    app.add_handler(CommandHandler("debug_voiso",     debug_voiso_command))
    app.add_handler(CommandHandler("debug_vrec",      debug_vrec_command))
    app.add_handler(CommandHandler("debug_coperato",  debug_coperato_command))
    app.add_handler(CommandHandler("kstats",          kstats_command))
    app.add_handler(CommandHandler("krec",            krec_command))
    app.add_handler(CommandHandler("debug_croco",     debug_croco_command))
    app.add_handler(CommandHandler("stats",           stats_command))
    app.add_handler(CommandHandler("vstats",          vstats_command))
    app.add_handler(CommandHandler("cstats",          cstats_command))
    app.add_handler(CallbackQueryHandler(
        handle_stats_callback,
        pattern="^(stats:|vstats:|cstats:|kstats:|dl_rec:|tr_rec:|vdl_rec:|vtr_rec:|kdl_rec:|ktr_rec:|cdl_rec:|ctr_rec:)"
    ))
    app.add_handler(CallbackQueryHandler(
        handle_bulk_callback,
        pattern="^(bulk_recs:|bdl:)"
    ))
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(handle_photo_callback, pattern="^photo_"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


async def run():
    init_db()

    bot_app = build_bot_app()

    PORT = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(webhook_app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)

    async with bot_app:
        await bot_app.start()
        await bot_app.updater.start_polling(drop_pending_updates=True)
        print("Solvy Pro запущен!")
        await server.serve()
        await bot_app.updater.stop()
        await bot_app.stop()


if __name__ == "__main__":
    asyncio.run(run())
