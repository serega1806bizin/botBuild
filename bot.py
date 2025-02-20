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

RU_MONTHS = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь"
}

# Конфигурация
KYIV_TZ = timezone("Europe/Kiev")
BOT_TOKEN = "7963376111:AAEW-j7v4upxE6YpLntKhzbzCR6-1MeVU3Y"
ADMIN_IDS = [1275110787, 7201861104, 78792040, 5750191057, 224519300, 6455959224]

ARCHIVE_FILE = "archive_reports.json"
REGISTERED_GROUPS_FILE = "registered_groups.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%d.%m.%Y %H:%M"
)

# Временное хранилище фотографий: для каждого чата сохраняются кортежи (сообщение, время)
temp_photo_storage = defaultdict(deque)

# Структура архивного отчёта
@dataclass
class ArchiveReport:
    group_id: int
    group_name: str
    report_date: str  # формат "DD-MM-YYYY"
    report_time: str  # формат "HH:MM"
    photo_count: int


def auto_create_empty_report():
    now = datetime.datetime.now(KYIV_TZ)
    # Проверяем, что сегодня пятница
    if now.weekday() != 4:
        return
    # Для каждой зарегистрированной группы проверяем, есть ли отчёт на сегодня
    for group_id_str, group_name in registered_groups.items():
        group_id = int(group_id_str)
        report_exists = False
        if group_id in archive_reports:
            for report in archive_reports[group_id]:
                if report.report_date == now.strftime("%d-%m-%Y"):
                    report_exists = True
                    break
        if not report_exists:
            new_report = ArchiveReport(
                group_id=group_id,
                group_name=group_name,
                report_date=now.strftime("%d-%m-%Y"),
                report_time="Не отправлен",
                photo_count=0
            )
            if group_id in archive_reports:
                archive_reports[group_id].append(new_report)
            else:
                archive_reports[group_id] = [new_report]
    save_archive_reports(archive_reports)
    logging.info("Пустые отчеты для пятницы созданы (если отсутствовали).")


def load_archive_reports():
    if not os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "w", encoding="utf-8") as file:
            json.dump({}, file, ensure_ascii=False, indent=4)
    with open(ARCHIVE_FILE, "r", encoding="utf-8") as file:
        try:
            data = json.load(file)
            def create_report(report):
                return ArchiveReport(
                    group_id=report.get("group_id"),
                    group_name=report.get("group_name"),
                    report_date=report.get("report_date"),
                    report_time=report.get("report_time", "Нет данных"),
                    photo_count=report.get("photo_count")
                )
            return {int(k): [create_report(report) for report in v] for k, v in data.items()}
        except json.JSONDecodeError:
            return {}


def save_archive_reports(archive_data):
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as file:
        json.dump({str(k): [report.__dict__ for report in v] for k, v in archive_data.items()},
                  file, ensure_ascii=False, indent=4)

async def send_latest_report_to_admins(app):
    latest_date = None
    # Ищем максимальную дату среди всех архивных отчётов
    for reports in archive_reports.values():
        for report in reports:
            try:
                r_date = datetime.datetime.strptime(report.report_date, "%d-%m-%Y")
                if latest_date is None or r_date > latest_date:
                    latest_date = r_date
            except Exception as e:
                logging.error(f"Ошибка парсинга даты: {e}")
    if latest_date is None:
        message_text = "Нет архивных отчетов для отправки."
    else:
        latest_date_str = latest_date.strftime("%d-%m-%Y")
        message_lines = [f"Отчеты за {latest_date_str}:\n"]
        # Формируем отчет по каждой зарегистрированной группе
        for group_id_str, group_name in registered_groups.items():
            group_id = int(group_id_str)
            report_found = None
            if group_id in archive_reports:
                for report in archive_reports[group_id]:
                    if report.report_date == latest_date_str:
                        report_found = report
                        break
            if report_found:
                if report_found.photo_count > 0:
                  status_line = f"✅ (получено {report_found.photo_count} фото)"
                  last_report_line = report_found.report_time
                else:
                  status_line = "❌"
                  last_report_line = "Нет данных"
            else:
                status_line = "❌"
                last_report_line = "Нет данных"
            message_lines.append(f"Группа: {group_name}")
            message_lines.append(f"Статус: {status_line}")
            message_lines.append(f"Последний отчет: {last_report_line}")
            message_lines.append("-------------------------\n")
        message_text = "\n".join(message_lines)
    # Отправляем сформированный отчет всем администраторам
    for admin_id in ADMIN_IDS:
        try:
            asyncio.create_task(app.bot.send_message(admin_id, message_text))
        except Exception as e:
            logging.error(f"Не удалось отправить отчет администратору {admin_id}: {e}")

# Глобальная переменная с архивными отчётами
archive_reports = load_archive_reports()

# Функции для регистрации групп
def load_registered_groups():
    if not os.path.exists(REGISTERED_GROUPS_FILE):
        with open(REGISTERED_GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=4)
    with open(REGISTERED_GROUPS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_registered_groups(data):
    with open(REGISTERED_GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# Глобальный словарь зарегистрированных групп: { "<chat_id>": "Chat Title" }
registered_groups = load_registered_groups()

# Клавиатура для администратора (начальное меню)
def get_admin_keyboard():
    buttons = [
        ("Просмотр отчетов", "group"),
    ]
    return InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=data) for text, data in buttons]])

# Функция обновления сообщения для администратора
async def update_admin_message(context, chat_id, text, keyboard):
    try:
        sent_message = await context.bot.send_message(chat_id, text, reply_markup=keyboard)
        context.user_data["last_message_id"] = sent_message.message_id
    except Exception as e:
        logging.warning(f"Ошибка обновления сообщения: {e}")

# Обработчик callback-запросов для админского интерфейса
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.message.chat.id

    if user_id not in ADMIN_IDS:
        await update_admin_message(context, user_id, "У вас нет доступа к этой команде.", get_admin_keyboard())
        return

    data = query.data
    if data == "group":
        if not archive_reports:
            await update_admin_message(context, user_id, "Нет архивных отчетов.", get_admin_keyboard())
            return
        # Формирование клавиатуры с выбором месяца: текущий и предыдущий
        now = datetime.datetime.now(KYIV_TZ)
        current_month_name = RU_MONTHS[now.month]
        first_day_current = now.replace(day=1)
        previous_month_date = first_day_current - datetime.timedelta(days=1)
        previous_month_name = RU_MONTHS[previous_month_date.month]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"За {current_month_name}", callback_data="month_current"),
            InlineKeyboardButton(f"За {previous_month_name}", callback_data="month_previous")
        ]])
        await update_admin_message(context, user_id, "Выберите за какой месяц хотите посмотреть отчеты:", keyboard)
    elif data in ["month_current", "month_previous"]:
        now = datetime.datetime.now(KYIV_TZ)
        if data == "month_current":
            target_year = now.year
            target_month = now.month
        else:
            first_day_current = now.replace(day=1)
            previous_month_date = first_day_current - datetime.timedelta(days=1)
            target_year = previous_month_date.year
            target_month = previous_month_date.month

        # Собираем уникальные даты отчётов за выбранный месяц
        dates_set = set()
        for reports in archive_reports.values():
            for report in reports:
                try:
                    report_date = datetime.datetime.strptime(report.report_date, "%d-%m-%Y")
                    if report_date.year == target_year and report_date.month == target_month:
                        dates_set.add(report.report_date)
                except Exception as e:
                    logging.error(f"Ошибка парсинга даты: {e}")
        if not dates_set:
            await update_admin_message(context, user_id, "Нет отчетов за выбранный месяц.", get_admin_keyboard())
            return
        sorted_dates = sorted(list(dates_set), key=lambda d: datetime.datetime.strptime(d, "%d-%m-%Y"))
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(date, callback_data=f"day_{date}")] for date in sorted_dates])
        await update_admin_message(context, user_id, "Выберите дату отчета:", keyboard)
    elif data.startswith("day_"):
        report_date_str = data[4:]
        # Для каждого зарегистрированного чата формируем блок отчета
        message_lines = [f"Отчеты за {report_date_str}:\n"]
        for group_id_str, group_name in registered_groups.items():
            group_id = int(group_id_str)
            report_found = None
            if group_id in archive_reports:
                # Ищем отчет по выбранной дате
                for report in archive_reports[group_id]:
                    if report.report_date == report_date_str:
                        report_found = report
                        break
            if report_found:
              if report_found.photo_count > 0:
                status_line = f"✅ (получено {report_found.photo_count} фото)"
                last_report_line = report_found.report_time
              else:
                status_line = "❌"
                last_report_line = "Нет данных"
            else:
                status_line = "❌"
                last_report_line = "Нет данных"

            message_lines.append(f"Группа: {group_name}")
            message_lines.append(f"Статус: {status_line}")
            message_lines.append(f"Последний отчет: {last_report_line}")
            message_lines.append("-------------------------\n")
        message_text = "\n".join(message_lines)
        await update_admin_message(context, user_id, message_text, get_admin_keyboard())

# Обработчик команды /start для администраторов
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Нет доступа к боту.")
        return
    if update.message.chat.type == "private":
        await update_admin_message(
            context,
            user_id,
            "Добро пожаловать! Этот бот формирует фотоотчеты для групп.",
            get_admin_keyboard()
        )
    else:
        await update.message.reply_text("Привет! Этот бот формирует фотоотчеты для группы.")

# Обработчик входящих фото: сохраняет фото во временное хранилище
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    temp_photo_storage[chat_id].append((update.message, datetime.datetime.now()))

# Обработчик команды "фотоотчет" (регистронезависимо)
async def report_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    # Если группа ещё не зарегистрирована, регистрируем её
    if str(chat_id) not in registered_groups:
        registered_groups[str(chat_id)] = update.message.chat.title or f"Chat_{chat_id}"
        save_registered_groups(registered_groups)
        logging.info(f"Группа {chat_id} зарегистрирована автоматически.")

    now = datetime.datetime.now(KYIV_TZ)
    # Проверяем, что сегодня пятница (weekday 4)
    if now.weekday() != 4:
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

    await asyncio.sleep(5)  # Ждем, чтобы гарантированно получить все фото
    current_time = datetime.datetime.now()
    recent_photos = []
    file_ids = []
    for msg, timestamp in list(temp_photo_storage[chat_id]):
        if (current_time - timestamp).seconds <= 20:
            recent_photos.append(msg)
            if msg.photo:
                file_ids.append(msg.photo[-1].file_id)
    logging.info(f"Проверка отчета: найдено {len(recent_photos)} фото")
    if recent_photos:
        report_date_str = now.strftime("%d-%m-%Y")
        report_time_str = now.strftime("%H:%M")
        new_report = ArchiveReport(
          group_id=chat_id,
          group_name=update.message.chat.title or f"Chat_{chat_id}",
          report_date=report_date_str,
          report_time=report_time_str,
          photo_count=len(recent_photos)
        )

        if chat_id in archive_reports:
            archive_reports[chat_id].append(new_report)
        else:
            archive_reports[chat_id] = [new_report]
        save_archive_reports(archive_reports)
        await update.message.reply_text(f"Отчет принят! Всего фотографий: {len(recent_photos)}")
        temp_photo_storage[chat_id].clear()
    else:
        await update.message.reply_text("Отчет не принят. Нет фотографий для отчета.")

# Задача по очистке устаревших фото из временного хранилища
async def clear_old_photos():
    while True:
        now = datetime.datetime.now()
        for chat_id, photos in list(temp_photo_storage.items()):
            temp_photo_storage[chat_id] = deque([
                (msg, timestamp) for msg, timestamp in photos
                if (now - timestamp).seconds <= 60
            ])
        await asyncio.sleep(30)

# Функция очистки архивных отчетов старше 2 месяцев
def clean_old_archive_reports():
    global archive_reports
    cutoff_date = datetime.datetime.now() - datetime.timedelta(days=60)
    for group_id, reports in list(archive_reports.items()):
        new_reports = []
        for report in reports:
            try:
                report_date = datetime.datetime.strptime(report.report_date, "%d-%m-%Y")
                if report_date >= cutoff_date:
                    new_reports.append(report)
            except Exception as e:
                logging.error(f"Ошибка парсинга даты при очистке архивов: {e}")
        if new_reports:
            archive_reports[group_id] = new_reports
        else:
            del archive_reports[group_id]
    save_archive_reports(archive_reports)
    logging.info("Старые архивные отчеты очищены.")

# Обработчик изменения статуса бота в чате (регистрация/удаление группы)
async def welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    status_change = update.my_chat_member
    if status_change.new_chat_member.status in ["kicked", "left"]:
        if str(chat.id) in registered_groups:
            del registered_groups[str(chat.id)]
            save_registered_groups(registered_groups)
            logging.info(f"Bot was removed from group {chat.title} ({chat.id}). Group data deleted.")
        return
    # Регистрация группы, если её ещё нет
    if str(chat.id) not in registered_groups:
        registered_groups[str(chat.id)] = chat.title or f"Chat_{chat.id}"
        save_registered_groups(registered_groups)
        logging.info(f"Группа {chat.id} зарегистрирована через welcome_message.")

def setup_scheduler(app):
    scheduler = AsyncIOScheduler()
    kyiv_tz = timezone("Europe/Kiev")
    # Ежедневная очистка архивов старше 2 месяцев (в 00:05)
    scheduler.add_job(
        clean_old_archive_reports,
        "cron",
        hour=0,
        minute=5,
        timezone=kyiv_tz
    )
    scheduler.add_job(
        lambda: asyncio.create_task(send_latest_report_to_admins(app)),
        "cron",
        day_of_week="sun",
        hour=12,
        minute=0,
        timezone=kyiv_tz
    )
    scheduler.add_job(
        auto_create_empty_report,
        "cron",
        day_of_week="fri",
        hour=23,
        minute=59,
        timezone=kyiv_tz
    )


    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.create_task(run_scheduler(scheduler))

async def run_scheduler(scheduler):
    scheduler.start()

def main():
    loop = asyncio.get_event_loop()
    loop.create_task(clear_old_photos())

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    # Обработчик callback-запросов для админского интерфейса
    app.add_handler(CallbackQueryHandler(button_handler))
    # Обработчик для входящих фото
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    # Обработчик для команды "фотоотчет" (регистронезависимо)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^фотоотч[её]т$"), report_handler))
    # Обработчик регистрации/удаления группы по изменениям статуса
    app.add_handler(ChatMemberHandler(welcome_message, ChatMemberHandler.MY_CHAT_MEMBER))

    logging.info("Бот успешно запущен и ожидает события...")

    setup_scheduler(app)

    loop.run_until_complete(app.run_polling())

if __name__ == "__main__":
    main()
