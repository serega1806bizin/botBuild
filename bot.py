import asyncio
import json
import os
import logging
import datetime
from collections import defaultdict, deque
from dataclasses import dataclass
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
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

KYIV_TZ = timezone("Europe/Kiev")
BOT_TOKEN = "7963376111:AAHslFJhLqQtO7lU7zKFqg3QORwpLd4Aos4"
ADMIN_IDS = [1275110787, 7201861104, 78792040, 5750191057, 224519300, 6455959224]
GROUPS_FILE = "group_reports.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%d-%m-%Y %H:%M"
)

temp_photo_storage = defaultdict(deque)

@dataclass
class GroupReport:
    name: str
    report_sent: bool = False
    photo_count: int = 0
    last_report_time: str = None

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


async def send_group_reports(app):
    if not group_reports:
        report_text = "Нет зарегистрированных групп."
    else:
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


async def clear_old_photos():
    while True:
        now = datetime.datetime.now()
        for chat_id, photos in list(temp_photo_storage.items()):
            temp_photo_storage[chat_id] = deque([
                (msg, timestamp) for msg, timestamp in photos
                if (now - timestamp).seconds <= 60  # Хранить фото 1 минуту
            ])
        await asyncio.sleep(30)  # Чистка каждые 30 секунд


def setup_scheduler(app):
    scheduler = AsyncIOScheduler()
    kyiv_tz = timezone("Europe/Kiev")

    scheduler.add_job(
        send_group_reports,  # Запуск еженедельного отчета
        "cron",
        day_of_week="wed",
        hour=22,

        minute=58,
        timezone=kyiv_tz,
        args=[app]
    )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    loop.create_task(run_scheduler(scheduler))

async def run_scheduler(scheduler):
    scheduler.start()

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    document = update.message.document

    logging.info(f"Получен документ в чате {chat_id}: {document.file_name} ({document.mime_type})")

    if document.mime_type.startswith("image/"):
        temp_photo_storage[chat_id].append((update.message, datetime.datetime.now()))
        await update.message.reply_text("Фото загружено как документ, учтено в отчете.")
    else:
        await update.message.reply_text("Этот файл не является изображением.")



async def welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    status_change = update.my_chat_member

    if status_change.new_chat_member.status in ["kicked", "left"]:
        # Remove the group from the JSON file if the bot was removed or left
        if chat.id in group_reports:
            del group_reports[chat.id]
            save_groups_to_file()
            logging.info(f"Bot was removed from group {chat.title} ({chat.id}). Group data deleted.")
        return

    if chat.id not in group_reports:
        group_reports[chat.id] = GroupReport(name=chat.title or f"Chat_{chat.id}")
        save_groups_to_file()


async def registr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    chat_title = update.message.chat.title or f"Chat_{chat_id}"
    if chat_id not in group_reports:
        group_reports[chat_id] = GroupReport(name=chat_title)
        save_groups_to_file()
        await update.message.reply_text(f"Группа '{chat_title}' успешно зарегистрирована!")
    else:
        await update.message.reply_text(f"Группа '{chat_title}' уже зарегистрирована.")

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    if chat_id in group_reports:
        temp_photo_storage[chat_id].append((update.message, datetime.datetime.now()))

async def report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    now = datetime.datetime.now(KYIV_TZ)
    
    if now.weekday() != 4:  # 4 - это пятница (0 - понедельник, 6 - воскресенье)
        next_friday = now + datetime.timedelta(days=(4 - now.weekday()) % 7 or 7)
        next_report_time = datetime.datetime(
            next_friday.year, next_friday.month, next_friday.day, 0, 0, tzinfo=KYIV_TZ
        )
        time_until_next = next_report_time - now
        days, seconds = time_until_next.days, time_until_next.seconds
        hours, minutes = divmod(seconds // 60, 60)
        
        await update.message.reply_text(
            f"Не время отчету. Начало отчетного дня через: {days} д. {hours} ч. {minutes} мин."
        )
        return
    
    if chat_id in group_reports:
        await asyncio.sleep(5)  # Даем время для загрузки всех фотографий
        current_time = datetime.datetime.now()
        recent_photos = [msg for msg, timestamp in temp_photo_storage[chat_id] if (current_time - timestamp).seconds <= 20]

        logging.info(f"Проверка отчета: найдено {len(recent_photos)} фото")

        if recent_photos:
            group_reports[chat_id].report_sent = True
            group_reports[chat_id].photo_count = len(recent_photos)
            group_reports[chat_id].last_report_time = current_time.strftime("%d-%m-%Y %H:%M")
            save_groups_to_file()
            await update.message.reply_text(f"Отчет принят! Всего фотографий: {len(recent_photos)}")
        else:
            await update.message.reply_text("Отчет не принят. Нет фотографий для отчета.")

def main():
    loop = asyncio.get_event_loop()
    loop.create_task(clear_old_photos())

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

    loop.run_until_complete(app.run_polling())

if __name__ == "__main__":
    main()
