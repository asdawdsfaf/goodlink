import os
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = os.environ.get("OWNER_ID")  # зададим в Railway как переменную окружения


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user

    if str(user.id) == str(OWNER_ID):
        text = "Привет, хозяин 😎 Я буду пересылать тебе сообщения от пользователей сюда."
    else:
        text = (
            "Привет! Напиши сюда своё сообщение, и я передам его автору.\n"
            "Он ответит, как только сможет 🙂"
        )

    await update.message.reply_text(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Любое текстовое сообщение от обычного пользователя"""
    user = update.effective_user
    message = update.message

    # Если пишет сам автор — пока просто ничего не делаем
    if str(user.id) == str(OWNER_ID):
        await message.reply_text(
            "Ты автор. Сейчас бот только пересылает сообщения от других людей. "
            "Потом можем добавить ответы через бота 👍"
        )
        return

    username = f"@{user.username}" if user.username else "нет username"
    text = message.text or ""

    # Сообщение, которое бот отправит тебе
    owner_text = (
        "✉️ *Новое сообщение для тебя!*\n\n"
        f"*От:* {user.full_name} ({username})\n"
        f"*User ID:* `{user.id}`\n"
        f"*Chat ID:* `{message.chat.id}`\n\n"
        f"*Текст:*\n{text}"
    )

    # Отправляем тебе (владельцу)
    await context.bot.send_message(
        chat_id=int(OWNER_ID),
        text=owner_text,
        parse_mode="Markdown",
    )

    # Отвечаем пользователю
    await message.reply_text(
        "Я передал твоё сообщение автору. Он ответит, как только сможет 🙂"
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан!")

    if not OWNER_ID:
        raise RuntimeError("OWNER_ID не задан!")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Bot started. Listening for messages...")
    application.run_polling()


if __name__ == "__main__":
    main()
