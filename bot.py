import asyncio
import json
import os
import logging
import datetime
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ChatMemberHandler,
    filters,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import timezone

# Настройки
KYIV_TZ = timezone("Europe/Kiev")
BOT_TOKEN = "7963376111:AAHslFJhLqQtO7lU7zKFqg3QORwpLd4Aos4"
ADMIN_IDS = [1275110787, 7201861104, 78792040, 5750191057, 224519300, 6455959224]
GROUPS_FILE = "group_reports.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%d-%m-%Y %H:%M"
)

# Хранилище фотографий и отчетов
temp_photo_storage = defaultdict(deque)

@dataclass
class GroupReport:
    name: str
    report_sent: bool = False
    photo_count: int = 0
    last_report_time: str = None

# Функции работы с файлами
def load_groups_from_file():
    if not os.path.exists(GROUPS_FILE):
        with open(GROUPS_FILE, "w", encoding="utf-8") as file:
            json.dump({}, file, ensure_ascii=False, indent=4)
    with open(GROUPS_FILE, "r", encoding="utf-8") as file:
        try:
            data = json.load(file)
            return {int(k): GroupReport(**v) for k, v in data.items()}
        except json.JSONDecodeError:
            return {}

def save_groups_to_file():
    with open(GROUPS_FILE, "w", encoding="utf-8") as file:
        json.dump({str(k): v.__dict__ for k, v in group_reports.items()}, file, ensure_ascii=False, indent=4)

group_reports = load_groups_from_file()

# Создание клавиатуры для админов
def get_admin_keyboard():
    buttons = [
        ("Просмотр отчетов", "group"),
        ("Сброс всех отчетов", "reset")
    ]
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data) for text, data in buttons]])

def get_admin_keyboard():
    buttons = [
        ("Просмотр отчетов", "group"),
        ("Сброс всех отчетов", "reset")
    ]
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data) for text, data in buttons]])

async def update_admin_message(context, chat_id, text, keyboard):
    try:
        sent_message = await context.bot.send_message(chat_id, text, reply_markup=keyboard)
        context.user_data["last_message_id"] = sent_message.message_id
    except Exception as e:
        logging.warning(f"Ошибка обновления сообщения: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.message.chat.id

    if query.data in ["group", "reset"]:
        if user_id not in ADMIN_IDS:
            await update_admin_message(context, user_id, "У вас нет доступа к этой команде.", get_admin_keyboard())
            return

    if query.data == "group":
        if not group_reports:
            await update_admin_message(context, user_id, "Нет зарегистрированных групп.", get_admin_keyboard())
        else:
            report_statuses = "\n".join(
                [
                    f"{data.name}\n"
                    f"Статус: {'✅ (получено ' + str(data.photo_count) + ' фото)' if data.report_sent else '❌'}\n"
                    f"Последний отчет: {data.last_report_time or 'Нет данных'}\n-------------------------\n"
                    for data in group_reports.values()
                ]
            )
            await update_admin_message(context, user_id, report_statuses, get_admin_keyboard())

    elif query.data == "reset":
        for group in group_reports.values():
            group.report_sent = False
            group.photo_count = 0
            group.last_report_time = None
        save_groups_to_file()
        await update_admin_message(context, user_id, "Все отчеты сброшены!", get_admin_keyboard())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Нет доступа к боту.")
        return
    
    if update.message.chat.type == "private":
        await update_admin_message(
            context,
            user_id,
            "Добро пожаловать! Этот бот помогает отслеживать отчеты в группах.",
            get_admin_keyboard()
        )
    else:
        await update.message.reply_text(
            "Привет! Этот бот помогает отслеживать отчеты в вашей группе."
        )


# Функция отправки отчетов
async def send_group_reports(app):
    report_text = "📊 Еженедельный отчет по группам:\n\n"
    report_text += "\n".join(
        [
            f"Группа: {data.name}\n"
            f"Статус: {'✅ (получено ' + str(data.photo_count) + ' фото)' if data.report_sent else '❌'}\n"
            f"Последний отчет: {data.last_report_time or 'Нет данных'}\n"
            f"-------------------------\n"
            for data in group_reports.values()
        ]
    )

    for admin_id in ADMIN_IDS:
        try:
            await app.bot.send_message(admin_id, report_text)
            logging.info(f"Отчет по группам отправлен администратору {admin_id}")
        except Exception as e:
            logging.error(f"Не удалось отправить отчет администратору {admin_id}: {e}")

# Очистка старых фото
async def clear_old_photos():
    while True:
        now = datetime.datetime.now()
        for chat_id, photos in list(temp_photo_storage.items()):
            temp_photo_storage[chat_id] = deque([
                (msg, timestamp) for msg, timestamp in photos
                if (now - timestamp).seconds <= 60
            ])
        await asyncio.sleep(30)

# Настройка планировщика
def setup_scheduler(app):
    scheduler = AsyncIOScheduler()
    kyiv_tz = timezone("Europe/Kiev")

    scheduler.add_job(
        lambda: asyncio.ensure_future(send_group_reports(app)),
        "cron",
        day_of_week="mon",
        hour=12,
        minute=0,
        timezone=kyiv_tz
    )

    def start_scheduler():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        scheduler.start()
        loop.run_forever()

    thread = threading.Thread(target=start_scheduler, daemon=True)
    thread.start()

# Основная логика бота
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.COMMAND, button_handler))
    app.add_handler(ChatMemberHandler(welcome_message, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CommandHandler("registr", registr))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^фотоотч[её]т$"), report_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.Document.IMAGE, document_handler))
    app.add_handler(CallbackQueryHandler(button_handler))

    logging.info("Бот успешно запущен и ожидает события...")

    setup_scheduler(app)

    await app.run_polling()

# Запуск бота
if __name__ == "__main__":
    try:
        import nest_asyncio
        nest_asyncio.apply()
    except ImportError:
        pass

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
