import os
import time
import logging
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
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

# --- анти-спам ---
RATE_LIMIT_SECONDS = 10  # минимум 10 сек между сообщениями от одного юзера
last_message_time = {}

# --- связка "сообщение у автора -> кому отвечать" ---
reply_map = {}

# --- баны ---
# BAN_LIST[user_id] = {"until": timestamp или None (перманентный бан)}
BAN_LIST = {}

# --- история сообщений ---
# HISTORY = [{"user_id": ..., "username": ..., "full_name": ..., "text": ..., "ts": ...}, ...]
HISTORY = []
MAX_HISTORY = 1000  # храним не больше 1000 записей

# --- состояния админа для диалогов (бан/разбан) ---
ADMIN_STATE = {}  # ADMIN_STATE[OWNER_ID] = {"mode": "ban_wait" / "unban_wait"}


# =======================
#     ВСПОМОГАТЕЛЬНОЕ
# =======================

def is_owner(user_id: int) -> bool:
    return str(user_id) == str(OWNER_ID)


def is_banned(user_id: int) -> bool:
    data = BAN_LIST.get(user_id)
    if not data:
        return False
    until = data.get("until")
    if until is None:
        return True
    return time.time() < until


def add_history_entry(user, text: str):
    entry = {
        "user_id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "text": text,
        "ts": time.time(),
    }
    HISTORY.append(entry)
    if len(HISTORY) > MAX_HISTORY:
        HISTORY.pop(0)


def format_history(days: int | None) -> str:
    if not HISTORY:
        return "История пуста."

    now = time.time()
    if days is not None:
        limit_ts = now - days * 86400
        rows = [h for h in HISTORY if h["ts"] >= limit_ts]
        period = f"за последние {days} дн."
    else:
        rows = HISTORY[:]
        period = "за всё время (по последним записям)"

    if not rows:
        return f"Нет сообщений {period}"

    # берём максимум 50, чтобы не лопнуло сообщение
    rows = rows[-50:]

    lines = [f"История {period} (последние {len(rows)} сообщений):"]
    for h in rows:
        dt = datetime.fromtimestamp(h["ts"]).strftime("%Y-%m-%d %H:%M")
        uname = f"@{h['username']}" if h["username"] else "нет username"
        text = h["text"].replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "..."
        lines.append(
            f"{dt} | {h['full_name']} ({uname}, id {h['user_id']}): {text}"
        )

    return "\n".join(lines)


def owner_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🚫 Бан", "♻️ Разбан"],
            ["🧾 Блеклист", "📜 История"],
        ],
        resize_keyboard=True,
    )


# =======================
#        ХЕНДЛЕРЫ
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user

    if is_owner(user.id):
        text = (
            "Привет, хозяин 😎\n\n"
            "Я пересылаю тебе сообщения от пользователей сюда.\n\n"
            "➡ Чтобы ответить пользователю:\n"
            " • нажми *Ответить* (Reply) на его сообщении, которое я переслал\n"
            " • напиши текст — я отправлю его пользователю.\n\n"
            "Внизу у тебя админ-панель: Бан, Разбан, Блеклист, История."
        )
        await update.message.reply_text(text, reply_markup=owner_keyboard())
        return

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✉️ Написать автору", callback_data="contact_author")]
        ]
    )

    text = (
        "Привет! Это бот для связи с автором.\n\n"
        "Пожалуйста, пиши сразу *одно нормальное сообщение*, а не по одному слову.\n\n"
        "❌ Плохо:\n"
        "  • «привет»\n"
        "  • (следом) «хотел узнать...»\n\n"
        "✅ Хорошо:\n"
        "  • «Привет, хотел узнать про ... (и дальше твой вопрос)»\n\n"
        "Нажми кнопку ниже или просто напиши своё сообщение 🙂"
    )
    await update.message.reply_text(text, reply_markup=keyboard)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на inline-кнопки"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "contact_author":
        await query.message.reply_text(
            "Напиши сюда своё сообщение, и я передам его автору 🙂"
        )
        return

    # История по кнопкам
    if is_owner(query.from_user.id) and data.startswith("history_"):
        if data == "history_all":
            days = None
        else:
            try:
                days = int(data.split("_")[1])
            except Exception:
                days = 7
        text = format_history(days)
        await query.message.reply_text(text)
        return


async def owner_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /history N — история за N дней (только для владельца)"""
    user = update.effective_user
    if not is_owner(user.id):
        return

    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            await update.message.reply_text("Использование: /history 7  (число дней)")
            return
    else:
        days = 7

    text = format_history(days)
    await update.message.reply_text(text)


async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Обработка сообщений админа (бан/разбан/блеклист/история).
    Возвращает True, если сообщение обработано тут.
    """
    message = update.message
    user = update.effective_user
    if not is_owner(user.id):
        return False

    text = (message.text or "").strip()

    # Проверка состояния (ожидание ввода после кнопки)
    state = ADMIN_STATE.get(user.id)

    if state and state.get("mode") == "ban_wait":
        parts = text.split()
        if not parts:
            await message.reply_text(
                "Отправь: user_id время_в_часах\nНапример: 5195905140 24"
            )
            return True

        try:
            target_id = int(parts[0])
            hours = int(parts[1]) if len(parts) > 1 else 24
        except Exception:
            await message.reply_text(
                "Не понял. Пример: 5195905140 24  (id и часы бана)"
            )
            return True

        until = time.time() + hours * 3600 if hours > 0 else None
        BAN_LIST[target_id] = {"until": until}

        if until:
            dt = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
            await message.reply_text(
                f"Пользователь {target_id} забанен на {hours} ч (до {dt})."
            )
        else:
            await message.reply_text(f"Пользователь {target_id} забанен навсегда.")

        ADMIN_STATE.pop(user.id, None)
        return True

    if state and state.get("mode") == "unban_wait":
        try:
            target_id = int(text)
        except Exception:
            await message.reply_text("Отправь просто числовой user_id для разбана.")
            return True

        if target_id in BAN_LIST:
            BAN_LIST.pop(target_id)
            await message.reply_text(f"Пользователь {target_id} разбанен.")
        else:
            await message.reply_text("Этого пользователя нет в бан-листе.")

        ADMIN_STATE.pop(user.id, None)
        return True

    # Если нет активного состояния — обрабатываем нажатия на кнопки панельки
    if text == "🚫 Бан":
        ADMIN_STATE[user.id] = {"mode": "ban_wait"}
        await message.reply_text(
            "Отправь user_id и время бана в часах.\n"
            "Например: 5195905140 24\n"
            "Если время = 0 — бан навсегда."
        )
        return True

    if text == "♻️ Разбан":
        ADMIN_STATE[user.id] = {"mode": "unban_wait"}
        await message.reply_text("Отправь user_id, которого нужно разбанить.")
        return True

    if text == "🧾 Блеклист":
        if not BAN_LIST:
            await message.reply_text("Блеклист пуст.")
            return True

        lines = ["Сейчас в бане:"]
        now = time.time()
        for uid, data in BAN_LIST.items():
            until = data.get("until")
            if until is None:
                lines.append(f" • {uid} — навсегда")
            elif until > now:
                dt = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M")
                lines.append(f" • {uid} — до {dt}")
        if len(lines) == 1:
            lines.append(" (активных банов нет)")
        await message.reply_text("\n".join(lines))
        return True

    if text == "📜 История":
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("1 день", callback_data="history_1"),
                    InlineKeyboardButton("7 дней", callback_data="history_7"),
                    InlineKeyboardButton("30 дней", callback_data="history_30"),
                ],
                [
                    InlineKeyboardButton("Все", callback_data="history_all"),
                ],
            ]
        )
        await message.reply_text(
            "Выбери период, за который показать историю:", reply_markup=keyboard
        )
        return True

    return False


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка всех сообщений (и от автора, и от пользователей)"""
    if update.message is None:
        return

    message = update.message
    user = update.effective_user

    # =========================
    #   1. Сообщения от автора
    # =========================
    if is_owner(user.id):
        # сначала пробуем отдать на обработку админ-панели
        if await handle_admin_text(update, context):
            return

        # Ответ пользователю через Reply
        if message.reply_to_message:
            original_id = message.reply_to_message.message_id
            data = reply_map.get(original_id)

            if not data:
                await message.reply_text(
                    "Не нашёл, кому ответить 🤔\n"
                    "Ответь именно на сообщение, которое я переслал от пользователя."
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
                await message.reply_text(
                    f"Ответ отправлен пользователю (id {target_user_id}) ✅"
                )

                # очищаем карту, чтобы не раздувалась
                del reply_map[original_id]

            except Exception as e:
                logger.error("Ошибка при отправке ответа пользователю: %s", e)
                await message.reply_text(
                    "Не получилось отправить ответ пользователю 😥"
                )
        else:
            await message.reply_text(
                "Чтобы ответить пользователю, нажми «Ответить» (Reply) "
                "на его сообщении, которое я переслал.",
                reply_markup=owner_keyboard(),
            )
        return

    # =========================
    #   2. Сообщения от юзеров
    # =========================

    if is_banned(user.id):
        await message.reply_text(
            "Ты временно не можешь писать этому боту. Попробуй позже."
        )
        return

    # --- анти-спам ---
    now = time.time()
    last = last_message_time.get(user.id, 0)
    if now - last < RATE_LIMIT_SECONDS:
        await message.reply_text(
            "Ты слишком часто отправляешь сообщения. Подожди немного ✋"
        )
        return
    last_message_time[user.id] = now

    # добавляем в историю
    text_content = message.text or "<не текст (фото/видео/док)>"
    add_history_entry(user, text_content)

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
            reply_markup=owner_keyboard(),
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
    application.add_handler(CommandHandler("history", owner_history_command))

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
