import os
import time
import logging

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
OWNER_ID = os.environ.get("OWNER_ID")  # задаётся в переменных Railway

# --- простейший анти-спам ---
RATE_LIMIT_SECONDS = 10  # минимум 10 сек между сообщениями от одного юзера
last_message_time: dict[int, float] = {}

# --- связь "сообщение у автора -> кому отвечать" ---
# key: message_id в чате автора, value: {"chat_id": int, "user_id": int}
reply_map: dict[int, dict] = {}


# =======================
#        ХЕНДЛЕРЫ
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user

    if str(user.id) == str(OWNER_ID):
        text = (
            "Привет, хозяин 😎\n\n"
            "Я буду пересылать тебе сообщения от пользователей сюда.\n"
            "Чтобы ответить пользователю — просто нажми «Ответить» "
            "на его сообщении, которое я тебе переслал, и напиши текст."
        )
        await update.message.reply_text(text)
        return

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✉️ Написать автору", callback_data="contact_author")]
        ]
    )

    text = (
        "Привет! Это бот для связи с автором.\n\n"
        "Нажми кнопку ниже или просто напиши мне своё сообщение 🙂"
    )
    await update.message.reply_text(text, reply_markup=keyboard)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на кнопки"""
    query = update.callback_query
    await query.answer()

    if query.data == "contact_author":
        await query.message.reply_text(
            "Напиши сюда своё сообщение, и я передам его автору 🙂"
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех сообщений (и от автора, и от пользователей)"""
    if update.message is None:
        return

    message = update.message
    user = update.effective_user

    # =========================
    #   1. Сообщения от автора
    # =========================
    if str(user.id) == str(OWNER_ID):
        # Автор отвечает на сообщение, пересланное ботом
        if message.reply_to_message:
            original_id = message.reply_to_message.message_id
            data = reply_map.get(original_id)

            if not data:
                await message.reply_text(
                    "Не нашёл, кому ответить 🤔\n"
                    "Ответь на сообщение бота, где было обращение от пользователя."
                )
                return

            target_chat_id = data["chat_id"]
            target_user_id = data["user_id"]

            reply_text = message.text or ""
            if not reply_text:
                await message.reply_text("Сейчас я пересылаю только текстовые ответы.")
                return

            try:
                await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=f"✉️ Ответ от автора:\n\n{reply_text}",
                )
                await message.reply_text("Ответ отправлен пользователю ✅")

                # можно удалить запись, чтобы не росла бесконечно
                del reply_map[original_id]

            except Exception as e:
                logger.error("Ошибка при отправке ответа пользователю: %s", e)
                await message.reply_text("Не получилось отправить ответ пользователю 😥")
        else:
            await message.reply_text(
                "Чтобы ответить пользователю, нажми «Ответить» на его сообщении, "
                "которое я переслал."
            )
        return

    # =========================
    #   2. Сообщения от юзеров
    # =========================

    # --- анти-спам ---
    now = time.time()
    last = last_message_time.get(user.id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        await message.reply_text(
            "Ты слишком часто отправляешь сообщения. Подожди немного ✋"
        )
        return
    last_message_time[user.id] = now

    username = f"@{user.username}" if user.username else "нет username"
    basic_info = (
        "✉️ *Новое сообщение для тебя!*\n\n"
        f"*От:* {user.full_name} ({username})\n"
        f"*User ID:* `{user.id}`\n"
        f"*Chat ID:* `{message.chat.id}`\n"
    )

    # 1) отправляем автору инфо
    try:
        info_msg = await context.bot.send_message(
            chat_id=int(OWNER_ID),
            text=basic_info,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Не удалось отправить инфо автору: %s", e)
        await message.reply_text(
            "Что-то пошло не так, не смог передать сообщение автору 😥"
        )
        return

    # 2) пересылаем само сообщение (текст/фото/видео/док и т.д.)
    try:
        copy = await context.bot.copy_message(
            chat_id=int(OWNER_ID),
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )

        # запоминаем, кому отвечать, если автор ответит на это сообщение
        reply_map[copy.message_id] = {
            "chat_id": message.chat.id,
            "user_id": user.id,
        }

    except Exception as e:
        logger.error("Ошибка при копировании сообщения автору: %s", e)
        # даже если не смогли скопировать, уже есть basic_info

    # 3) отвечаем пользователю
    await message.reply_text(
        "Я передал твоё сообщение автору. Он ответит, как только сможет 🙂"
    )


# =======================
#        MAIN
# =======================

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан!")

    if not OWNER_ID:
        raise RuntimeError("OWNER_ID не задан!")

    application = Application.builder().token(BOT_TOKEN).build()

    # команды
    application.add_handler(CommandHandler("start", start))

    # кнопки
    application.add_handler(CallbackQueryHandler(button_handler))

    # все остальные сообщения
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_message)
    )

    logger.info("Bot started. Listening for messages...")
    application.run_polling()


if __name__ == "__main__":
    main()
