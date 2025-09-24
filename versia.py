# ============================================================================
# ИМПОРТЫ И КОНФИГУРАЦИЯ
# ============================================================================

import asyncio
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart, Command
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, BufferedInputFile
import sqlite3
import logging
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pytz import timezone
from datetime import datetime, timedelta, date
from config import ADMIN_PASSWORD, ACCOUNT_PASSWORD
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
import zipfile
import io
import os

from config import TOKEN
from config import WEBHOOK_URL, NEW_WEBHOOK_URL, WEBHOOK_USERS_URL, WEBHOOK_COLUMN_URL, WEBHOOK_STUDENTS_URL, WEBHOOK_ATTENDANCE_URL, WEBHOOK_NEW_STUDENTS_URL, WEBHOOK_COUNT_URL
from config import WEBHOOK_LESSONS_EDIT_URL, WEBHOOK_ADMIN_VERIFY_URL, WEBHOOK_CHECK_NEW_TEACHER_URL, WEBHOOK_ASSISTANT_URL

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Глобальные переменные
lessons_data = []  # Данные уроков в команде /lessons
current_edit_mode = False  # Флаг для определения режима редактирования
lessons_data_photo = {}  # Словарь для хранения уроков каждого пользователя при загрузке фото

# ============================================================================
# БАЗА ДАННЫХ - ПОДКЛЮЧЕНИЕ И СОЗДАНИЕ
# ============================================================================

# Создаем базу данных, если она не существует
def create_db():
    conn = sqlite3.connect('/data/userreg.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            name TEXT,
            status TEXT,
            nik_name TEXT,
            work TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            Date_L TEXT,
            Time_L TEXT,
            Point TEXT,
            Groupp TEXT,
            Teacher TEXT,
            Assist TEXT,
            Adress TEXT,
            Modul TEXT,
            Theme TEXT,
            DateLL TEXT,
            Teacher_w TEXT,
            Assist_w TEXT,
            Counter_p TEXT,
            Comment TEXT,
            Present TEXT,
            Detail TEXT,
            Insra TEXT,
            foto TEXT,
            lesson_code TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fotoalbum (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kindergarten TEXT,
            groupp TEXT,
            teacher TEXT,
            date TEXT,
            time TEXT,
            file_id TEXT,
            file_unique_id TEXT,
            file_size INTEGER,
            file_type TEXT DEFAULT 'photo',
            upload_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            point TEXT,
            groupp TEXT,
            name_s TEXT,
            student_rowid TEXT,
            column_d TEXT,
            present TEXT DEFAULT '',
            free TEXT DEFAULT '',
            is_permanent INTEGER DEFAULT 0,
            lesson_code TEXT,
            is_send INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS export_lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            point TEXT,
            groupp TEXT,
            time_l TEXT,
            date_ll TEXT,
            modul TEXT,
            theme TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

# Подключение к базе данных
def get_db_connection():
    conn = sqlite3.connect('/data/userreg.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

def add_is_send_column():
    """Добавляет колонку is_send в существующую таблицу lessons"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('ALTER TABLE lessons ADD COLUMN is_send INTEGER DEFAULT 0')
        conn.commit()
        print("[DEBUG] Колонка is_send добавлена в таблицу lessons")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("[DEBUG] Колонка is_send уже существует")
        else:
            print(f"[ERROR] Ошибка добавления колонки is_send: {e}")
    finally:
        conn.close()

def generate_lesson_code():
    """
    Генерирует уникальный 10-значный код для урока
    
    Используется для создания уникальных идентификаторов уроков
    в системе управления посещаемостью
    
    Returns:
        str: 10-значный код урока
    """
    import random
    import string
    
    while True:
        # Генерируем код: 5 букв + 5 цифр
        letters = ''.join(random.choices(string.ascii_uppercase, k=5))
        digits = ''.join(random.choices(string.digits, k=5))
        code = letters + digits
        
        # Проверяем уникальность в базе
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM lessons WHERE lesson_code = ?", (code,))
        count = cursor.fetchone()[0]
        conn.close()
        
        if count == 0:
            return code

def get_lesson_by_code(lesson_code):
    """Получает параметры урока по lesson_code"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT point, groupp, free 
            FROM lessons 
            WHERE lesson_code = ? 
            LIMIT 1
        """, (lesson_code,))
        
        result = cursor.fetchone()
        if result:
            return result[0], result[1], result[2]  # point, groupp, free
        else:
            return None, None, None
    except Exception as e:
        print(f"[ERROR] Ошибка при поиске урока по коду {lesson_code}: {e}")
        return None, None, None
    finally:
        conn.close()

# ============================================================================
# БАЗА ДАННЫХ - ОПЕРАЦИИ С РАСПИСАНИЕМ
# ============================================================================

# Функция для обновления таблицы schedule
def update_schedule_table(data, notify=True):
    """
    Обновляет таблицу schedule данными из JSON и уведомляет пользователей
    
    Args:
        data: JSON данные с расписанием
        notify: Если True, отправляет уведомления пользователям
    
    Вызывается из планировщика в 19:00 для обновления расписания
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Создаём таблицу schedule, если её нет
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            Date_L TEXT,
            Time_L TEXT,
            Point TEXT,
            Groupp TEXT,
            Teacher TEXT,
            Assist TEXT,
            Adress TEXT,
            Modul TEXT,
            Theme TEXT,
            DateLL TEXT,
            Teacher_w TEXT,
            Assist_w TEXT,
            Counter_p TEXT,
            Comment TEXT,
            Present TEXT,
            Detail TEXT,
            Insra TEXT,
            foto TEXT
        )
    """)

    # Очищаем таблицу перед добавлением новых данных
    cursor.execute("DELETE FROM schedule")

    # Заполняем таблицу данными из JSON
    added_count = 0
    for item in data:
        cursor.execute("""
            INSERT INTO schedule (
                Date_L, Time_L, Point, Groupp, Teacher, Assist, 
                Adress, Modul, Theme, DateLL, Teacher_w, Assist_w, Counter_p,
                Comment, Present, Detail, Insra
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item.get("Date_L", ""),
            item.get("Time_L", ""),
            item.get("Point", ""),
            item.get("Groupp", ""),
            item.get("Teacher", ""),
            item.get("Assist", ""),
            item.get("Adress", ""),
            item.get("Modul", ""),
            item.get("Theme", ""),
            item.get("DateLL", ""),
            "",  # Teacher_w - пустая строка
            "",  # Assist_w - пустая строка
            item.get("Counter_p", ""),
            item.get("Comment", ""),
            item.get("Present", ""),
            item.get("Detail", ""),
            item.get("Insra", "")
        ))
        added_count += 1

    conn.commit()

    # Уведомление администраторам и DoubleA
    try:
        cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
        admins = cursor.fetchall()
        message = f"Расписание обновлено!\n"
        message += f"Добавлено уроков: {added_count}"
        for admin in admins:
            asyncio.create_task(bot.send_message(chat_id=admin[0], text=message))
    except Exception as e:
        print(f"Ошибка при отправке уведомления администраторам: {e}")

    # После обновления таблицы schedule обрабатываем расписание и уведомляем пользователей
    if notify:
        process_schedule_and_notify()

    conn.close()
    



# Функция для отправки POST-запроса на вебхук
async def send_post_request():
    try:
        response = requests.post(WEBHOOK_URL)
        print("Запрос отправлен.")

        if response.status_code == 200:
            data = response.json()
            update_schedule_table(data)
            print("Данные успешно обновлены!")
        else:
            print(f"Ошибка при отправке запроса: {response.status_code}")
    except Exception as e:
        print(f"Произошла ошибка: {e}")

# Асинхронная функция для очистки lessons и обновления column в 00:00
async def clear_lessons_and_update_column():
    # Создаём таблицу lessons, если её нет
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            point TEXT,
            groupp TEXT,
            name_s TEXT,
            student_rowid TEXT,
            column_d TEXT,
            present TEXT DEFAULT '',
            free TEXT DEFAULT ''
        )
    """)
    # Очищаем таблицу lessons
    cursor.execute("DELETE FROM lessons")
    lessons_deleted = cursor.rowcount
    print(f"[00:00] Удалено записей из lessons: {lessons_deleted}")
    conn.commit()
    # Обновляем таблицу column
    update_column_table()

# Асинхронная функция для очистки старых данных каждую пятницу в 23:57
async def cleanup_old_data_friday():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Получаем текущее время в Казахстане
        kaz_time = datetime.now(timezone("Asia/Ho_Chi_Minh"))
        print(f"[FRIDAY CLEANUP] Текущее время в Казахстане: {kaz_time.strftime('%Y-%m-%d %H:%M')}")
        
        # Определяем дату прошлой субботы (6 дней назад от пятницы)
        past_saturday = kaz_time - timedelta(days=6)
        past_saturday_str = past_saturday.strftime('%Y-%m-%d')
        print(f"[FRIDAY CLEANUP] Удаляем данные до: {past_saturday_str}")
        
        # 1. Очищаем таблицу schedule полностью
        cursor.execute("DELETE FROM schedule")
        schedule_deleted = cursor.rowcount
        print(f"[FRIDAY CLEANUP] Удалено записей из schedule: {schedule_deleted}")
        
        # 2. Удаляем старые записи из fotoalbum (до прошлой субботы)
        cursor.execute("DELETE FROM fotoalbum WHERE date < ?", (past_saturday_str,))
        foto_deleted = cursor.rowcount
        print(f"[FRIDAY CLEANUP] Удалено записей из fotoalbum: {foto_deleted}")
        
        # 3. Удаляем старые записи из export_lessons (до прошлой субботы)
        cursor.execute("DELETE FROM export_lessons WHERE date_ll < ?", (past_saturday_str,))
        export_deleted = cursor.rowcount
        print(f"[FRIDAY CLEANUP] Удалено записей из export_lessons: {export_deleted}")
        
        conn.commit()
        print(f"[FRIDAY CLEANUP] Очистка завершена успешно!")
        print(f"[FRIDAY CLEANUP] Итого удалено: schedule={schedule_deleted}, fotoalbum={foto_deleted}, export_lessons={export_deleted}")
        
    except Exception as e:
        print(f"[ERROR FRIDAY CLEANUP] Ошибка при очистке: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

# Функция для получения списка подтверждений от преподавателей (ассиситентов) в конце дня
async def send_info_report():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Получаем всех администраторов и DoubleA
        cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
        admins = cursor.fetchall()

        # Формируем списки пользователей с никнеймами
        # Подтвержденные
        cursor.execute("SELECT name, nik_name FROM users WHERE work = 'accept'")
        accepted_users = [
            f"{user[0]} ({user[1]})" if user[1] else user[0]
            for user in cursor.fetchall()
        ]

        # Ожидающие
        cursor.execute("SELECT name, nik_name FROM users WHERE work = 'wait'")
        waiting_users = [
            f"{user[0]} ({user[1]})" if user[1] else user[0]
            for user in cursor.fetchall()
        ]

        # Отказы
        cursor.execute("SELECT name, nik_name FROM users WHERE work = 'cancel'")
        canceled_users = [
            f"{user[0]} ({user[1]})" if user[1] else user[0]
            for user in cursor.fetchall()
        ]

        # Создаем сообщение
        message_text = "Ежедневный отчет:\n\nПодтверждены уроки:\n"
        message_text += ", ".join(accepted_users) if accepted_users else "нет данных"
        message_text += "\n\nОжидают подтверждения:\n"
        message_text += ", ".join(waiting_users) if waiting_users else "нет данных"
        message_text += "\n\nОтказы:\n"
        message_text += ", ".join(canceled_users) if canceled_users else "нет данных"

        # Отправляем сообщение всем администраторам и DoubleA
        for admin in admins:
            await bot.send_message(chat_id=admin[0], text=message_text)

    except Exception as e:
        print(f"Ошибка при отправке автоматического отчета: {e}")
    finally:
        conn.close()


# ============================================================================
# ПЛАНИРОВЩИКИ И УВЕДОМЛЕНИЯ
# ============================================================================

# Функция для запуска планировщика задач
async def start_scheduler():
    """
    Запускает планировщик задач с различными расписаниями:
    
    - 19:00 - обновление расписания (send_post_request)
    - 20:00 - ежедневный отчет (send_info_report)
    - 00:00 - очистка данных (clear_lessons_and_update_column)
    - Каждые 5 минут - проверка уроков и напоминания
    
    Все время указано по часовому поясу Казахстана (Asia/Ho_Chi_Minh)
    """
    scheduler = AsyncIOScheduler()
    kazakhstan_timezone = timezone("Asia/Ho_Chi_Minh")  # Часовой пояс Казахстана

    # Добавляем задачу в планировщика (каждый день в 19:00 по времени Казахстана)
    scheduler.add_job(
        send_post_request,
        CronTrigger(hour=19, minute=0, timezone=kazakhstan_timezone)
    )
    # Новая задача для ежедневного отчета в 20:00 (только отчет, без очистки lessons и update_column_table)
    scheduler.add_job(
        send_info_report,
        CronTrigger(hour=20, minute=00, timezone=kazakhstan_timezone)
    )
    # Новая задача для очистки lessons и обновления column в 00:00
    scheduler.add_job(
        clear_lessons_and_update_column,
        CronTrigger(hour=0, minute=0, timezone=kazakhstan_timezone)
    )
    # Новая задача для проверки уроков каждые 5 минут
    scheduler.add_job(
        check_upcoming_lessons,
        CronTrigger(
            minute='4,9,14,19,24,29,34,39,44,49,54,59',  # 4, 9, 14, 19, 24, 29, 34, 39, 44, 49, 54, 59
            timezone=kazakhstan_timezone)
    )
    scheduler.add_job(
        check_pending_lessons,
        CronTrigger(
            minute='*/5',  # Каждые 5 минут
            timezone=kazakhstan_timezone)
    )
    scheduler.add_job(
        check_lessons_10min_before,
        trigger='cron',
        minute='*/5',  # Каждые 5 минут (0,5,10,15...)
        timezone=kazakhstan_timezone
    )

    # Планировщик для напоминаний о фотографиях каждые 5 минут
    scheduler.add_job(
        check_photo_reminders,
        CronTrigger(
            minute='*/5',  # Каждые 5 минут
            timezone=kazakhstan_timezone)
    )

    # Новая задача для очистки старых данных каждую субботу в 23:57 по времени Казахстана
    scheduler.add_job(
        cleanup_old_data_friday,
        CronTrigger(day_of_week='sat', hour=23, minute=57, timezone=kazakhstan_timezone)
    )

    scheduler.start()
    print("Планировщик запущен.")

# ============================================================================
# ОБРАБОТЧИКИ ФОТОГРАФИЙ
# ============================================================================

# Функция для проверки напоминаний о фотографиях
async def check_photo_reminders():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Получаем текущее время в Казахстане
        kaz_time = datetime.now(timezone("Asia/Ho_Chi_Minh"))
        print(f"[DEBUG PHOTO REMINDER] Текущее время в Казахстане: {kaz_time.strftime('%H:%M')}")
        
        # Проверяем уроки, которые закончились 45 минут, 1:45 и 2:45 назад
        reminder_times = []
        for hours in [0, 1, 2]:
            for minutes in [45]:
                reminder_time = kaz_time - timedelta(hours=hours, minutes=minutes)
                reminder_times.append(reminder_time.strftime("%H:%M"))
        
        print(f"[DEBUG PHOTO REMINDER] Времена напоминаний: {reminder_times}")
        
        # Ищем уроки с foto = 'wait' и временем окончания в нужные моменты
        lessons_to_remind = {}
        
        for reminder_time in reminder_times:
            print(f"[DEBUG PHOTO REMINDER] Ищем уроки для времени {reminder_time}")
            
            cursor.execute("""
                SELECT Point, Groupp, Teacher, Time_L, DateLL
                FROM schedule 
                WHERE foto = 'wait' AND Time_L = ?
            """, (reminder_time,))
            
            lessons = cursor.fetchall()
            print(f"[DEBUG PHOTO REMINDER] Найдено уроков для {reminder_time}: {len(lessons)}")
            
            for lesson in lessons:
                point, groupp, teacher, time_l, date_ll = lesson
                print(f"[DEBUG PHOTO REMINDER] Урок: {point}, {groupp}, {teacher}, {time_l}, {date_ll}")
                
                if teacher not in lessons_to_remind:
                    lessons_to_remind[teacher] = []
                
                lessons_to_remind[teacher].append({
                    'point': point,
                    'groupp': groupp,
                    'time_l': time_l,
                    'date_ll': date_ll
                })
        
        print(f"[DEBUG PHOTO REMINDER] Всего преподавателей для напоминаний: {len(lessons_to_remind)}")
        
        # Отправляем напоминания преподавателям
        for teacher_name, lessons in lessons_to_remind.items():
            print(f"[DEBUG PHOTO REMINDER] Обрабатываем преподавателя: {teacher_name}")
            
            # Получаем telegram_id преподавателя
            cursor.execute("SELECT telegram_id FROM users WHERE name = ?", (teacher_name,))
            teacher_row = cursor.fetchone()
            
            if teacher_row:
                teacher_id = teacher_row[0]
                print(f"[DEBUG PHOTO REMINDER] Найден telegram_id: {teacher_id}")
                
                # Формируем сообщение с напоминанием
                message = "📸 Отправьте фото и видео по урокам:\n\n"
                
                for lesson in lessons:
                    message += f"• {lesson['point']}, {lesson['groupp']}, {lesson['time_l']}\n"
                
                message += "\nИспользуйте команду /foto для загрузки фото и видео."
                
                try:
                    await bot.send_message(chat_id=teacher_id, text=message)
                    print(f"[PHOTO REMINDER] Напоминание отправлено преподавателю {teacher_name}")
                except Exception as e:
                    print(f"[ERROR] Ошибка отправки напоминания преподавателю {teacher_name}: {e}")
            else:
                print(f"[DEBUG PHOTO REMINDER] Преподаватель {teacher_name} не найден в таблице users")
        
        # Дополнительная отладка: показываем все уроки с foto = 'wait'
        cursor.execute("SELECT Point, Groupp, Teacher, Time_L, DateLL, foto FROM schedule WHERE foto = 'wait'")
        all_wait_lessons = cursor.fetchall()
        print(f"[DEBUG PHOTO REMINDER] Всего уроков с foto = 'wait': {len(all_wait_lessons)}")
        for lesson in all_wait_lessons:
            print(f"[DEBUG PHOTO REMINDER] Урок с foto = 'wait': {lesson}")
        
    except Exception as e:
        print(f"[ERROR] Ошибка в check_photo_reminders: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()




# ============================================================================
# ОБРАБОТЧИКИ РЕГИСТРАЦИИ
# ============================================================================

# Проверяем, зарегистрирован ли пользователь
def is_user_registered(telegram_id):
    """
    Проверяет, зарегистрирован ли пользователь в системе
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        bool: True если пользователь зарегистрирован, False иначе
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    user = cursor.fetchone()
    conn.close()
    return user

# Добавляем пользователя в базу данных
def register_user(telegram_id, name, status, nik_name):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (telegram_id, name, status, nik_name) VALUES (?, ?, ?, ?)", (telegram_id, name, status, nik_name))
    conn.commit()
    conn.close()
    
    # Отправляем веб-хук для новых преподавателей
    if status == "Teacher":
        send_new_teacher_webhook(name)

# Функция для отправки веб-хука о новом преподавателе
def send_new_teacher_webhook(teacher_name):
    try:
        payload = {"teacher_name": teacher_name}
        response = requests.post(WEBHOOK_CHECK_NEW_TEACHER_URL, json=payload, timeout=10)
        # Логируем результат, но не показываем пользователю
        logging.info(f"Webhook sent for new teacher {teacher_name}: {response.status_code}")
    except Exception as e:
        # Логируем ошибку, но не показываем пользователю
        logging.error(f"Failed to send webhook for new teacher {teacher_name}: {e}")

# Состояния для FSM (Finite State Machine)
# ============================================================================
# FSM СОСТОЯНИЯ
# ============================================================================

class Registration(StatesGroup):
    waiting_for_role = State()
    waiting_for_password = State()
    waiting_for_name = State()
    waiting_for_account_password = State()  # для роли Account
    waiting_for_admin_choice = State()      # для выбора совмещения ролей Admin

# Состояния для загрузки фотографий
class PhotoUpload(StatesGroup):
    waiting_for_lesson_selection = State()
    waiting_for_photos = State()

# ============================================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================================

# Команда /start
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    """
    Обработчик команды /start
    
    - Проверяет, зарегистрирован ли пользователь
    - Если зарегистрирован - показывает главное меню
    - Если нет - запускает процесс регистрации
    """
    user_id = message.from_user.id
    user_registered = is_user_registered(user_id)

    if user_registered:
        await message.answer("Ты уже зарегистрирован!")
    else:
        # Кнопка для регистрации
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Регистрация", callback_data="register")]
            ]
        )
        await message.answer(f"Приветики, {message.from_user.first_name}, зарегистрируйся!", reply_markup=keyboard)

# Обработка нажатия кнопки "Регистрация"
@dp.callback_query(lambda callback: callback.data == "register")
async def register(callback: CallbackQuery, state: FSMContext):
    # Кнопки выбора роли
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Администратор", callback_data="role_admin")],
            [InlineKeyboardButton(text="Преподаватель", callback_data="role_teacher")],#убра
            [InlineKeyboardButton(text="Аккаунт", callback_data="role_account")],
        ]
    )
    await callback.message.answer("Выбери свою роль:", reply_markup=keyboard)

    # Устанавливаем состояние ожидания роли
    await state.set_state(Registration.waiting_for_role)

# Обработка выбора роли
@dp.callback_query(lambda callback: callback.data.startswith("role_"))
async def set_role(callback: CallbackQuery, state: FSMContext):
    roles = {
        "role_admin": "Admin",
        "role_teacher": "Teacher",
        "role_account": "Account"
    }
    role = roles[callback.data]

    await state.update_data(role=role)  # Сохраняем роль во временное состояние
    if role == "Admin":
        # Для админа запрашиваем пароль
        await callback.message.answer("Введите пароль для администратора:")
        await state.set_state(Registration.waiting_for_password)
    elif role == "Account":
        # Для Account запрашиваем пароль
        await callback.message.answer("Введите пароль для аккаунта:")
        await state.set_state(Registration.waiting_for_account_password)
    else:
        # Для Teacher сразу запрашиваем имя
        await callback.message.answer("Введите свое имя:")
        await state.set_state(Registration.waiting_for_name)

# Обработка пароля при регистрации
@dp.message(StateFilter(Registration.waiting_for_password))
async def check_admin_password(message: Message, state: FSMContext):
    if message.text == ADMIN_PASSWORD:
        # Добавляем вопрос о совмещении ролей
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Да", callback_data="admin_choice_yes")],
                [InlineKeyboardButton(text="Нет", callback_data="admin_choice_no")]
            ]
        )
        await message.answer("Вы будете совмещать роль Аккаунта?", reply_markup=keyboard)
        await state.set_state(Registration.waiting_for_admin_choice)
    else:
        await message.answer("❌ Неверный пароль! Регистрация отменена.")
        await state.clear()  # Сбрасываем состояние

        # Показываем кнопку для начала регистрации заново
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Регистрация", callback_data="register")]
            ]
        )
        await message.answer("Нажмите для повторной регистрации:", reply_markup=keyboard)


# Обработка пароля Account при регистрации
@dp.message(StateFilter(Registration.waiting_for_account_password))
async def check_account_password(message: Message, state: FSMContext):
    if message.text == ACCOUNT_PASSWORD:
        await message.answer("Пароль верный! Теперь введите свое имя:")
        await state.set_state(Registration.waiting_for_name)
    else:
        await message.answer("❌ Неверный пароль! Регистрация отменена.")
        await state.clear()  # Сбрасываем состояние

        # Показываем кнопку для начала регистрации заново
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Регистрация", callback_data="register")]
            ]
        )
        await message.answer("Нажмите для повторной регистрации:", reply_markup=keyboard)


# Обработка выбора совмещения ролей для Admin
@dp.callback_query(lambda callback: callback.data.startswith("admin_choice_"))
async def handle_admin_choice(callback: CallbackQuery, state: FSMContext):
    choice = callback.data
    if choice == "admin_choice_yes":
        await state.update_data(role="DoubleA")
        await callback.message.answer("Вы будете совмещать роль Аккаунта. Теперь введите свое имя:")
        await state.set_state(Registration.waiting_for_name)
    else:  # admin_choice_no
        await state.update_data(role="Admin")
        await callback.message.answer("Теперь введите свое имя:")
        await state.set_state(Registration.waiting_for_name)
    await callback.answer()


# Обработка ввода имени
@dp.message(StateFilter(Registration.waiting_for_name))
async def set_name(message: Message, state: FSMContext):
    name = message.text
    user_id = message.from_user.id


    #ВСТАВКА
    # Проверяем, существует ли уже такое имя в базе
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE name = ?", (name,))
    existing_user = cursor.fetchone()
    conn.close()

    if existing_user:
        # Если имя уже занято - выводим сообщение и сбрасываем состояние
        await message.answer("❌ Это имя уже занято. Пожалуйста, начните регистрацию заново.")
        await state.clear()

        # Возвращаем пользователя на начало регистрации
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Регистрация", callback_data="register")]
            ]
        )
        await message.answer("Нажмите для повторной регистрации:", reply_markup=keyboard)
        return
    #ВСТАВКА



    # Получаем username и добавляем @
    nik_name = f"@{message.from_user.username}" if message.from_user.username else ""

    # Получаем данные из состояния
    data = await state.get_data()
    role = data.get("role")

    # Регистрируем пользователя в базе данных (функция register_user должна быть определена)
    register_user(user_id, name, role, nik_name)

    await message.answer("Ты успешно зарегистрирован!")

    # Очищаем состояние после завершения регистрации
    await state.clear()

#Обработка неподтвержденных уроков за 30 минут
async def check_pending_lessons():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Текущее время по Казахстану
    kaz_time = datetime.now(timezone("Asia/Ho_Chi_Minh"))
    current_time = kaz_time.strftime("%H:%M")

    # Время урока = текущее время + 30 минут
    lesson_time = (kaz_time + timedelta(minutes=30)).strftime("%H:%M")

    # 1. Находим все подходящие уроки
    cursor.execute("""
        SELECT 
            rowid,
            Time_L,
            Point,
            Teacher,
            Assist,
            Teacher_w,
            Assist_w
        FROM schedule
        WHERE Time_L = ?
    """, (lesson_time,))
    lessons = cursor.fetchall()

    # 2. Собираем данные для отчета
    report = {
        'teacher_wait': [],
        'teacher_cancel': [],
        'assist_wait': [],
        'assist_cancel': [],
        'update_ids': []  # ID строк для обновления
    }

    for lesson in lessons:
        rowid, time_l, point, teacher, assist, t_status, a_status = lesson

        # Обработка преподавателей
        if t_status in ('wait', 'cancel'):
            # Получаем ник преподавателя
            cursor.execute("""
                SELECT nik_name FROM users 
                WHERE name = ? AND status IN ('Teacher', 'Admin', 'DoubleA', 'Account')
            """, (teacher,))
            nik = cursor.fetchone()
            nik = nik[0] if nik else "нет ника"

            entry = f"{time_l}, {teacher}, {nik}, {point}"

            if t_status == 'wait':
                report['teacher_wait'].append(entry)
                report['update_ids'].append(rowid)  # Добавляем для обновления
            else:
                report['teacher_cancel'].append(entry)

        # Обработка ассистентов
        if a_status in ('wait', 'cancel'):
            # Получаем ник ассистента
            cursor.execute("""
                SELECT nik_name FROM users 
                WHERE name = ? AND status IN ('Teacher', 'Admin', 'DoubleA', 'Account')
            """, (assist,))
            nik = cursor.fetchone()
            nik = nik[0] if nik else "нет ника"

            entry = f"{time_l}, {assist}, {nik}, {point}"

            if a_status == 'wait':
                report['assist_wait'].append(entry)
                report['update_ids'].append(rowid)  # Добавляем для обновления
            else:
                report['assist_cancel'].append(entry)

    # 3. Формируем сообщение
    message_parts = []

    if report['teacher_wait']:
        message_parts.append("Преподаватели не подтвердили уроки:\n" + "\n".join(report['teacher_wait']))

    if report['teacher_cancel']:
        message_parts.append("Преподаватели отказались от урока:\n" + "\n".join(report['teacher_cancel']))

    if report['assist_wait']:
        message_parts.append("Ассистенты не подтвердили уроки:\n" + "\n".join(report['assist_wait']))

    if report['assist_cancel']:
        message_parts.append("Ассистенты отказались от урока:\n" + "\n".join(report['assist_cancel']))

    full_message = "\n\n".join(message_parts)

    # 4. Обновляем статусы wait -> waitold
    if report['update_ids']:
        # Уникальные ID для обновления
        unique_ids = list(set(report['update_ids']))

        cursor.executemany("""
            UPDATE schedule
            SET
                Teacher_w = CASE 
                                WHEN Teacher_w = 'wait' THEN 'waitold' 
                                ELSE Teacher_w 
                            END,
                Assist_w = CASE 
                              WHEN Assist_w = 'wait' THEN 'waitold' 
                              ELSE Assist_w 
                           END
            WHERE rowid = ?
        """, [(row_id,) for row_id in unique_ids])

        conn.commit()

    # 5. Отправляем сообщение администраторам и DoubleA
    if full_message:
        cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
        admins = cursor.fetchall()

        for admin in admins:
            await bot.send_message(
                chat_id=admin[0],
                text=full_message,
                parse_mode='HTML'
            )

    conn.close()



# ============================================================================
# ОБРАБОТЧИКИ УРОКОВ И ПОСЕЩАЕМОСТИ
# ============================================================================

# Функция для обработки расписания и отправки сообщений пользователям
def process_schedule_and_notify():
    """
    Обрабатывает расписание и отправляет уведомления пользователям об их уроках
    
    - Находит уроки для каждого пользователя (как преподавателя, так и ассистента)
    - Отправляет сообщения с деталями уроков
    - Обновляет статус работы пользователей
    - Уведомляет ассистентов о пробных уроках
    
    Вызывается после обновления расписания
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Получаем всех пользователей из таблицы users
    cursor.execute("SELECT telegram_id, name FROM users")
    users = cursor.fetchall()

    # Словарь для хранения сообщений для каждого пользователя
    messages = {}

    # Проходим по всем пользователям
    for user in users:
        telegram_id, name = user

        # Ищем уроки, где пользователь Teacher ИЛИ Assist (изменение здесь)
        cursor.execute("""
            SELECT Time_L, Point, Adress, Theme, Modul, Insra, Detail, Present, Comment, datell
            FROM schedule
            WHERE Teacher = ? OR Assist = ?
        """, (name, name))  # Добавлен второй параметр
        lessons = cursor.fetchall()

        if lessons:
            # Если уроки найдены, формируем сообщение
            message = "Ваши запланированные уроки:\n"
            for lesson in lessons:
                time_l, point, adress, theme, modul, insra, detail, present, comment, datell = lesson
                message += f"\nДата: {datell}\nВремя: {time_l}\nСадик: {point}\nАдрес: {adress}\n"
                if insra:
                    message += f"Сценарий: <a href=\"{insra}\">страница</a>\n"
                if detail:
                    message += f"Детали: <a href=\"{detail}\">страница</a>\n"
                if present:
                    message += f"Презентация: <a href=\"{present}\">страница</a>\n"
                message += f"Тема: {modul}, {theme}\n"
                if comment and comment.strip():
                    message += f"Комментарии: {comment.strip()}\n"
            # Добавляем сообщение в словарь
            messages[telegram_id] = message

            # Обновляем поле work для пользователя
            cursor.execute("UPDATE users SET work = 'wait' WHERE telegram_id = ?", (telegram_id,))
        else:
            # Если уроков нет, очищаем поле work
            cursor.execute("UPDATE users SET work = '' WHERE telegram_id = ?", (telegram_id,))

    # Сохраняем изменения в базе данных
    conn.commit()

    # Отправляем сообщения пользователям
    for telegram_id, message in messages.items():
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="confirm_lesson"),
                InlineKeyboardButton(text="Отказаться", callback_data="cancel_lesson")
            ]
        ])
        asyncio.create_task(bot.send_message(chat_id=telegram_id, text=message, reply_markup=keyboard, parse_mode='HTML'))

    # update_column_table()  # УДАЛЕНО: обновление теперь только в 00:00

    conn.close()
    
    # Уведомляем ассистентов о пробных уроках
    asyncio.create_task(notify_assistants_for_trial_lessons())

# ============================================================================
# ОБРАБОТЧИКИ АССИСТЕНТОВ
# ============================================================================

# Функция для поиска пробных уроков без ассистента и отправки уведомлений
async def notify_assistants_for_trial_lessons():
    """Поиск пробных уроков без ассистента и отправка уведомлений преподавателям"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Ищем пробные уроки без ассистента
        cursor.execute("""
            SELECT Point, Adress, DateLL, Time_L, rowid
            FROM schedule 
            WHERE groupp = 'Пробное' 
            AND (Assist IS NULL OR Assist = '' OR Assist = 'Нет')
        """)
        trial_lessons = cursor.fetchall()
        
        if not trial_lessons:
            print("[ASSIST] Пробных уроков без ассистента не найдено")
            conn.close()
            return
        
        print(f"[ASSIST] Найдено {len(trial_lessons)} пробных уроков без ассистента")
        
        # Получаем всех преподавателей
        cursor.execute("SELECT telegram_id, name FROM users WHERE status = 'Teacher'")
        teachers = cursor.fetchall()
        
        if not teachers:
            print("[ASSIST] Преподаватели не найдены")
            conn.close()
            return
        
        # Отправляем уведомления о каждом пробном уроке
        for lesson in trial_lessons:
            point, adress, datell, time_l, lesson_id = lesson
            
            message = f"В садик {point}, по адресу {adress}, {datell} в {time_l}.\nтребуется ассистент"
            
            # Создаем кнопки
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Согласиться", callback_data=f"assist_accept:{lesson_id}"),
                    InlineKeyboardButton(text="Отказаться", callback_data=f"assist_decline:{lesson_id}")
                ]
            ])
            
            # Отправляем всем преподавателям
            for teacher_id, teacher_name in teachers:
                try:
                    await bot.send_message(
                        chat_id=teacher_id,
                        text=message,
                        reply_markup=keyboard
                    )
                    print(f"[ASSIST] Уведомление отправлено преподавателю {teacher_name} (ID: {teacher_id})")
                except Exception as e:
                    print(f"[ERROR ASSIST] Ошибка отправки уведомления преподавателю {teacher_name}: {e}")
        
        conn.close()
        
    except Exception as e:
        print(f"[ERROR ASSIST] Ошибка в notify_assistants_for_trial_lessons: {e}")
        import traceback
        traceback.print_exc()

# Обработчик кнопки "Согласиться" для ассистента
@dp.callback_query(lambda c: c.data.startswith('assist_accept:'))
async def handle_assist_accept(callback: CallbackQuery):
    """
    Обработка согласия стать ассистентом на пробный урок
    
    - Проверяет, не занят ли уже ассистент
    - Назначает ассистента в базу данных
    - Отправляет уведомление админам и DoubleA
    - Отправляет webhook с данными ассистента
    - Обновляет сообщение для преподавателя
    """
    try:
        user_id = callback.from_user.id
        lesson_id = callback.data.split(':')[1]
        
        print(f"[ASSIST] Пользователь {user_id} согласился стать ассистентом для урока {lesson_id}")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Получаем имя пользователя
        cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (user_id,))
        user_data = cursor.fetchone()
        
        if not user_data:
            await callback.answer("Ошибка: пользователь не найден")
            return
        
        user_name = user_data[0]
        
        # Проверяем, не занят ли уже ассистент
        cursor.execute("SELECT Assist FROM schedule WHERE rowid = ?", (lesson_id,))
        assist_data = cursor.fetchone()
        
        if not assist_data:
            await callback.answer("Ошибка: урок не найден")
            conn.close()
            return
        
        current_assist = assist_data[0]
        
        if current_assist and current_assist.strip() and current_assist != 'Нет':
            # Ассистент уже назначен
            await callback.message.edit_text(
                callback.message.text + "\n\nАссистент на это занятие уже выбран."
            )
            await callback.answer("Ассистент уже выбран")
            conn.close()
            return
        
        # Назначаем ассистента
        cursor.execute("UPDATE schedule SET Assist = ? WHERE rowid = ?", (user_name, lesson_id))
        conn.commit()
        
        # Получаем данные урока для webhook
        cursor.execute("SELECT Point, Adress, DateLL, Time_L FROM schedule WHERE rowid = ?", (lesson_id,))
        lesson_data = cursor.fetchone()
        
        if lesson_data:
            point, adress, datell, time_l = lesson_data
            
            # Получаем ник ассистента
            cursor.execute("SELECT nik_name FROM users WHERE name = ?", (user_name,))
            nik_row = cursor.fetchone()
            nik_name = nik_row[0] if nik_row and nik_row[0] else "нет ника"
            
            # Уведомляем админов и DoubleA о найденном ассистенте
            cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
            admins = cursor.fetchall()
            
            admin_message = f"На Пробное занятие в Садик: {point}, Дата: {datell}, Время: {time_l} найден ассистент: {user_name} ({nik_name})"
            
            for admin in admins:
                asyncio.create_task(bot.send_message(chat_id=admin[0], text=admin_message))
            
            # Отправляем webhook
            webhook_data = {
                "date": datell,
                "time": time_l,
                "point": point,
                "assistant_name": user_name
            }
            
            try:
                response = requests.post(WEBHOOK_ASSISTANT_URL, json=webhook_data, timeout=10)
                print(f"[ASSIST WEBHOOK] Отправлен webhook: {response.status_code}")
            except Exception as e:
                print(f"[ERROR ASSIST WEBHOOK] Ошибка отправки webhook: {e}")
        
        # Обновляем сообщение
        await callback.message.edit_text(
            f"Вы назначены ассистентом на занятие В садик {point}, по адресу {adress}, {datell} в {time_l}."
        )
        await callback.answer("Вы назначены ассистентом!")
        
        conn.close()
        
    except Exception as e:
        print(f"[ERROR ASSIST] Ошибка в handle_assist_accept: {e}")
        import traceback
        traceback.print_exc()
        await callback.answer("Произошла ошибка")

# Обработчик кнопки "Отказаться" для ассистента
@dp.callback_query(lambda c: c.data.startswith('assist_decline:'))
async def handle_assist_decline(callback: CallbackQuery):
    """Обработка отказа стать ассистентом"""
    try:
        user_id = callback.from_user.id
        lesson_id = callback.data.split(':')[1]
        
        print(f"[ASSIST] Пользователь {user_id} отказался стать ассистентом для урока {lesson_id}")
        
        # Обновляем сообщение
        await callback.message.edit_text(
            callback.message.text + "\n\nВы отказались"
        )
        await callback.answer("Вы отказались")
        
    except Exception as e:
        print(f"[ERROR ASSIST] Ошибка в handle_assist_decline: {e}")
        import traceback
        traceback.print_exc()
        await callback.answer("Произошла ошибка")

# Обработка кнопки "Подтвердить" вечером
@dp.callback_query(lambda c: c.data == 'confirm_lesson')
async def handle_confirm_evening(callback: CallbackQuery):
    user_id = callback.from_user.id

    conn = get_db_connection()
    cursor = conn.cursor()
    # Обновляем статус пользователя
    cursor.execute("UPDATE users SET work = 'accept' WHERE telegram_id = ?", (user_id,))

    #Получаем имя и ник пользователя
    cursor.execute("SELECT name, nik_name FROM users WHERE telegram_id = ?", (user_id,))
    user_data = cursor.fetchone()
    user_name, nik_name = user_data if user_data else ("Неизвестный", "")

    # Находим всех всех администраторов
    #cursor.execute("SELECT telegram_id FROM users WHERE status = 'Admin'")
    #admins = cursor.fetchall()
    conn.commit()
    conn.close()

    # Формируем сообщение с ником
    #admin_message = f"{user_name}"
    #if nik_name:
    #    admin_message += f" ({nik_name})"
    #admin_message += " подтвердил уроки."

    #for admin in admins:
    #    await bot.send_message(chat_id=admin[0], text=admin_message)

    await callback.answer()
    await callback.message.answer("Вы подтвердили уроки")

# Обработка кнопки "Отказаться" вечером
@dp.callback_query(lambda c: c.data == 'cancel_lesson')
async def handle_cancel_evening(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    # Обновляем статус пользователя
    cursor.execute("UPDATE users SET work = 'cancel' WHERE telegram_id = ?", (user_id,))

    # Получаем имя и ник пользователя
    cursor.execute("SELECT name, nik_name FROM users WHERE telegram_id = ?", (user_id,))
    user_data = cursor.fetchone()
    user_name, nik_name = user_data if user_data else ("Неизвестный", "")

    # Находим всех администраторов и DoubleA
    cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
    admins = cursor.fetchall()

    # Пытаемся найти rowid урока, который отменяет преподаватель (ищем по Teacher и work='cancel', берем ближайший по времени)
    cursor.execute("SELECT rowid, Time_L, Point, Groupp, Theme FROM schedule WHERE Teacher = ? ORDER BY Date_L DESC, Time_L DESC LIMIT 1", (user_name,))
    lesson_row = cursor.fetchone()
    rowid = lesson_row[0] if lesson_row else None

    # Формируем сообщение с ником
    admin_message = f"🔴 {user_name}"
    if nik_name:
        admin_message += f" ({nik_name})"
    admin_message += " ОТКАЗАЛСЯ от проведения уроков."

    # Кнопка 'Пригласить' только если rowid найден
    if rowid:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пригласить", callback_data=f"invite_teacher:{rowid}")]
        ])
        for admin in admins:
            await bot.send_message(chat_id=admin[0], text=admin_message, reply_markup=keyboard)
    else:
        for admin in admins:
            await bot.send_message(chat_id=admin[0], text=admin_message)

    conn.commit()
    conn.close()

    await callback.answer()
    await callback.message.answer("Вы отказались от уроков")

# --- Новый обработчик: приглашение преподавателей ---
@dp.callback_query(lambda c: c.data.startswith('invite_teacher:'))
async def handle_invite_teacher(callback: CallbackQuery):
    rowid = callback.data.split(':')[1]
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT Time_L, Point, Groupp, Theme FROM schedule WHERE rowid = ?", (rowid,))
    lesson = cursor.fetchone()
    if not lesson:
        await callback.answer("Урок не найден", show_alert=True)
        conn.close()
        return
    time_l, point, groupp, theme = lesson
    # Формируем текст приглашения
    message = f"Ищем преподавателя на уроки:\nВремя: {time_l}\nСадик: {point}\nГруппа: {groupp}\nТема: {theme}"
    # Кнопка 'Принять'
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Принять", callback_data=f"accept_lesson:{rowid}")]
    ])
    # Получаем всех преподавателей
    cursor.execute("SELECT telegram_id FROM users WHERE status = 'Teacher'")
    teachers = cursor.fetchall()
    for teacher in teachers:
        await bot.send_message(chat_id=teacher[0], text=message, reply_markup=keyboard)
    conn.close()
    await callback.answer("Приглашение отправлено преподавателям")

# --- Новый обработчик: принятие урока преподавателем ---
@dp.callback_query(lambda c: c.data.startswith('accept_lesson:'))
async def handle_accept_lesson(callback: CallbackQuery):
    rowid = callback.data.split(':')[1]
    user_id = callback.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    # Получаем имя и ник преподавателя
    cursor.execute("SELECT name, nik_name FROM users WHERE telegram_id = ?", (user_id,))
    user_data = cursor.fetchone()
    user_name, nik_name = user_data if user_data else ("Неизвестный", "")
    # Получаем параметры урока
    cursor.execute("SELECT Time_L, Point, Groupp, Theme FROM schedule WHERE rowid = ?", (rowid,))
    lesson = cursor.fetchone()
    if not lesson:
        await callback.answer("Урок не найден", show_alert=True)
        conn.close()
        return
    time_l, point, groupp, theme = lesson
    # Сообщение для админов
    admin_message = f"🟢 {user_name}"
    if nik_name:
        admin_message += f" ({nik_name})"
    admin_message += f" ПРИНЯЛ уроки:\nВремя: {time_l}\nСадик: {point}\nГруппа: {groupp}\nТема: {theme}"
    # Получаем всех админов и DoubleA
    cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
    admins = cursor.fetchall()
    for admin in admins:
        await bot.send_message(chat_id=admin[0], text=admin_message)
    conn.close()
    await callback.answer("Вы приняли урок! Информация отправлена администраторам.")




#Принятие урока за час перед уроком
@dp.callback_query(lambda c: c.data.startswith('upcoming_confirm_'))
async def handle_confirm_upcoming(callback: CallbackQuery):
    data = callback.data.split('_')
    telegram_id = int(data[2])
    rowid = int(data[3])

    conn = get_db_connection()
    cursor = conn.cursor()

    # Получаем имя пользователя по telegram_id
    cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    name = row[0] if row else None
    if not name:
        conn.close()
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    # Обновляем статус в зависимости от роли (Teacher_w или Assist_w)
    cursor.execute("""
        UPDATE schedule 
        SET 
            Teacher_w = CASE 
                            WHEN Teacher = ? AND Teacher_w = 'wait' 
                            THEN 'accept' 
                            ELSE Teacher_w 
                         END,
            Assist_w = CASE 
                          WHEN Assist = ? AND Assist_w = 'wait' 
                          THEN 'accept' 
                          ELSE Assist_w 
                       END
        WHERE rowid = ?
        AND (Teacher_w = 'wait' OR Assist_w = 'wait')
    """, (name, name, rowid))

    if cursor.rowcount == 0:
        conn.close()
        return  # Просто выходим без уведомлений

    conn.commit()
    conn.close()

    await callback.answer()
    await callback.message.answer("Вы подтвердили урок.")

#Отказ от урока за час перед уроком
@dp.callback_query(lambda c: c.data.startswith('upcoming_cancel_'))
async def handle_cancel_upcoming(callback: CallbackQuery):
    data = callback.data.split('_')
    telegram_id = int(data[2])
    rowid = int(data[3])

    conn = get_db_connection()
    cursor = conn.cursor()

    # Получаем имя пользователя по telegram_id
    cursor.execute("SELECT name, nik_name FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    name = row[0] if row else None
    nik_name = row[1] if row else ""
    if not name:
        conn.close()
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    # Обновляем статус в зависимости от роли (Teacher_w или Assist_w)
    cursor.execute("""
        UPDATE schedule 
        SET 
            Teacher_w = CASE 
                            WHEN Teacher = ? AND Teacher_w = 'wait' 
                            THEN 'cancel' 
                            ELSE Teacher_w 
                         END,
            Assist_w = CASE 
                          WHEN Assist = ? AND Assist_w = 'wait' 
                          THEN 'cancel' 
                          ELSE Assist_w 
                       END
        WHERE rowid = ?
        AND (Teacher_w = 'wait' OR Assist_w = 'wait')
    """, (name, name, rowid))

    if cursor.rowcount == 0:
        conn.close()
        return  # Просто выходим без уведомлений

    # Уведомление администраторам
    admin_message = f"🔴{name}"
    if nik_name:
        admin_message += f" ({nik_name})"
    admin_message += " ОТКАЗАЛСЯ от урока."

    cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
    admins = cursor.fetchall()
    for admin in admins:
        await bot.send_message(chat_id=admin[0], text=admin_message)

    conn.commit()
    conn.close()

    await callback.answer()
    await callback.message.answer("Вы отказались от урока.")




# Функция для удаления пользователя из базы данных
def delete_user(telegram_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()

## Команда /delete
@dp.message(Command("delete"))
async def delete_user_command(message: Message):
    user_id = message.from_user.id
    user_registered = is_user_registered(user_id)

    if user_registered:
        # Удаляем пользователя из базы данных
        delete_user(user_id)
        await message.answer("Ты успешно удалён из базы данных.")
    else:
        await message.answer("Ты не зарегистрирован, поэтому нечего удалять.")

#Рассылка за час до занятия
async def check_upcoming_lessons():
    conn = get_db_connection()
    cursor = conn.cursor()

    kaz_time = datetime.now(timezone("Asia/Ho_Chi_Minh"))
    time_plus_1h = (kaz_time + timedelta(minutes=61)).strftime("%H:%M")

    # Выбираем уроки с преподавателем и ассистентом
    cursor.execute("""
        SELECT rowid, Time_L, Point, Adress, Teacher, Assist
        FROM schedule
        WHERE Time_L = ?
    """, (time_plus_1h,))

    lessons = cursor.fetchall()

    for lesson in lessons:
        rowid, time_l, point, address, teacher, assist = lesson

        # Обрабатываем преподавателя и ассистента
        for role, name, status_column in [
            ('Teacher', teacher, 'Teacher_w'),
            ('Assist', assist, 'Assist_w')
        ]:
            if not name:  # Пропускаем пустые значения
                continue

            # Проверка регистрации в системе
            cursor.execute("SELECT 1 FROM users WHERE name = ?", (name,))
            if not cursor.fetchone():
                continue

            # Проверка ранних уроков для роли
            cursor.execute(f"""
                SELECT 1 
                FROM schedule 
                WHERE {role} = ? 
                AND Point = ? 
                AND Time_L < ?
                LIMIT 1
            """, (name, point, time_l))

            if cursor.fetchone():
                continue  # Есть ранние уроки - пропускаем

            # Сбор всех уроков для роли в точке (для сценариев)
            cursor.execute(f'''
                SELECT Time_L, Insra
                FROM schedule
                WHERE {role} = ? AND Point = ?
                ORDER BY Time_L
            ''', (name, point))
            lessons_for_role = cursor.fetchall()
            all_times = [row[0] for row in lessons_for_role]
            times_str = ", ".join(all_times)

            # Формируем блок сценариев
            scenario_block = ""
            scenario_lines = []
            for t, insra in lessons_for_role:
                if insra and insra.strip():
                    scenario_lines.append(f"<b>{t}</b>: <a href=\"{insra}\">сценарий</a>")
            if scenario_lines:
                scenario_block = "\nНе забудьте до занятия прочитать сценарий:\n" + "\n".join(scenario_lines)

            # Отправка уведомления
            cursor.execute("SELECT telegram_id FROM users WHERE name = ?", (name,))
            if user := cursor.fetchone():

                # Добавляем кнопки
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Подтвердить",
                            callback_data=f"upcoming_confirm_{user[0]}_{rowid}"
                        ),
                        InlineKeyboardButton(
                            text="Отказаться",
                            callback_data=f"upcoming_cancel_{user[0]}_{rowid}"
                        )
                    ]
                ])

                await bot.send_message(
                    chat_id=user[0],
                    text=f"У вас через час уроки в садике {point}\nАдрес: {address}\nВремя: {times_str}{scenario_block}",
                    reply_markup=keyboard,
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )

                # Обновление статуса для первого урока
                cursor.execute(f"""
                    UPDATE schedule
                    SET {status_column} = 'wait'
                    WHERE rowid = (
                        SELECT rowid
                        FROM schedule
                        WHERE {role} = ? AND Point = ?
                        ORDER BY Time_L ASC
                        LIMIT 1
                    )
                """, (name, point))
                
                # Обновление статуса foto для всех уроков преподавателя в этом детском саду
                cursor.execute(f"""
                    UPDATE schedule
                    SET foto = 'wait'
                    WHERE {role} = ? AND Point = ?
                """, (name, point))
                
                conn.commit()

    conn.close()

# Функция для получения информации из таблицы users
def get_help_info():
    try:
        # Подключаемся к базе данных
        conn = get_db_connection()
        cursor = conn.cursor()

        # Выполняем SQL-запрос (пример: получаем данные из таблицы users)
        cursor.execute("""
            SELECT telegram_id, name, status, work, nik_name FROM users""")
        result = cursor.fetchall()

        # Закрываем соединение
        conn.close()

        # Формируем строку с информацией
        if result:
            return "\n".join([
                f"Telegram ID: {row[0]}, Имя: {row[1]}, Ник: {row[4]}, Роль: {row[2]}, Статус: {row[3]}"
                for row in result
            ])
        else:
            return "Информация о пользователях отсутствует."
    except Exception as e:
        return f"Ошибка при получении данных: {e}"

# ============================================================================
# АДМИНСКИЕ ФУНКЦИИ
# ============================================================================

# Обработчик команды /help
@dp.message(Command("help"))
async def send_help(message: Message):
    # Проверяем, является ли пользователь администратором
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE telegram_id = ?", (message.from_user.id,))
    user = cursor.fetchone()
    conn.close()

    if user and user[0] in ('Admin', 'DoubleA'):
        help_info = get_help_info()
        await message.answer(help_info)
    else:
        await message.answer("У вас нет прав для выполнения этой команды")

# Функция для получения информации из таблицы schedule
def get_schedule_info():
    try:
        # Подключаемся к базе данных
        conn = get_db_connection()
        cursor = conn.cursor()

        # Выполняем SQL-запрос (получаем данные из таблицы schedule)
        cursor.execute("""
            SELECT DateLL, Time_L, Teacher, Theme, Teacher_w, Counter_p, foto
            FROM schedule
        """)
        result = cursor.fetchall()

        # Закрываем соединение
        conn.close()

        # Формируем строку с информацией
        if result:
            return "\n\n".join([
                f"Дата: {row[0]}\n"
                f"Время: {row[1]}\n"
                f"Преподаватель: {row[2]}\n"
                f"Тема: {row[3]}\n"
                f"Статус: {row[4]}\n"
                f"Фото: {row[6]}\n"
                for row in result
            ])
        else:
            return "Информация о расписании отсутствует."
    except Exception as e:
        return f"Ошибка при получении данных: {e}"


@dp.message(Command("info"))
async def info_command(message: Message):
    await send_info_report()
    await message.answer("Информация отправлена администраторам.")



# Обработчик команды /helps
@dp.message(Command("helps"))
async def send_schedule(message: Message):
    # Проверяем, является ли пользователь администратором
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE telegram_id = ?", (message.from_user.id,))
    user = cursor.fetchone()
    conn.close()

    if user and user[0] in ('Admin', 'DoubleA'):
        schedule_info = get_schedule_info()
        await message.answer(schedule_info)
    else:
        await message.answer("У вас нет прав для выполнения этой команды")

@dp.message(Command(commands=["renamesss"]))
async def renamesss_command(message: Message):
    url = WEBHOOK_USERS_URL
    conn = None
    try:
        # Выполняем POST-запрос
        response = requests.post(url)
        if response.status_code != 200:
            await message.answer(f"Ошибка запроса: {response.status_code}")
            return

        data = response.json()

        # Подключаемся к базе
        conn = get_db_connection()
        cursor = conn.cursor()

        # Очищаем таблицу users
        cursor.execute("DELETE FROM users")

        # Вставляем новые записи y
        for item in data:
            telegram_id = item.get("telega")
            name = item.get("name")
            nik_name = item.get("nameT")
            status = item.get("Role")
            work = item.get("work")

            cursor.execute(
                "INSERT INTO users (telegram_id, name, nik_name, status, work) VALUES (?, ?, ?, ?, ?)",
                (telegram_id, name, nik_name, status, work)
            )

        conn.commit()
        await message.answer("Таблица users успешно обновлена.")

    except Exception as e:
        await message.answer(f"Произошла ошибка: {e}")

    finally:
        conn.close()


@dp.message(Command("retable"))
async def handle_retable(message: Message):
    user_id = message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE telegram_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()

    if user and user[0] in ('Admin', 'DoubleA'):
        # Кнопки выбора дня
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Сегодня", callback_data="retable_today"),
                InlineKeyboardButton(text="Завтра", callback_data="retable_tomorrow")
            ]
        ])
        await message.answer("На какой день обновить занятия?", reply_markup=keyboard)
    else:
        await message.answer("У вас нет прав для выполнения этой команды.")

@dp.callback_query(lambda c: c.data in ["retable_today", "retable_tomorrow"])
async def handle_retable_choice(callback: CallbackQuery):
    user_id = callback.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE telegram_id = ?", (user_id,))
    user = cursor.fetchone()
    if not (user and user[0] in ('Admin', 'DoubleA')):
        await callback.answer("Нет прав", show_alert=True)
        conn.close()
        return
    conn.close()

    # Выбор вебхука
    if callback.data == "retable_today":
        url = NEW_WEBHOOK_URL
        day_text = "сегодня"
    else:
        url = WEBHOOK_URL
        day_text = "завтра"

    try:
        response = requests.post(url)
        if response.status_code != 200:
            await callback.message.answer(f"Ошибка при запросе: {response.status_code}")
            return
        new_data = response.json()
    except Exception as e:
        await callback.message.answer(f"Ошибка при получении данных: {e}")
        return

    # Получаем старое расписание
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT Date_L, Time_L, Teacher FROM schedule")
    old_rows = set(cursor.fetchall())

    # Для поиска новых и существующих строк
    new_rows = set()
    notify_teachers = []
    for item in new_data:
        key = (item.get("Date_L", ""), item.get("Time_L", ""), item.get("Teacher", ""))
        new_rows.add(key)
        if key not in old_rows:
            # Добавляем новую строку
            cursor.execute("""
                INSERT INTO schedule (
                    Date_L, Time_L, Point, Groupp, Teacher, Assist, 
                    Adress, Modul, Theme, DateLL, Teacher_w, Assist_w, Counter_p,
                    Comment, Present, Detail, Insra
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item.get("Date_L", ""),
                item.get("Time_L", ""),
                item.get("Point", ""),
                item.get("Groupp", ""),
                item.get("Teacher", ""),
                item.get("Assist", ""),
                item.get("Adress", ""),
                item.get("Modul", ""),
                item.get("Theme", ""),
                item.get("DateLL", ""),
                "",  # Teacher_w - пустая строка
                "",  # Assist_w - пустая строка
                item.get("Counter_p", ""),
                item.get("Comment", ""),
                item.get("Present", ""),
                item.get("Detail", ""),
                item.get("Insra", "")
            ))
            notify_teachers.append((item.get("Teacher", ""), item))
            
            # Обновляем статус учителя в таблице users
            teacher_name = item.get("Teacher", "")
            if teacher_name:
                cursor.execute("SELECT id FROM users WHERE name = ?", (teacher_name,))
                teacher_user = cursor.fetchone()
                if teacher_user:
                    cursor.execute("UPDATE users SET work = 'wait' WHERE name = ?", (teacher_name,))
    # Удаляем строки, которых нет в новом расписании
    to_delete = old_rows - new_rows
    for row in to_delete:
        cursor.execute("DELETE FROM schedule WHERE Date_L = ? AND Time_L = ? AND Teacher = ?", row)
    conn.commit()

    # Уведомляем преподавателей о новых занятиях (аналогично process_schedule_and_notify, но только для новых)
    # Группируем новые занятия по преподавателю
    teacher_lessons = {}
    for teacher, item in notify_teachers:
        if not teacher:
            continue
        teacher_lessons.setdefault(teacher, []).append(item)

    for teacher, lessons in teacher_lessons.items():
        cursor.execute("SELECT telegram_id FROM users WHERE name = ?", (teacher,))
        user_row = cursor.fetchone()
        if not user_row:
            continue
        telegram_id = user_row[0]
        msg = "Вам добавлены новые занятия:\n"
        for item in lessons:
            msg += f"\nДата: {item.get('DateLL','')}\nВремя: {item.get('Time_L','')}\nСадик: {item.get('Point','')}\nАдрес: {item.get('Adress','')}\n"
            if item.get('Insra'):
                msg += f"Сценарий: <a href=\"{item.get('Insra')}\">страница</a>\n"
            if item.get('Detail'):
                msg += f"Детали: <a href=\"{item.get('Detail')}\">страница</a>\n"
            if item.get('Present'):
                msg += f"Презентация: <a href=\"{item.get('Present')}\">страница</a>\n"
            msg += f"Тема: {item.get('Modul','')}, {item.get('Theme','')}\n"
            if item.get('Comment') and item.get('Comment').strip():
                msg += f"Комментарии: {item.get('Comment').strip()}\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить", callback_data="confirm_lesson"),
                InlineKeyboardButton(text="Отказаться", callback_data="cancel_lesson")
            ]
        ])
        try:
            await bot.send_message(chat_id=telegram_id, text=msg, parse_mode='HTML', reply_markup=keyboard, disable_web_page_preview=True)
        except Exception as e:
            print(f"Ошибка отправки уведомления преподавателю: {e}")
    added_count = len(notify_teachers)
    if added_count == 1:
        added_text = "Добавлен 1 урок."
    elif 2 <= added_count <= 4:
        added_text = f"Добавлено {added_count} урока."
    else:
        added_text = f"Добавлено {added_count} уроков."
    await callback.message.answer(f"Таблица расписания обновлена на {day_text}!\n{added_text}")
    conn.close()

#Получаем значение свободной колонки для записи посещаемости.
def update_column_table():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Создаём таблицу column, если её нет
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS column (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            column_d TEXT
        )
    """)

    # Отправляем POST-запрос
    url = WEBHOOK_COLUMN_URL
    response = requests.post(url, timeout=30)

    # Проверяем успешность запроса
    if response.status_code != 200:
        print(f"Ошибка при выполнении запроса: статус {response.status_code}")
        return

    # Получаем текстовый ответ вместо JSON
    body_value = response.text.strip()  # Удаляем лишние пробелы

    # Дополнительная очистка если нужно
    if body_value.startswith('"') and body_value.endswith('"'):
        body_value = body_value[1:-1]

    if not body_value:
        print("Пустой ответ от сервера")
        return

    print(f"Получено значение: '{body_value}'")

    # Проверяем существование записи
    cursor.execute("SELECT id FROM column")
    existing_record = cursor.fetchone()

    if existing_record:
        # Обновляем существующую запись
        cursor.execute("UPDATE column SET column_d = ? WHERE id = ?",
                       (body_value, existing_record[0]))
    else:
        # Создаем новую запись
        cursor.execute("INSERT INTO column (column_d) VALUES (?)", (body_value,))

    conn.commit()
    print(f"Таблица column успешно обновлена значением: '{body_value}'")

    conn.close()

@dp.message(Command("add_counter_column"))
async def add_counter_column(message: Message):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Проверяем существование колонки
    cursor.execute("PRAGMA table_info(schedule)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'Counter_p' in columns:
        await message.answer("Колонка Counter_p уже существует в таблице schedule")
        return

    # Добавляем новую колонку
    cursor.execute("ALTER TABLE schedule ADD COLUMN Counter_p TEXT")
    conn.commit()

    await message.answer("✅ Колонка Counter_p успешно добавлена в таблицу schedule")

    conn.close()


async def check_lessons_10min_before():
    conn = get_db_connection()
    cursor = conn.cursor()

    kaz_time = datetime.now(timezone("Asia/Ho_Chi_Minh"))
    lesson_time = (kaz_time + timedelta(minutes=10)).strftime("%H:%M")
    print(f"[DEBUG] Проверка уроков в {lesson_time}")
    print(f"[DEBUG] Текущее время: {kaz_time.strftime('%H:%M')}")

    # Получаем значение из таблицы column
    cursor.execute("SELECT column_d FROM column LIMIT 1")
    row = cursor.fetchone()
    column_d_value = row[0] if row else ""
    print(f"[DEBUG] Column_d value: '{column_d_value}'")

    # Ищем подходящие уроки
    cursor.execute("""
        SELECT rowid, Point, Groupp, Teacher, Counter_p, Time_L
        FROM schedule 
        WHERE Time_L = ? 
    """, (lesson_time,))

    lessons = cursor.fetchall()
    print(f"[DEBUG] Найдено уроков: {len(lessons)}")

    if not lessons:
        print("[DEBUG] Уроки не найдены")
        return

    for lesson in lessons:
        rowid, point, groupp, teacher, counter_p, time_l = lesson
        print(f"[DEBUG] Обработка урока #{rowid}:")
        print(f"  Point: {point}, Groupp: {groupp}, Teacher: {teacher}")
        print(f"  Counter_p: '{counter_p}'")
        print(f"  Time_L: '{time_l}'")

        # Проверяем статус "не вносить"
        if counter_p and "не вносить" in counter_p.lower():
            print("  [SPECIAL] Запрос количества учеников у преподавателя - статус 'не вносить'")
            cursor.execute("SELECT telegram_id FROM users WHERE name = ?", (teacher,))
            teacher_data = cursor.fetchone()
            if not teacher_data:
                print(f"  [SKIP] Учитель '{teacher}' не найден в системе")
                continue
            teacher_id = teacher_data[0]
            # Формируем callback_data с rowid урока
            callback_data = f"enter_count:{rowid}"
            kb = InlineKeyboardBuilder()
            kb.button(text="Ввести количество", callback_data=callback_data)
            kb.adjust(1)
            await bot.send_message(
                teacher_id,
                f"Пожалуйста, нажмите кнопку и введите количество учеников на уроке садик {point}, группа {groupp}:",
                reply_markup=kb.as_markup()
            )
            continue

        # Проверяем наличие учителя в системе
        cursor.execute("SELECT telegram_id FROM users WHERE name = ?", (teacher,))
        teacher_data = cursor.fetchone()

        if not teacher_data:
            print(f"  [SKIP] Учитель '{teacher}' не найден в системе")
            continue

        teacher_id = teacher_data[0]
        print(f"  Учитель найден, Telegram ID: {teacher_id}")

        # Очищаем старые данные для этой группы и точки
        cursor.execute("""
            DELETE FROM lessons 
            WHERE point = ? AND groupp = ? AND free = ?
        """, (point, groupp, time_l))
        print(f"  [DEBUG] Удалено старых записей: {cursor.rowcount}")
        print(f"  [DEBUG] time_l из schedule: '{time_l}' (длина: {len(time_l)}, repr: {repr(time_l)})")
        conn.commit()

        # Отправляем POST-запрос для получения учеников
        url = WEBHOOK_STUDENTS_URL
        payload = {"Point": point, "Groupp": groupp}
        print(f"  Отправка запроса учеников: {payload}")

        try:
            response = requests.post(url, json=payload)
            print(f"  Статус ответа: {response.status_code}")

            if response.status_code != 200:
                print(f"  [ERROR] Ошибка при запросе учеников: {response.status_code}")
                print(f"  Содержимое ответа: {response.text[:200]}")
                continue

            # Безопасное получение JSON
            try:
                students = response.json()
                print(f"  Получено учеников: {len(students)}")
                
                # Проверяем, не пустой ли ответ (садик не найден)
                if students and len(students) == 1:
                    lesson = students[0]
                    if (lesson.get("point") is None and 
                        lesson.get("groupp") is None and 
                        lesson.get("name_s") is None and 
                        lesson.get("idrow") is None):
                        # Садик не найден - отправляем уведомление админам и DoubleA
                        print(f"  [WARNING] Садик {point} не найден в системе")
                        cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
                        admins = cursor.fetchall()
                        admin_message = f"Садик {point} не найден"
                        for admin in admins:
                            await bot.send_message(chat_id=admin[0], text=admin_message)
                        continue  # Пропускаем этот урок
                        
            except Exception as e:
                print(f"  [ERROR] Ошибка парсинга JSON: {e}")
                print(f"  Содержимое ответа: {response.text[:200]}")
                students = []



            # Создаем/проверяем таблицу lessons
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    point TEXT,
                    groupp TEXT,
                    name_s TEXT,
                    student_rowid TEXT,
                    column_d TEXT,
                    present TEXT DEFAULT '',
                    free TEXT DEFAULT ''
                )
            """)

            # Генерируем уникальный код для урока
            lesson_code = generate_lesson_code()
            print(f"  [DEBUG] Сгенерирован lesson_code: {lesson_code}")
            
            # Добавляем учеников
            added_count = 0
            for student in students:
                point_val = student.get("point", "")
                groupp_val = student.get("groupp", "")
                name_s_val = student.get("name_s", "")
                idrow_val = student.get("idrow", "")

                try:
                    cursor.execute("""
                        INSERT INTO lessons (point, groupp, name_s, student_rowid, column_d, free, lesson_code)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (point_val, groupp_val, name_s_val, idrow_val, column_d_value, time_l, lesson_code))
                    added_count += 1
                except Exception as e:
                    print(f"  [ERROR] Ошибка добавления ученика: {e}")

            conn.commit()
            print(f"  Добавлено учеников в базу: {added_count}")

            # Отправляем сообщение преподавателю только если есть ученики
            if students:
                print("  Отправка сообщения преподавателю...")
                print(f"  [DEBUG] time_l: '{time_l}' (длина: {len(time_l)}, repr: {repr(time_l)})")
                await create_primary_keyboard(teacher_id, point, groupp, time_l, lesson_code=lesson_code)
            else:
                print("  Нет учеников для отправки сообщения")

        except Exception as e:
            print(f"  [ERROR] Ошибка при обработке урока: {e}")
            import traceback
            traceback.print_exc()

    conn.close()
    print("[DEBUG] Проверка завершена\n")


# ============================================================================
# ОБРАБОТЧИКИ УЧЕНИКОВ
# ============================================================================

class NewStudent(StatesGroup):
    waiting_for_name = State()
    waiting_for_type = State()


@dp.callback_query(lambda c: c.data.startswith('add_primary_student:'))
async def add_primary_student_handler(callback: CallbackQuery, state: FSMContext):
    try:
        # Безопасный разбор данных
        parts = callback.data.split(':')
        
        # Проверяем новый формат: add_primary_student:lesson_code
        if len(parts) == 2:
            lesson_code = parts[1]
            
            # Получаем параметры урока по коду
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                await callback.answer("Урок не найден")
                return
                
            print(f"[DEBUG] Новый формат добавления ученика (первичная): lesson_code={lesson_code}")
            print(f"[DEBUG] Параметры урока: point={point}, groupp={groupp}, free={free}")
        else:
            # Старый формат: add_primary_student:point:groupp:free
            print(f"[DEBUG] Старый формат добавления ученика (первичная): {parts}")
            
            if len(parts) < 4:
                print(f"[ERROR] Неправильный формат callback_data: {callback.data}")
                await callback.answer("Ошибка в данных урока")
                return
            
            # Простой разбор по аналогии с работающим кодом пагинации
            point = parts[1].replace('_', ' ')  # Восстанавливаем пробелы
            groupp = parts[2]                    # Без изменений
            # Free - это все элементы после groupp
            free_parts = parts[3:]               # Берем все части после groupp
            free = ':'.join(free_parts)          # Собираем время обратно
        
        print(f"[DEBUG] Обработка добавления ученика (первичная):")
        print(f"  Point: {point}")
        print(f"  Groupp: {groupp}")
        print(f"  Free: {free}")
        print(f"  Message ID: {callback.message.message_id}")
        
        # Сохраняем данные в состоянии
        await state.update_data(
            point=point,
            groupp=groupp,
            free=free,
            teacher_id=callback.from_user.id,
            message_id=callback.message.message_id,
            is_primary_mode=True
        )
        print(f"[DEBUG] Данные сохранены в состоянии")
        
        # Устанавливаем состояние ожидания имени
        await state.set_state(NewStudent.waiting_for_name)
        print(f"[DEBUG] Состояние установлено на waiting_for_name")
        
        # Отправляем сообщение с запросом имени
        await callback.message.answer("Введите имя нового ученика:")
        await callback.answer()
        
    except Exception as e:
        print(f"[ERROR] Ошибка в add_primary_student_handler: {e}")
        import traceback
        traceback.print_exc()
        await callback.answer(f"Ошибка: {e}")

@dp.callback_query(lambda c: c.data.startswith('add_edit_student:'))
async def add_edit_student_handler(callback: CallbackQuery, state: FSMContext):
    try:
        # Безопасный разбор данных
        parts = callback.data.split(':')
        
        # Проверяем новый формат: add_edit_student:lesson_code
        if len(parts) == 2:
            lesson_code = parts[1]
            
            # Получаем параметры урока по коду
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                await callback.answer("Урок не найден")
                return
                
            print(f"[DEBUG] Новый формат добавления ученика (повторная): lesson_code={lesson_code}")
            print(f"[DEBUG] Параметры урока: point={point}, groupp={groupp}, free={free}")
        else:
            # Старый формат: add_edit_student:point:groupp:free
            print(f"[DEBUG] Старый формат добавления ученика (повторная): {parts}")
            
            if len(parts) < 4:
                print(f"[ERROR] Неправильный формат callback_data: {callback.data}")
                await callback.answer("Ошибка в данных урока")
                return
            
            # Простой разбор по аналогии с работающим кодом пагинации
            point = parts[1].replace('_', ' ')  # Восстанавливаем пробелы
            groupp = parts[2]                    # Без изменений
            # Free - это все элементы после groupp
            free_parts = parts[3:]               # Берем все части после groupp
            free = ':'.join(free_parts)          # Собираем время обратно
        
        print(f"[DEBUG] Обработка добавления ученика (повторная):")
        print(f"  Point: {point}")
        print(f"  Groupp: {groupp}")
        print(f"  Free: {free}")
        print(f"  Message ID: {callback.message.message_id}")
        
        # Сохраняем данные в состоянии
        await state.update_data(
            point=point,
            groupp=groupp,
            free=free,
            teacher_id=callback.from_user.id,
            message_id=callback.message.message_id,
            is_primary_mode=False
        )
        print(f"[DEBUG] Данные сохранены в состоянии")
        
        # Устанавливаем состояние ожидания имени
        await state.set_state(NewStudent.waiting_for_name)
        print(f"[DEBUG] Состояние установлено на waiting_for_name")
        
        # Отправляем сообщение с запросом имени
        await callback.message.answer("Введите имя нового ученика:")
        await callback.answer()
        
    except Exception as e:
        print(f"[ERROR] Ошибка в add_edit_student_handler: {e}")
        import traceback
        traceback.print_exc()
        await callback.answer(f"Ошибка: {e}")

@dp.callback_query(lambda c: c.data.startswith('add_student:'))
async def add_student_handler(callback: CallbackQuery, state: FSMContext):
    try:
        # Безопасный разбор данных
        parts = callback.data.split(':')
        
        # Проверяем новый формат: add_student:lesson_code
        if len(parts) == 2:
            lesson_code = parts[1]
            
            # Получаем параметры урока по коду
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                await callback.answer("Ошибка: урок не найден")
                return
                
            print(f"[DEBUG] Новый формат добавления ученика: lesson_code={lesson_code}")
            print(f"[DEBUG] Параметры урока: point={point}, groupp={groupp}, free={free}")
        else:
            # Старый формат: add_student:point:groupp:free
            print(f"[DEBUG] Старый формат добавления ученика: {parts}")
            
            if len(parts) < 4:
                await callback.answer("Ошибка: неверный формат данных")
                return

            # Извлекаем параметры из частей
            point = parts[1].replace('_', ' ')
            groupp = parts[2]
            # Объединяем все оставшиеся части как free, так как время может содержать двоеточие
            free = ':'.join(parts[3:])

        # Сохраняем message_id текущего сообщения
        message_id = callback.message.message_id

        print(f"[DEBUG] Обработка добавления ученика:")
        print(f"  Point: {point}")
        print(f"  Groupp: {groupp}")
        print(f"  Free: {free}")
        print(f"  Message ID: {message_id}")

        await state.update_data(
            point=point,
            groupp=groupp,
            free=free,
            message_id=message_id,
            teacher_id=callback.from_user.id
        )
        print("[DEBUG] Данные сохранены в состоянии")

        await callback.message.answer("Введите имя и фамилию нового ученика:")
        await state.set_state(NewStudent.waiting_for_name)
        print("[DEBUG] Состояние установлено на waiting_for_name")
    except Exception as e:
        print(f"[ERROR] Ошибка в add_student_handler: {e}")
        await callback.answer("Произошла ошибка")
    finally:
        await callback.answer()


@dp.message(StateFilter(NewStudent.waiting_for_name))
async def process_student_name(message: Message, state: FSMContext):
    student_name = message.text
    data = await state.get_data()

    # Получаем все необходимые данные из состояния
    point = data['point']
    groupp = data['groupp']
    free = data['free']  # Время урока из состояния
    teacher_id = data['teacher_id']
    message_id = data['message_id']  # Получаем сохраненный message_id

    print(f"[DEBUG] Добавление нового ученика:")
    print(f"  Имя: {student_name}")
    print(f"  Point: {point}")
    print(f"  Groupp: {groupp}")
    print(f"  Free: {free}")
    print(f"  Teacher ID: {teacher_id}")
    print(f"  Message ID: {message_id}")

    # Сохраняем имя ученика в состоянии
    await state.update_data(student_name=student_name)

    # Определяем режим (первичная или повторная отправка)
    is_primary_mode = data.get('is_primary_mode', True)  # По умолчанию первичная
    
    print(f"[DEBUG] Режим добавления ученика: {'первичная' if is_primary_mode else 'повторная'}")

    # Показываем кнопки выбора типа ученика с правильными callback'ами
    if is_primary_mode:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Разовый", callback_data="primary_student_type_temporary"),
                InlineKeyboardButton(text="Постоянный", callback_data="primary_student_type_permanent")
            ]
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="Разовый", callback_data="edit_student_type_temporary"),
                InlineKeyboardButton(text="Постоянный", callback_data="edit_student_type_permanent")
            ]
        ])
    
    await message.answer(
        f"Выберите тип ученика для {student_name}:",
        reply_markup=keyboard
    )

    # Переходим к состоянию выбора типа
    await state.set_state(NewStudent.waiting_for_type)
    print("[DEBUG] Переход к состоянию waiting_for_type")



async def send_students_list(teacher_id, point, groupp, free, page=0, message_id=None, is_edit_mode=False, lesson_code=None):
    global current_edit_mode
    # Устанавливаем глобальный флаг только если явно передан True
    if is_edit_mode:
        current_edit_mode = True
    # Если is_edit_mode=False, НЕ сбрасываем current_edit_mode
    
    conn = get_db_connection()
    cursor = conn.cursor()
    print(f"[DEBUG] Формирование списка учеников:")
    print(f"  Point: {point}")
    print(f"  Groupp: {groupp}")
    print(f"  Free (время): {free}")
    print(f"  Page: {page}")
    print(f"  Message ID: {message_id}")
    print(f"  Teacher ID: {teacher_id}")
    print(f"  Teacher ID тип: {type(teacher_id)}")
    print(f"  lesson_code: {lesson_code}")
    
    # Проверяем, что teacher_id существует в базе
    cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (teacher_id,))
    teacher_row = cursor.fetchone()
    if teacher_row:
        print(f"  Преподаватель найден в базе: {teacher_row[0]}")
    else:
        print(f"  [WARNING] Преподаватель с ID {teacher_id} не найден в базе!")
    
    # Проверяем, откуда вызвана функция
    import inspect
    stack = inspect.stack()
    caller = stack[1].function if len(stack) > 1 else "unknown"
    print(f"  Вызвана из функции: {caller}")
    
    # Проверяем данные в базе lessons
    cursor.execute("""
        SELECT COUNT(*) FROM lessons 
        WHERE point = ? AND groupp = ? AND free = ?
    """, (point, groupp, free))
    count = cursor.fetchone()[0]
    print(f"  Записей в lessons для этого урока: {count}")
    
    # Получаем всех учеников В ИСХОДНОМ ПОРЯДКЕ (без сортировки)
    cursor.execute("""
        SELECT id, name_s, present 
        FROM lessons 
        WHERE point = ? AND groupp = ? AND free = ?
        ORDER BY id
    """, (point, groupp, free))
    all_students = cursor.fetchall()
    print(f"[DEBUG] Всего учеников: {len(all_students)}")
    print("[DEBUG] Список учеников:")
    for student in all_students:
        print(f"  ID: {student[0]}, Имя: {student[1]}, Присутствие: {student[2]}")

    if not all_students:
        print("[DEBUG] Нет учеников для отображения")
        return

    # Разбиваем на страницы
    total_pages = (len(all_students) + STUDENTS_PER_PAGE - 1) // STUDENTS_PER_PAGE
    start_index = page * STUDENTS_PER_PAGE
    end_index = min(start_index + STUDENTS_PER_PAGE, len(all_students))
    students_page = all_students[start_index:end_index]

    # Считаем присутствующих
    present_count = sum(1 for s in all_students if s[2] == "1")
    total_count = len(all_students)

    # Создаем клавиатуру
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    # Добавляем учеников текущей страницы (сохраняем текущую страницу в callback_data)
    for student in students_page:
        student_id, name_s, present = student
        is_present = present == "1"
        
        callback_data = f"t:{student_id}:{page}"
        print(f"[DEBUG BUTTON] Кнопка ученика '{name_s}':")
        print(f"  - callback_data: '{callback_data}'")
        print(f"  - длина: {len(callback_data)} байт")
        print(f"  - содержит скобки: {('(' in callback_data or ')' in callback_data)}")
        print(f"  - содержит пробелы: {' ' in callback_data}")
        print(f"  - содержит двоеточия: {callback_data.count(':')}")
        
        try:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"✅ {name_s}" if is_present else name_s,
                    callback_data=callback_data  # Добавляем страницу в callback_data
                )
            ])
            print(f"[DEBUG BUTTON] ✓ Кнопка ученика '{name_s}' добавлена успешно")
        except Exception as e:
            print(f"[ERROR BUTTON] ❌ Ошибка при создании кнопки ученика '{name_s}': {e}")
            print(f"[ERROR BUTTON] Проблемный callback_data: '{callback_data}'")

    # Добавляем кнопки навигации
    print(f"[DEBUG] Формирование кнопок пагинации: prev={page > 0}, next={end_index < len(all_students)}")
    navigation_buttons = []

    if page > 0:
        if lesson_code:
            callback_data = f"page:{lesson_code}:prev:{page}"
        else:
            callback_data = f"page:{point}:{groupp}:{free}:prev:{page}"
        print(f"[DEBUG BUTTON] Кнопка 'Назад':")
        print(f"  - callback_data: '{callback_data}'")
        print(f"  - длина: {len(callback_data)} байт")
        print(f"  - содержит скобки: {('(' in callback_data or ')' in callback_data)}")
        print(f"  - содержит пробелы: {' ' in callback_data}")
        print(f"  - содержит двоеточия: {callback_data.count(':')}")
        
        try:
            navigation_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=callback_data
                )
            )
            print(f"[DEBUG BUTTON] ✓ Кнопка 'Назад' создана успешно")
        except Exception as e:
            print(f"[ERROR BUTTON] ❌ Ошибка при создании кнопки 'Назад': {e}")
            print(f"[ERROR BUTTON] Проблемный callback_data: '{callback_data}'")

    if end_index < len(all_students):
        if lesson_code:
            callback_data = f"page:{lesson_code}:next:{page}"
        else:
            callback_data = f"page:{point}:{groupp}:{free}:next:{page}"
        print(f"[DEBUG BUTTON] Кнопка 'Вперед':")
        print(f"  - callback_data: '{callback_data}'")
        print(f"  - длина: {len(callback_data)} байт")
        print(f"  - содержит скобки: {('(' in callback_data or ')' in callback_data)}")
        print(f"  - содержит пробелы: {' ' in callback_data}")
        print(f"  - содержит двоеточия: {callback_data.count(':')}")
        
        try:
            navigation_buttons.append(
                InlineKeyboardButton(
                    text="Вперед ➡️",
                    callback_data=callback_data
                )
            )
            print(f"[DEBUG BUTTON] ✓ Кнопка 'Вперед' создана успешно")
        except Exception as e:
            print(f"[ERROR BUTTON] ❌ Ошибка при создании кнопки 'Вперед': {e}")
            print(f"[ERROR BUTTON] Проблемный callback_data: '{callback_data}'")

    if navigation_buttons:
        keyboard.inline_keyboard.append(navigation_buttons)

    # Кнопка добавления нового ученика
    if lesson_code:
        add_callback = f"add_primary_student:{lesson_code}"
    else:
        add_callback = f"add_primary_student:{point}:{groupp}:{free}"
    print(f"[DEBUG BUTTON] Кнопка 'Добавить ученика':")
    print(f"  - callback_data: '{add_callback}'")
    print(f"  - длина: {len(add_callback)} байт")
    print(f"  - содержит скобки: {('(' in add_callback or ')' in add_callback)}")
    print(f"  - содержит пробелы: {' ' in add_callback}")
    print(f"  - содержит двоеточия: {add_callback.count(':')}")
    
    try:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(
                text="➕ Добавить ученика",
                callback_data=add_callback
            )
        ])
        print(f"[DEBUG BUTTON] ✓ Кнопка 'Добавить ученика' создана успешно")
    except Exception as e:
        print(f"[ERROR BUTTON] ❌ Ошибка при создании кнопки 'Добавить ученика': {e}")
        print(f"[ERROR BUTTON] Проблемный callback_data: '{add_callback}'")

    # Кнопка отправки данных
    if message_id is None:
        # Создание нового сообщения - создаем кнопку "Отправить данные"
        if lesson_code:
            if is_edit_mode:
                send_data_callback = f"send_edit_data:{lesson_code}"  # Для редактирования
            else:
                send_data_callback = f"send_data:{lesson_code}"       # Для первичной отправки
        else:
            send_data_callback = f"send_data:{point}:{groupp}:{free}"
        print(f"[DEBUG BUTTON] Кнопка 'Отправить данные' (новое сообщение):")
        print(f"  - callback_data: '{send_data_callback}'")
        print(f"  - длина: {len(send_data_callback)} байт")
        
        try:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"Отправить данные ({present_count}/{total_count})",
                    callback_data=send_data_callback
                )
            ])
            print(f"[DEBUG BUTTON] ✓ Кнопка 'Отправить данные' создана успешно")
        except Exception as e:
            print(f"[ERROR BUTTON] ❌ Ошибка при создании кнопки 'Отправить данные': {e}")
            print(f"[ERROR BUTTON] Проблемный callback_data: '{send_data_callback}'")
    else:
        # Обновление существующего сообщения - сохраняем существующую кнопку "Отправить данные"
        try:
            # Получаем текущее сообщение
            current_message = await bot.get_chat(teacher_id, message_id)
            existing_send_button = None
            
            if hasattr(current_message, 'reply_markup') and current_message.reply_markup:
                # Ищем кнопку "Отправить данные" в существующей клавиатуре
                for row in current_message.reply_markup.inline_keyboard:
                    for button in row:
                        if button.text and "Отправить данные" in button.text:
                            existing_send_button = button
                            break
            
            if existing_send_button:
                # Сохраняем существующую кнопку "Отправить данные"
                keyboard.inline_keyboard.append([existing_send_button])
                print(f"[DEBUG BUTTON] Сохранена существующая кнопка 'Отправить данные': {existing_send_button.callback_data}")
            else:
                # Fallback - создаем новую кнопку
                if lesson_code:
                    if is_edit_mode:
                        send_data_callback = f"send_edit_data:{lesson_code}"
                    else:
                        send_data_callback = f"send_data:{lesson_code}"
                else:
                    send_data_callback = f"send_data:{point}:{groupp}:{free}"
                
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(
                        text=f"Отправить данные ({present_count}/{total_count})",
                        callback_data=send_data_callback
                    )
                ])
                print(f"[DEBUG BUTTON] Создана новая кнопка 'Отправить данные' как fallback")
                
        except Exception as e:
            print(f"[ERROR BUTTON] Ошибка при получении существующей кнопки: {e}")
            # Fallback - создаем новую кнопку
            if lesson_code:
                if is_edit_mode:
                    send_data_callback = f"send_edit_data:{lesson_code}"
                else:
                    send_data_callback = f"send_data:{lesson_code}"
            else:
                send_data_callback = f"send_data:{point}:{groupp}:{free}"
            
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"Отправить данные ({present_count}/{total_count})",
                    callback_data=send_data_callback
                )
            ])
            print(f"[DEBUG BUTTON] Создана новая кнопка 'Отправить данные' как fallback")

    # Текст сообщения с информацией о странице
    page_info = f" (Страница {page + 1}/{total_pages})" if total_pages > 1 else ""
    message_text = f"Отметьте присутствующих учеников ({groupp}, {point}){page_info}:"

    print(f"[DEBUG] Попытка отправить сообщение:")
    print(f"  Chat ID: {teacher_id}")
    print(f"  Message ID: {message_id}")
    print(f"  Text: {message_text}")
    print(f"  Keyboard rows: {len(keyboard.inline_keyboard)}")
    
    # Анализируем всю клавиатуру перед отправкой
    print(f"[DEBUG BUTTON CHECK] Анализ клавиатуры:")
    print(f"  - Всего строк: {len(keyboard.inline_keyboard)}")
    for i, row in enumerate(keyboard.inline_keyboard):
        print(f"  - Строка {i}: {len(row)} кнопок")
        for j, button in enumerate(row):
            print(f"    - Кнопка {j}: text='{button.text}', callback_data='{button.callback_data}' (длина: {len(button.callback_data)})")

    # Если message_id передан - редактируем существующее сообщение
    if message_id:
        print(f"[DEBUG] Редактирование сообщения {message_id}")
        try:
            await bot.edit_message_text(
                chat_id=teacher_id,
                message_id=message_id,
                text=message_text,
                reply_markup=keyboard
            )
            print(f"[DEBUG BUTTON CHECK] ✓ Сообщение {message_id} отредактировано успешно")
        except Exception as e:
            print(f"[ERROR BUTTON CHECK] ❌ Ошибка при редактировании сообщения: {e}")
            print(f"[ERROR BUTTON CHECK] Проблемная клавиатура: {keyboard}")
            print(f"[ERROR BUTTON CHECK] Детали клавиатуры:")
            print(f"  - inline_keyboard={keyboard.inline_keyboard}")
            for i, row in enumerate(keyboard.inline_keyboard):
                print(f"  - Строка {i}: {len(row)} кнопок")
                for j, button in enumerate(row):
                    print(f"    - Кнопка {j}: text='{button.text}', url={button.url}, callback_data='{button.callback_data}', web_app={button.web_app}, login_url={button.login_url}, switch_inline_query={button.switch_inline_query}, switch_inline_query_current_chat={button.switch_inline_query_current_chat}, switch_inline_query_chosen_chat={button.switch_inline_query_chosen_chat}, callback_game={button.callback_game}, pay={button.pay}")
            raise
    else:
        print("[DEBUG] Отправка нового сообщения")
        try:
            message = await bot.send_message(
                chat_id=teacher_id,
                text=message_text,
                reply_markup=keyboard
            )
            print(f"[DEBUG BUTTON CHECK] ✓ Новое сообщение отправлено успешно, ID: {message.message_id}")
            return message.message_id
        except Exception as e:
            print(f"[ERROR BUTTON CHECK] ❌ Ошибка при отправке нового сообщения: {e}")
            print(f"[ERROR BUTTON CHECK] Проблемная клавиатура: {keyboard}")
            print(f"[ERROR BUTTON CHECK] Детали клавиатуры:")
            print(f"  - inline_keyboard={keyboard.inline_keyboard}")
            for i, row in enumerate(keyboard.inline_keyboard):
                print(f"  - Строка {i}: {len(row)} кнопок")
                for j, button in enumerate(row):
                    print(f"    - Кнопка {j}: text='{button.text}', url={button.url}, callback_data='{button.callback_data}', web_app={button.web_app}, login_url={button.login_url}, switch_inline_query={button.switch_inline_query}, switch_inline_query_current_chat={button.switch_inline_query_current_chat}, switch_inline_query_chosen_chat={button.switch_inline_query_chosen_chat}, callback_game={button.callback_game}, pay={button.pay}")
            raise

    conn.close()

# Обработка отметки присутствия с сохранением страницы
@dp.callback_query(lambda c: c.data.startswith('t:'))
async def toggle_presence(callback: CallbackQuery):
    global current_edit_mode
    
    conn = get_db_connection()
    cursor = conn.cursor()

    # Безопасный разбор данных
    parts = callback.data.split(':')
    if len(parts) < 3:
        await callback.answer("Ошибка: неверный формат данных")
        return

    student_id = int(parts[1])
    page = int(parts[2])

    # Получаем данные урока
    cursor.execute("""
        SELECT point, groupp, free FROM lessons WHERE id = ?
    """, (student_id,))
    lesson_data = cursor.fetchone()

    if not lesson_data:
        await callback.answer("Урок не найден!")
        return

    point, groupp, free = lesson_data

    # Получаем lesson_code для этого урока
    lesson_code = None
    try:
        cursor.execute("""
            SELECT lesson_code FROM lessons 
            WHERE point = ? AND groupp = ? AND free = ? 
            LIMIT 1
        """, (point, groupp, free))
        result = cursor.fetchone()
        if result and result[0]:
            lesson_code = result[0]
    except Exception as e:
        pass

    # Переключаем статус присутствия
    cursor.execute("""
        UPDATE lessons
        SET present = CASE WHEN present = '1' THEN '' ELSE '1' END
        WHERE id = ?
    """, (student_id,))
    conn.commit()

    # Обновляем список учеников в зависимости от режима
    if current_edit_mode:
        await create_edit_keyboard(
            callback.from_user.id,
            point,
            groupp,
            free,
            page=page,
            message_id=callback.message.message_id,
            lesson_code=lesson_code
        )
    else:
        await create_primary_keyboard(
            callback.from_user.id,
            point,
            groupp,
            free,
            page=page,
            message_id=callback.message.message_id,
            lesson_code=lesson_code
        )

    conn.close()
    await callback.answer()



# Обработка отправки данных
@dp.callback_query(lambda c: c.data.startswith('send_data:') or c.data.startswith('send_edit_data:'))
async def send_attendance_data(callback: CallbackQuery):
    global current_edit_mode
    
    # Убираем клавиатуру сразу после первого нажатия, чтобы предотвратить повторные отправки
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception as e:
        # Если клавиатура уже убрана или сообщение изменено, игнорируем ошибку
        pass
    conn = get_db_connection()
    cursor = conn.cursor()

    # Безопасный разбор данных
    parts = callback.data.split(':')
    
    # Проверяем новый формат: send_data:lesson_code
    if len(parts) == 2:
        lesson_code = parts[1]
        
        # Получаем параметры урока по коду
        point, groupp, free = get_lesson_by_code(lesson_code)
        if not point:
            await callback.answer("Ошибка: урок не найден")
            return
            
        print(f"[DEBUG] Новый формат отправки данных: lesson_code={lesson_code}")
        print(f"[DEBUG] Параметры урока: point={point}, groupp={groupp}, free={free}")
    else:
        # Старый формат: send_data:point:groupp:free
        print(f"[DEBUG] Старый формат отправки данных: {parts}")
        
        point = parts[1].replace('_', ' ')
        groupp = parts[2]
        # free может содержать двоеточие
        free = ':'.join(parts[3:])
    
    # Определяем режим редактирования по источнику вызова
    # Если это команда /lessons, то это режим редактирования
    if callback.data.startswith('send_data:'):
        is_edit = False  # Первичная отправка
    elif callback.data.startswith('send_edit_data:'):
        is_edit = True   # Редактирование
    else:
        # Обратная совместимость со старым форматом
        is_edit = False
    
    # ДОПОЛНИТЕЛЬНАЯ ОТЛАДКА
    print(f"[DEBUG] === АНАЛИЗ РЕЖИМА РЕДАКТИРОВАНИЯ ===")
    print(f"[DEBUG] callback.data: '{callback.data}'")
    print(f"[DEBUG] callback.data.startswith('send_data:'): {callback.data.startswith('send_data:')}")
    print(f"[DEBUG] len(callback.data.split(':')): {len(callback.data.split(':'))}")
    print(f"[DEBUG] parts: {parts}")
    print(f"[DEBUG] Вычисленный is_edit: {is_edit}")
    print(f"[DEBUG] === КОНЕЦ АНАЛИЗА ===")

    # Логируем полученные параметры для отладки
    print(f"[DEBUG] Отправка данных: point={point}, groupp={groupp}, free={free}, is_edit={is_edit}")

    # Показываем всех учеников для этого урока с их present
    cursor.execute(f"""
        SELECT id, name_s, present FROM lessons 
        WHERE point = ? AND groupp = ? AND free = ?
        ORDER BY id
    """, (point, groupp, free))
    all_students_debug = cursor.fetchall()
    print(f"[DEBUG] Все ученики для этого урока:")
    for s in all_students_debug:
        print(f"  id={s[0]}, name={s[1]}, present={s[2]}")

        # Получаем учеников в зависимости от режима
    if is_edit:
        # При редактировании - всех учеников
        sql_query = f"""
            SELECT point, groupp, name_s, student_rowid, column_d, is_permanent, present 
            FROM lessons 
            WHERE point = '{point}' 
            AND groupp = '{groupp}' 
            AND free = '{free}'
        """
        print(f"[DEBUG] SQL запрос (режим редактирования): {sql_query}")
        cursor.execute(sql_query)
        all_present_students = cursor.fetchall()
        print(f"[DEBUG] Режим редактирования - найдено всех учеников: {len(all_present_students)}")
    else:
        # При первичной отправке - только присутствующих
        sql_query = f"""
            SELECT point, groupp, name_s, student_rowid, column_d, is_permanent, present 
        FROM lessons 
        WHERE point = '{point}' 
        AND groupp = '{groupp}' 
        AND free = '{free}' 
        AND present = '1'
    """
        print(f"[DEBUG] SQL запрос (первичная отправка): {sql_query}")
        cursor.execute(sql_query)
        all_present_students = cursor.fetchall()
        print(f"[DEBUG] Первичная отправка - найдено присутствующих: {len(all_present_students)}")

    # Разделяем на обычных и новых учеников
    regular_students = []
    new_students = []
    
    print(f"[DEBUG] === РАЗДЕЛЕНИЕ УЧЕНИКОВ ===")
    print(f"[DEBUG] Всего учеников для обработки: {len(all_present_students)}")

    for student in all_present_students:
        point_val, groupp_val, name_s, student_rowid, column_d, is_permanent, present = student

        # Логируем данные ученика
        print(f"[DEBUG] Ученик: {name_s}, rowid={student_rowid}, column_d={column_d}, is_permanent={is_permanent}, present={present}")

        # Проверяем является ли ученик "новым"
        if student_rowid is None or student_rowid == '' or column_d is None or column_d == '':
            print(f"[DEBUG] ✓ Новый ученик: {name_s}")
            new_students.append((point_val, groupp_val, name_s, is_permanent))
        else:
            print(f"[DEBUG] ✓ Обычный ученик: {name_s}")
            # Преобразуем present в число (1 или 0)
            present_value = 1 if present == '1' else 0
            regular_students.append((point_val, groupp_val, name_s, column_d, present_value))
    
    print(f"[DEBUG] Итого:")
    print(f"  - Обычных учеников: {len(regular_students)}")
    print(f"  - Новых учеников: {len(new_students)}")
    print(f"[DEBUG] === КОНЕЦ РАЗДЕЛЕНИЯ ===")
    
    # ДОБАВЛЯЮ ДЕБАГ ДЛЯ ПОСТОЯННЫХ УЧЕНИКОВ
    print(f"[DEBUG] === ПРОВЕРКА ПОСТОЯННЫХ УЧЕНИКОВ ===")
    
    # Проверяем всех учеников с их is_permanent
    cursor.execute(f"""
        SELECT id, name_s, is_permanent, present, student_rowid, column_d 
        FROM lessons 
        WHERE point = ? AND groupp = ? AND free = ?
        ORDER BY id
    """, (point, groupp, free))
    
    all_students_check = cursor.fetchall()
    print(f"[DEBUG] Все ученики для проверки:")
    for s in all_students_check:
        print(f"  id={s[0]}, name={s[1]}, is_permanent={s[2]}, present={s[3]}, rowid={s[4]}, column_d={s[5]}")
    
    # Проверяем постоянных учеников отдельно
    cursor.execute(f"""
        SELECT id, name_s, is_permanent, present 
        FROM lessons 
        WHERE point = ? AND groupp = ? AND free = ? AND is_permanent = '1'
        ORDER BY id
    """, (point, groupp, free))
    
    permanent_students = cursor.fetchall()
    print(f"[DEBUG] Постоянных учеников найдено: {len(permanent_students)}")
    for s in permanent_students:
        print(f"  id={s[0]}, name={s[1]}, is_permanent={s[2]}, present={s[3]}")
    
    print(f"[DEBUG] === КОНЕЦ ПРОВЕРКИ ПОСТОЯННЫХ ===")

    # 1. Отправляем обычных учеников
    if regular_students:
        print(f"[DEBUG] Отправка {len(regular_students)} обычных учеников")
        # Получаем имя преподавателя
        cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (callback.from_user.id,))
        teacher_name_row = cursor.fetchone()
        teacher_name = teacher_name_row[0] if teacher_name_row else "Неизвестный"
        
        data_to_send = {
            "data": [
                {
                    "point": student[0],
                    "Groupp": student[1],
                    "name": student[2],
                    "column_d": student[3],
                    "present": student[4],
                    "teacher": teacher_name
                }
                for student in regular_students
            ]
        }
        # --- Новая логика: если это изменение по /lessons (edit_lesson), отправляем на новый хук ---
        if is_edit:
            response = requests.post(
                WEBHOOK_LESSONS_EDIT_URL,
                json=data_to_send,
                timeout=50
            )
        else:
            response = requests.post(
                WEBHOOK_ATTENDANCE_URL,
                json=data_to_send,
                timeout=30
            )
        print(f"[DEBUG] Статус отправки обычных учеников: {response.status_code}")
        # Проверяем на Error в теле ответа
        if response.status_code == 200:
            try:
                resp_json = response.json()
            except Exception:
                resp_json = response.text
            if (isinstance(resp_json, dict) and resp_json.get("Error")) or (isinstance(resp_json, str) and "Error" in resp_json):
                cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
                admins = cursor.fetchall()
                admin_message = f"Преподаватель {teacher_name} в таблице не найден"
                for admin in admins:
                    await bot.send_message(chat_id=admin[0], text=admin_message)

    # 2. Отправляем новых учеников
    if new_students:
        print(f"[DEBUG] Отправка {len(new_students)} новых учеников")
        # Получаем имя преподавателя
        cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (callback.from_user.id,))
        teacher_name_row = cursor.fetchone()
        teacher_name = teacher_name_row[0] if teacher_name_row else "Неизвестный"

        new_data_to_send = {
            "data": [
                {
                    "point": student[0],
                    "Groupp": student[1],
                    "name": student[2],
                    "teacher": teacher_name,
                    "is_permanent": student[3]
                }
                for student in new_students
            ]
        }
        response = requests.post(
            WEBHOOK_NEW_STUDENTS_URL,
            json=new_data_to_send,
            timeout=30
        )
        print(f"[DEBUG] Статус отправки новых учеников: {response.status_code}")

    # 3. Уведомляем админов при первичной отправке, если учеников менее 3
    if not is_edit:  # Только при первичной отправке
        total_students = len(regular_students) + len(new_students)
        print(f"[DEBUG] Общее количество учеников: {total_students} (обычных: {len(regular_students)}, новых: {len(new_students)})")
        
        if total_students < 3:
            # Определяем правильное окончание для числа
            if total_students == 1:
                student_word = "ученик"
            else:
                student_word = "ученика"
            
            admin_message = f"В садике {point}, в группе {groupp}, в {free} - присутствуют {total_students} {student_word}."
            
            # Отправляем сообщение всем админам и DoubleA
            cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
            admins = cursor.fetchall()
            print(f"[DEBUG] Отправка уведомления {len(admins)} админам: {admin_message}")
            
            for admin in admins:
                try:
                    await bot.send_message(chat_id=admin[0], text=admin_message)
                except Exception as e:
                    print(f"[ERROR] Ошибка отправки админу {admin[0]}: {e}")

    # 4. Проверяем новых учеников и отправляем админам для верификации
    print(f"[DEBUG] === НАЧАЛО ВЕРИФИКАЦИИ АДМИНАМИ ===")
    print(f"[DEBUG] is_edit = {is_edit}")
    print(f"[DEBUG] new_students = {new_students}")
    print(f"[DEBUG] len(new_students) = {len(new_students) if new_students else 0}")
    
    if not is_edit and new_students:  # Только при первичной отправке и если есть новые ученики
        print(f"[DEBUG] ✓ Условие выполнено: не редактирование И есть новые ученики")
        print(f"[DEBUG] Проверяем новых учеников для верификации админами")
        
        # Получаем всех админов и DoubleA
        cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
        admins = cursor.fetchall()
        print(f"[DEBUG] Найдено админов: {len(admins)}")
        print(f"[DEBUG] ID админов: {[admin[0] for admin in admins]}")
        
        if admins:
            print(f"[DEBUG] ✓ Админы найдены, создаем клавиатуру")
            # Создаем клавиатуру с новыми учениками
            # Используем простые callback_data по аналогии с существующим кодом
            keyboard_buttons = []
            
            # Создаем callback_data по аналогии с работающим кодом пагинации
            # Простая структура: admin_verify:point:groupp:free:student_index
            print(f"[DEBUG] Создаем callback_data по аналогии с пагинацией")
            print(f"[DEBUG] Исходные данные: point='{point}', groupp='{groupp}', free='{free}'")
            
            # Сохраняем данные для обработчика
            print(f"[DEBUG] Сохраняем данные для обработчика:")
            print(f"[DEBUG] - point: '{point}'")
            print(f"[DEBUG] - groupp: '{groupp}'")
            print(f"[DEBUG] - free: '{free}'")
            print(f"[DEBUG] - new_students: {new_students}")
            
            # Получаем lesson_code для этого урока
            lesson_code = None
            try:
                cursor.execute("""
                    SELECT lesson_code FROM lessons 
                    WHERE point = ? AND groupp = ? AND free = ? 
                    LIMIT 1
                """, (point, groupp, free))
                result = cursor.fetchone()
                if result and result[0]:
                    lesson_code = result[0]
                    print(f"[DEBUG] Найден lesson_code: {lesson_code}")
                else:
                    print(f"[DEBUG] lesson_code не найден, используем старый формат")
            except Exception as e:
                print(f"[DEBUG] Ошибка при поиске lesson_code: {e}, используем старый формат")
            
            for i, student in enumerate(new_students):
                point_val, groupp_val, name_s, is_permanent = student
                print(f"[DEBUG] Обрабатываем ученика {i}: {name_s} (is_permanent={is_permanent})")
                # Создаем кнопку с именем ученика и его текущим статусом
                button_text = f"{'✅' if is_permanent == 1 else '❌'} {name_s}"
                
                # Используем lesson_code если доступен, иначе старый формат
                if lesson_code:
                    callback_data = f"admin_verify:{lesson_code}:{i}"
                else:
                    callback_data = f"admin_verify:{point}:{groupp}:{free}:{i}"
                print(f"[DEBUG] Создана кнопка: '{button_text}' -> '{callback_data}'")
                keyboard_buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
            
            # Добавляем кнопку "Отправить учеников"
            # Используем lesson_code если доступен, иначе старый формат
            if lesson_code:
                send_button_callback = f"admin_send:{lesson_code}"
            else:
                send_button_callback = f"admin_send:{point}:{groupp}:{free}"
            print(f"[DEBUG] Создана кнопка отправки: 'Отправить учеников' -> '{send_button_callback}'")
            keyboard_buttons.append([InlineKeyboardButton(text="Отправить учеников", callback_data=send_button_callback)])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
            print(f"[DEBUG] Клавиатура создана: {len(keyboard_buttons)} кнопок")
            
            # Отправляем сообщение всем админам
            admin_verify_message = f"Отметьте постоянных учеников\nСадик: {point}\nГруппа: {groupp}\nВремя: {free}"
            print(f"[DEBUG] Текст сообщения: '{admin_verify_message}'")
            
            for admin in admins:
                try:
                    print(f"[DEBUG] Отправляем сообщение админу {admin[0]}")
                    await bot.send_message(
                        chat_id=admin[0], 
                        text=admin_verify_message, 
                        reply_markup=keyboard
                    )
                    print(f"[DEBUG] ✓ Сообщение успешно отправлено админу {admin[0]}")
                except Exception as e:
                    print(f"[ERROR] Ошибка отправки верификации админу {admin[0]}: {e}")
        else:
            print(f"[DEBUG] ✗ Админы не найдены")
    else:
        print(f"[DEBUG] ✗ Условие НЕ выполнено:")
        print(f"  - is_edit = {is_edit}")
        print(f"  - new_students = {new_students}")
        print(f"  - len(new_students) = {len(new_students) if new_students else 0}")
    
    print(f"[DEBUG] === КОНЕЦ ВЕРИФИКАЦИИ АДМИНАМИ ===")

    # Удаляем записи после успешной отправки
    # (удаление отключено, теперь только ночью)
    # cursor.execute(f"""
    #     DELETE FROM lessons 
    #     WHERE point = '{point}' 
    #     AND groupp = '{groupp}' 
    #     AND free = '{free}'
    # """)
    # conn.commit()
    # print(f"[DEBUG] Удалено записей: {cursor.rowcount}")

    await bot.edit_message_text(
        chat_id=callback.from_user.id,
        message_id=callback.message.message_id,
        text=f"✅ Посещаемость для группы {groupp} ({point}) отправлена."
    )

    conn.close()

    await callback.answer()

STUDENTS_PER_PAGE = 10


@dp.callback_query(lambda c: c.data.startswith('page:'))
async def handle_pagination(callback: CallbackQuery):
    try:
        # Получаем все части callback_data
        data_parts = callback.data.split(':')
        print(f"[DEBUG] Полные данные пагинации: {data_parts}")

        # Проверяем минимальное количество частей
        if len(data_parts) < 4:
            raise ValueError(f"Недостаточно частей в данных: {len(data_parts)}")

        # Проверяем новый формат: page:lesson_code:prev/next:page
        if len(data_parts) == 4:
            lesson_code = data_parts[1]
            direction = data_parts[2]
            current_page = int(data_parts[3])
            
            # Получаем параметры урока по коду
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                raise ValueError(f"Урок с кодом {lesson_code} не найден")
                
            print(f"[DEBUG] Новый формат пагинации: lesson_code={lesson_code}, direction={direction}, page={current_page}")
            print(f"[DEBUG] Параметры урока: point={point}, groupp={groupp}, free={free}")
        else:
            # Старый формат: page:point:groupp:free:prev/next:page
            print(f"[DEBUG] Старый формат пагинации: {data_parts}")
            
            # Извлекаем основные параметры
            point = data_parts[1].replace('_', ' ')
            groupp = data_parts[2]

            # Находим индексы направления и страницы
            direction_index = -2
            page_index = -1

            # Проверяем, что последние два элемента - это направление и страница
            if data_parts[direction_index] not in ['prev', 'next']:
                # Если не нашли направление в предпоследнем элементе, ищем в другом месте
                for i in range(3, len(data_parts) - 1):
                    if data_parts[i] in ['prev', 'next']:
                        direction_index = i
                        page_index = i + 1
                        break

                if direction_index == -2:
                    raise ValueError("Направление не найдено в данных")

            direction = data_parts[direction_index]
            current_page = int(data_parts[page_index])

            # Free - это все элементы между groupp и direction
            free_parts = data_parts[3:direction_index]
            free = ':'.join(free_parts)

        # current_page уже преобразован в int выше

        print(f"[DEBUG] Пагинация: point={point}, groupp={groupp}, free={free}, "
              f"direction={direction}, current_page={current_page}")

        # Рассчитываем новую страницу
        if direction == "next":
            new_page = current_page + 1
        elif direction == "prev":
            new_page = max(0, current_page - 1)
        else:
            raise ValueError(f"Неизвестное направление: {direction}")

        # Обновляем список учеников в зависимости от режима
        global current_edit_mode
        if current_edit_mode:
            await create_edit_keyboard(
                callback.from_user.id,
                point,
                groupp,
                free,
                page=new_page,
                message_id=callback.message.message_id,
                lesson_code=lesson_code
            )
        else:
            await create_primary_keyboard(
                callback.from_user.id,
                point,
                groupp,
                free,
                page=new_page,
                message_id=callback.message.message_id,
                lesson_code=lesson_code
            )

    except Exception as e:
        print(f"[ERROR] Ошибка в handle_pagination: {str(e)}")
        import traceback
        traceback.print_exc()
        await callback.answer(f"Ошибка пагинации: {str(e)}")
    finally:
        await callback.answer()

# ============================================================================
# ЗАПУСК БОТА
# ============================================================================

# Основная функция запуска бота и планировщика
async def main():
    create_db()  # Создаём базу данных при запуске приложения
    await start_scheduler()  # Запускаем планировщик задач
    await dp.start_polling(bot)  # Запускаем Telegram-бота

@dp.message(Command("clean_lessons"))
async def clean_lessons_command(message: Message):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем текущее время
    kaz_time = datetime.now(timezone("Asia/Ho_Chi_Minh"))
    current_time = kaz_time.strftime("%H:%M")
    
    # Удаляем все записи для текущего времени
    cursor.execute("""
        DELETE FROM lessons 
        WHERE free = ?
    """, (current_time,))
    
    deleted_count = cursor.rowcount
    conn.commit()
    
    await message.answer(f"✅ Удалено {deleted_count} старых записей для времени {current_time}")
    
    conn.close()

@dp.message(Command("current_time"))
async def show_current_time(message: Message):
    kaz_time = datetime.now(timezone("Asia/Ho_Chi_Minh"))
    current_time = kaz_time.strftime("%H:%M")
    await message.answer(f"Текущее время в системе: {current_time}")


# Добавляю обработчик для FSM ввода количества учеников
from aiogram.filters.state import StateFilter
class EnterCountState(StatesGroup):
    waiting_for_count = State()

@dp.message(StateFilter(EnterCountState.waiting_for_count))
async def process_enter_count(message: Message, state: FSMContext):
    count = message.text.strip()
    data = await state.get_data()
    point = data.get("point")
    groupp = data.get("groupp")
    teacher = data.get("teacher")
    teacher_id = message.from_user.id
    if not count.isdigit():
        await message.answer("Пожалуйста, введите число!")
        return
    # Отправляем POST на нужный вебхук
    url = WEBHOOK_COUNT_URL
    payload = {
        "point": point,
        "Groupp": groupp,
        "count": count,
        "teacher": teacher
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            await message.answer("Информация передана!")
        else:
            await message.answer(f"Ошибка передачи: {response.status_code}")
    except Exception as e:
        await message.answer(f"Ошибка передачи: {e}")
    await state.clear()

# Добавляю хендлер для команды /count
@dp.message(Command("count"))
async def start_count_fsm(message: Message, state: FSMContext):
    try:
        parts = message.text.split()
        if len(parts) < 4:
            await message.answer("Формат: /count <садик> <группа> <имя преподавателя>")
            return
        point, groupp, teacher = parts[1], parts[2], " ".join(parts[3:])
        await state.set_state(EnterCountState.waiting_for_count)
        await state.update_data(point=point, groupp=groupp, teacher=teacher)
        await message.answer(f"Введите количество учеников на уроке садик {point}, группа {groupp}:")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

# Добавляю хендлер на кнопку 'Ввести количество'
@dp.message(lambda message: message.text == "Ввести количество")
async def start_count_fsm(message: Message, state: FSMContext):
    # Для простоты: просим ввести число, сохраняем состояние
    # Если нужно, можно получить point/groupp/teacher из базы или из последнего сообщения
    # Для примера — просто просим ввести число:
    await state.set_state(EnterCountState.waiting_for_count)
    await message.answer("Введите количество учеников:", reply_markup=ReplyKeyboardRemove())

# Добавляю callback-хендлер для кнопки 'Ввести количество'
@dp.callback_query(lambda c: c.data.startswith('enter_count:'))
async def start_count_fsm_callback(callback: CallbackQuery, state: FSMContext):
    try:
        parts = callback.data.split(':')
        if len(parts) < 2:
            await callback.answer("Ошибка: недостаточно данных")
            return
        rowid = parts[1]
        # Получаем все данные урока по rowid
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT Point, Groupp, Teacher, Time_L FROM schedule WHERE rowid = ?", (rowid,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            await callback.answer("Ошибка: урок не найден")
            return
        point, groupp, teacher, time_l = row
        await state.set_state(EnterCountState.waiting_for_count)
        await state.update_data(point=point, groupp=groupp, teacher=teacher, time_l=time_l)
        await callback.message.answer(f"Введите количество учеников на уроке садик {point}, группа {groupp}:")
        await callback.answer()
    except Exception as e:
        await callback.answer(f"Ошибка: {e}")

@dp.message(Command("lessons"))
async def show_past_lessons(message: Message):
    user_id = message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    # Получаем имя преподавателя
    cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        await message.answer("Вы не зарегистрированы как преподаватель.")
        conn.close()
        return
    teacher_name = row[0]
    # Текущее время в Казахстане
    from pytz import timezone
    now_time = datetime.now(timezone('Asia/Ho_Chi_Minh')).strftime("%H:%M")
    print(f"[DEBUG] Казахстанское время сейчас: {now_time}")
    # Получаем все уроки для преподавателя
    cursor.execute("""
        SELECT DISTINCT point, groupp, free
        FROM lessons
        WHERE (name_s = ? OR ? IN (SELECT Teacher FROM schedule WHERE schedule.Point = lessons.point AND schedule.Groupp = lessons.groupp AND schedule.Time_L = lessons.free))
        ORDER BY free
    """, (teacher_name, teacher_name))
    all_lessons = cursor.fetchall()
    print(f"[DEBUG] Всего уроков для преподавателя: {len(all_lessons)}")
    for l in all_lessons:
        print(f"  {l[0]}, {l[1]}, {l[2]} (длина free: {len(str(l[2]))})")
        # Проверяем, что содержится в free
        print(f"    free repr: {repr(l[2])}")
    # Фильтруем только прошедшие
    lessons = [l for l in all_lessons if l[2] < now_time]
    print(f"[DEBUG] Прошедших уроков: {len(lessons)}")
    if not lessons:
        await message.answer("Нет прошедших уроков.")
        conn.close()
        return
    # Формируем компактные кнопки по урокам
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for i, (point, groupp, free) in enumerate(lessons):
        btn_text = f"{point}, {groupp}, {free}"
        # Используем короткий callback_data без времени
        callback_data = f"edit_lesson:{i}"
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=btn_text, callback_data=callback_data)
        ])
    
    # Сохраняем данные уроков в глобальной переменной для обработчика
    global lessons_data
    lessons_data = lessons
    await message.answer("Изменить учеников на уроке:", reply_markup=keyboard)
    conn.close()

@dp.callback_query(lambda c: c.data.startswith('edit_lesson:'))
async def handle_edit_lesson(callback: CallbackQuery):
    print(f"[DEBUG] handle_edit_lesson вызван с callback.data: {callback.data}")
    print(f"[DEBUG] Пользователь: {callback.from_user.id} ({callback.from_user.first_name})")
    
    # Получаем индекс урока из callback_data
    lesson_index = int(callback.data.split(':')[1])
    print(f"[DEBUG] Индекс урока: {lesson_index}")
    
    # Получаем данные урока из глобального списка
    if lesson_index < len(lessons_data):
        point, groupp, free = lessons_data[lesson_index]
        print(f"[DEBUG] Данные урока: point={point}, groupp={groupp}, free={free}")
    else:
        await callback.answer("Ошибка: урок не найден")
        return
    
    # Находим teacher_id
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Используем telegram_id как teacher_id (как в первичной отправке)
    teacher_id = callback.from_user.id
    print(f"[DEBUG] Используем telegram_id как teacher_id: {teacher_id}")
    
    conn.close()
    
    # Получаем lesson_code для этого урока
    lesson_code = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        print(f"[DEBUG] Поиск lesson_code в базе:")
        print(f"[DEBUG] - point: '{point}' (тип: {type(point)}, длина: {len(point)})")
        print(f"[DEBUG] - groupp: '{groupp}' (тип: {type(groupp)}, длина: {len(groupp)})")
        print(f"[DEBUG] - free: '{free}' (тип: {type(free)}, длина: {len(free)})")
        
        cursor.execute("""
            SELECT lesson_code FROM lessons 
            WHERE point = ? AND groupp = ? AND free = ? 
            LIMIT 1
        """, (point, groupp, free))
        result = cursor.fetchone()
        print(f"[DEBUG] Результат запроса: {result}")
        
        if result and result[0]:
            lesson_code = result[0]
            print(f"[DEBUG] ✓ Найден lesson_code для handle_edit_lesson: '{lesson_code}' (тип: {type(lesson_code)}, длина: {len(lesson_code)})")
        else:
            print(f"[DEBUG] ❌ lesson_code не найден для handle_edit_lesson, используем старый формат")
        conn.close()
    except Exception as e:
        print(f"[DEBUG] ❌ Ошибка при поиске lesson_code для handle_edit_lesson: {e}, используем старый формат")
    
    print(f"[DEBUG] Вызываем send_students_list с teacher_id={teacher_id}, lesson_code={lesson_code}")
    
    try:
        await create_edit_keyboard(teacher_id, point, groupp, free, lesson_code=lesson_code)
        await callback.answer("Список учеников загружен")
    except Exception as e:
        print(f"[ERROR] Ошибка в handle_edit_lesson: {e}")
        await callback.answer("Ошибка при загрузке списка учеников")


@dp.message(Command("check_lesson_codes"))
async def check_lesson_codes(message: Message):
    """Проверка lesson_code в таблице lessons - показывает по одному ученику из каждого урока"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Получаем уникальные уроки с lesson_code
        cursor.execute("""
            SELECT DISTINCT point, groupp, free, lesson_code 
            FROM lessons 
            WHERE lesson_code IS NOT NULL 
            ORDER BY point, groupp, free
        """)
        
        lessons = cursor.fetchall()
        
        if not lessons:
            await message.answer("❌ В таблице lessons нет записей с lesson_code")
            return
        
        result = "📋 **Проверка lesson_code в таблице lessons:**\n\n"
        
        for i, (point, groupp, free, lesson_code) in enumerate(lessons[:35]):  # Показываем первые 35
            result += f"**{i+1}.** Садик: `{point}`\n"
            result += f"    Группа: `{groupp}`\n"
            result += f"    Время: `{free}`\n"
            result += f"    Код: `{lesson_code}`\n\n"
        
        if len(lessons) > 35:
            result += f"... и еще {len(lessons) - 35} уроков\n"
        
        result += f"\n**Всего уроков с lesson_code: {len(lessons)}**"
        
        await message.answer(result, parse_mode="Markdown")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        print(f"[ERROR] Ошибка в check_lesson_codes: {e}")
    finally:
        conn.close()


# Обработчики выбора типа ученика для первичной отправки
@dp.callback_query(lambda c: c.data in ["primary_student_type_temporary", "primary_student_type_permanent"])
async def handle_primary_student_type_choice(callback: CallbackQuery, state: FSMContext):
    print(f"[DEBUG] === НАЧАЛО ВЫБОРА ТИПА УЧЕНИКА (ПЕРВИЧНАЯ) ===")
    print(f"[DEBUG] callback.data: {callback.data}")
    print(f"[DEBUG] callback.from_user.id: {callback.from_user.id}")
    
    try:
        data = await state.get_data()
        print(f"[DEBUG] Данные из состояния: {data}")
        
        # Получаем данные из состояния
        point = data.get('point')
        groupp = data.get('groupp')
        free = data.get('free')
        teacher_id = data.get('teacher_id')
        message_id = data.get('message_id')
        student_name = data.get('student_name')
        
        print(f"[DEBUG] Извлеченные данные:")
        print(f"  - point: '{point}' (тип: {type(point)})")
        print(f"  - groupp: '{groupp}' (тип: {type(groupp)})")
        print(f"  - free: '{free}' (тип: {type(free)})")
        print(f"  - teacher_id: {teacher_id} (тип: {type(teacher_id)})")
        print(f"  - message_id: {message_id} (тип: {type(message_id)})")
        print(f"  - student_name: '{student_name}' (тип: {type(student_name)})")
        
        # Определяем тип ученика
        is_permanent = 1 if callback.data == "primary_student_type_permanent" else 0
        type_text = "постоянный" if is_permanent else "временный"
        
        print(f"[DEBUG] Выбор типа ученика:")
        print(f"  Имя: {student_name}")
        print(f"  Тип: {type_text} (is_permanent: {is_permanent})")
        print(f"  callback.data: {callback.data}")
        
        # Добавляем ученика в базу данных
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print(f"[DEBUG] Подключение к БД установлено")
        
        # SQL запрос для добавления ученика
        sql_query = """
            INSERT INTO lessons (point, groupp, name_s, present, free, is_permanent)
            VALUES (?, ?, ?, '1', ?, ?)
        """
        params = (point, groupp, student_name, free, is_permanent)
        
        print(f"[DEBUG] SQL запрос:")
        print(f"  {sql_query}")
        print(f"  Параметры: point='{point}', groupp='{groupp}', name_s='{student_name}', free='{free}', is_permanent={is_permanent}")
        
        cursor.execute(sql_query, params)
        conn.commit()
        print(f"[DEBUG] SQL выполнен, rowcount: {cursor.rowcount}")
        print(f"[DEBUG] Транзакция зафиксирована")
        
        conn.close()
        print(f"[DEBUG] Подключение к БД закрыто")
        
        print(f"[DEBUG] Новый ученик добавлен в базу (тип: {type_text})")
        
        # Получаем lesson_code для обновления клавиатуры
        lesson_code = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT lesson_code FROM lessons 
                WHERE point = ? AND groupp = ? AND free = ? AND lesson_code IS NOT NULL
                LIMIT 1
            """, (point, groupp, free))
            result = cursor.fetchone()
            if result and result[0]:
                lesson_code = result[0]
                print(f"[DEBUG] Найден lesson_code для handle_primary_student_type_choice: {lesson_code}")
            else:
                print(f"[DEBUG] lesson_code не найден для handle_primary_student_type_choice, используем старый формат")
            conn.close()
        except Exception as e:
            print(f"[DEBUG] Ошибка при поиске lesson_code для handle_primary_student_type_choice: {e}, используем старый формат")

        # Обновляем список учеников, используя сохраненный message_id
        print(f"[DEBUG] Обновление списка учеников:")
        print(f"  - teacher_id: {teacher_id}")
        print(f"  - point: '{point}'")
        print(f"  - groupp: '{groupp}'")
        print(f"  - free: '{free}'")
        print(f"  - message_id: {message_id}")
        print(f"  - lesson_code: {lesson_code}")
        
        # Обновляем список учеников для первичной отправки
        await create_primary_keyboard(
            teacher_id,
            point,
            groupp,
            free,
            message_id=message_id,
            lesson_code=lesson_code
        )

        # Удаляем сообщение с кнопками выбора типа
        try:
            await callback.message.delete()
            print(f"[DEBUG] Сообщение с кнопками удалено")
        except Exception as e:
            print(f"[DEBUG] Ошибка при удалении сообщения: {e}")

        await callback.answer(f"Ученик {student_name} добавлен как {type_text}")
        await state.clear()
        print(f"[DEBUG] Состояние очищено")
        
    except Exception as e:
        print(f"[ERROR] Ошибка в handle_primary_student_type_choice: {e}")
        import traceback
        traceback.print_exc()
        await callback.answer(f"Ошибка: {e}")
    
    print(f"[DEBUG] === КОНЕЦ ВЫБОРА ТИПА УЧЕНИКА (ПЕРВИЧНАЯ) ===")

# Обработчики выбора типа ученика для повторной отправки
@dp.callback_query(lambda c: c.data in ["edit_student_type_temporary", "edit_student_type_permanent"])
async def handle_edit_student_type_choice(callback: CallbackQuery, state: FSMContext):
    print(f"[DEBUG] === НАЧАЛО ВЫБОРА ТИПА УЧЕНИКА (ПОВТОРНАЯ) ===")
    print(f"[DEBUG] callback.data: {callback.data}")
    print(f"[DEBUG] callback.from_user.id: {callback.from_user.id}")
    
    try:
        data = await state.get_data()
        print(f"[DEBUG] Данные из состояния: {data}")
        
        # Получаем данные из состояния
        point = data.get('point')
        groupp = data.get('groupp')
        free = data.get('free')
        teacher_id = data.get('teacher_id')
        message_id = data.get('message_id')
        student_name = data.get('student_name')
        
        print(f"[DEBUG] Извлеченные данные:")
        print(f"  - point: '{point}' (тип: {type(point)})")
        print(f"  - groupp: '{groupp}' (тип: {type(groupp)})")
        print(f"  - free: '{free}' (тип: {type(free)})")
        print(f"  - teacher_id: {teacher_id} (тип: {type(teacher_id)})")
        print(f"  - message_id: {message_id} (тип: {type(message_id)})")
        print(f"  - student_name: '{student_name}' (тип: {type(student_name)})")
        
        # Определяем тип ученика
        is_permanent = 1 if callback.data == "edit_student_type_permanent" else 0
        type_text = "постоянный" if is_permanent else "временный"
        
        print(f"[DEBUG] Выбор типа ученика:")
        print(f"  Имя: {student_name}")
        print(f"  Тип: {type_text} (is_permanent: {is_permanent})")
        print(f"  callback.data: {callback.data}")
        
        # Добавляем ученика в базу данных
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print(f"[DEBUG] Подключение к БД установлено")
        
        # SQL запрос для добавления ученика
        sql_query = """
            INSERT INTO lessons (point, groupp, name_s, present, free, is_permanent)
            VALUES (?, ?, ?, '1', ?, ?)
        """
        params = (point, groupp, student_name, free, is_permanent)
        
        print(f"[DEBUG] SQL запрос:")
        print(f"  {sql_query}")
        print(f"  Параметры: point='{point}', groupp='{groupp}', name_s='{student_name}', free='{free}', is_permanent={is_permanent}")
        
        cursor.execute(sql_query, params)
        conn.commit()
        print(f"[DEBUG] SQL выполнен, rowcount: {cursor.rowcount}")
        print(f"[DEBUG] Транзакция зафиксирована")
        
        conn.close()
        print(f"[DEBUG] Подключение к БД закрыто")
        
        print(f"[DEBUG] Новый ученик добавлен в базу (тип: {type_text})")
        
        # Получаем lesson_code для обновления клавиатуры
        lesson_code = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT lesson_code FROM lessons 
                WHERE point = ? AND groupp = ? AND free = ? AND lesson_code IS NOT NULL
                LIMIT 1
            """, (point, groupp, free))
            result = cursor.fetchone()
            if result and result[0]:
                lesson_code = result[0]
                print(f"[DEBUG] Найден lesson_code для handle_edit_student_type_choice: {lesson_code}")
            else:
                print(f"[DEBUG] lesson_code не найден для handle_edit_student_type_choice, используем старый формат")
            conn.close()
        except Exception as e:
            print(f"[DEBUG] Ошибка при поиске lesson_code для handle_edit_student_type_choice: {e}, используем старый формат")

        # Обновляем список учеников, используя сохраненный message_id
        print(f"[DEBUG] Обновление списка учеников:")
        print(f"  - teacher_id: {teacher_id}")
        print(f"  - point: '{point}'")
        print(f"  - groupp: '{groupp}'")
        print(f"  - free: '{free}'")
        print(f"  - message_id: {message_id}")
        print(f"  - lesson_code: {lesson_code}")
        
        # Обновляем список учеников для повторной отправки
        await create_edit_keyboard(
            teacher_id,
            point,
            groupp,
            free,
            message_id=message_id,
            lesson_code=lesson_code
        )

        # Удаляем сообщение с кнопками выбора типа
        try:
            await callback.message.delete()
            print(f"[DEBUG] Сообщение с кнопками удалено")
        except Exception as e:
            print(f"[DEBUG] Ошибка при удалении сообщения: {e}")

        await callback.answer(f"Ученик {student_name} добавлен как {type_text}")
        await state.clear()
        print(f"[DEBUG] Состояние очищено")
        
    except Exception as e:
        print(f"[ERROR] Ошибка в handle_edit_student_type_choice: {e}")
        import traceback
        traceback.print_exc()
        await callback.answer(f"Ошибка: {e}")
    
    print(f"[DEBUG] === КОНЕЦ ВЫБОРА ТИПА УЧЕНИКА (ПОВТОРНАЯ) ===")

# Обработчики выбора типа ученика (старая функция для совместимости)
@dp.callback_query(lambda c: c.data in ["student_type_temporary", "student_type_permanent"])
async def handle_student_type_choice(callback: CallbackQuery, state: FSMContext):
    print(f"[DEBUG] === НАЧАЛО ВЫБОРА ТИПА УЧЕНИКА ===")
    print(f"[DEBUG] callback.data: {callback.data}")
    print(f"[DEBUG] callback.from_user.id: {callback.from_user.id}")
    
    try:
        data = await state.get_data()
        print(f"[DEBUG] Данные из состояния: {data}")
        
        # Получаем данные из состояния
        point = data.get('point')
        groupp = data.get('groupp')
        free = data.get('free')
        teacher_id = data.get('teacher_id')
        message_id = data.get('message_id')
        student_name = data.get('student_name')
        
        print(f"[DEBUG] Извлеченные данные:")
        print(f"  - point: '{point}' (тип: {type(point)})")
        print(f"  - groupp: '{groupp}' (тип: {type(groupp)})")
        print(f"  - free: '{free}' (тип: {type(free)})")
        print(f"  - teacher_id: {teacher_id} (тип: {type(teacher_id)})")
        print(f"  - message_id: {message_id} (тип: {type(message_id)})")
        print(f"  - student_name: '{student_name}' (тип: {type(student_name)})")
        
        # Проверяем что все данные есть
        if not all([point, groupp, free, teacher_id, message_id, student_name]):
            print(f"[ERROR] Не все данные найдены в состоянии!")
            missing = []
            if not point: missing.append('point')
            if not groupp: missing.append('groupp')
            if not free: missing.append('free')
            if not teacher_id: missing.append('teacher_id')
            if not message_id: missing.append('message_id')
            if not student_name: missing.append('student_name')
            print(f"[ERROR] Отсутствуют: {missing}")
            await callback.answer("Ошибка: не все данные найдены")
            return
        
        # Определяем тип ученика
        is_permanent = 1 if callback.data == "student_type_permanent" else 0
        type_text = "постоянный" if is_permanent else "разовый"
        
        print(f"[DEBUG] Выбор типа ученика:")
        print(f"  Имя: {student_name}")
        print(f"  Тип: {type_text} (is_permanent: {is_permanent})")
        print(f"  callback.data: {callback.data}")
        
        # Добавляем нового ученика в базу
        conn = get_db_connection()
        cursor = conn.cursor()

        print(f"[DEBUG] Подключение к БД установлено")
        print(f"[DEBUG] SQL запрос:")
        print(f"  INSERT INTO lessons (point, groupp, name_s, present, free, is_permanent)")
        print(f"  VALUES (?, ?, ?, '1', ?, ?)")
        print(f"  Параметры: point='{point}', groupp='{groupp}', name_s='{student_name}', free='{free}', is_permanent={is_permanent}")

        # Добавляем нового ученика с указанием типа
        cursor.execute("""
            INSERT INTO lessons (point, groupp, name_s, present, free, is_permanent)
            VALUES (?, ?, ?, '1', ?, ?)
        """, (point, groupp, student_name, free, is_permanent))
        
        print(f"[DEBUG] SQL выполнен, rowcount: {cursor.rowcount}")
        conn.commit()
        print(f"[DEBUG] Транзакция зафиксирована")
        conn.close()
        print(f"[DEBUG] Подключение к БД закрыто")

        print(f"[DEBUG] Новый ученик добавлен в базу (тип: {type_text})")

        # Получаем lesson_code для этого урока
        lesson_code = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT lesson_code FROM lessons 
                WHERE point = ? AND groupp = ? AND free = ? 
                LIMIT 1
            """, (point, groupp, free))
            result = cursor.fetchone()
            if result and result[0]:
                lesson_code = result[0]
                print(f"[DEBUG] Найден lesson_code для handle_student_type_choice: {lesson_code}")
            else:
                print(f"[DEBUG] lesson_code не найден для handle_student_type_choice, используем старый формат")
            conn.close()
        except Exception as e:
            print(f"[DEBUG] Ошибка при поиске lesson_code для handle_student_type_choice: {e}, используем старый формат")

        # Обновляем список учеников, используя сохраненный message_id
        print(f"[DEBUG] Обновление списка учеников:")
        print(f"  - teacher_id: {teacher_id}")
        print(f"  - point: '{point}'")
        print(f"  - groupp: '{groupp}'")
        print(f"  - free: '{free}'")
        print(f"  - message_id: {message_id}")
        print(f"  - lesson_code: {lesson_code}")
        
        # Обновляем список учеников в зависимости от режима
        if current_edit_mode:
            await create_edit_keyboard(
                teacher_id,
                point,
                groupp,
                free,
                message_id=message_id,
                lesson_code=lesson_code
            )
        else:
            await create_primary_keyboard(
                teacher_id,
                point,
                groupp,
                free,
                message_id=message_id,
                lesson_code=lesson_code
            )

        # Удаляем сообщение с кнопками выбора типа
        try:
            await callback.message.delete()
            print(f"[DEBUG] Сообщение с кнопками удалено")
        except Exception as e:
            print(f"[DEBUG] Не удалось удалить сообщение с кнопками: {e}")
        
        await callback.answer(f"Ученик {student_name} добавлен как {type_text}")
        await state.clear()
        print(f"[DEBUG] Состояние очищено")
        
    except Exception as e:
        print(f"[ERROR] Ошибка в handle_student_type_choice: {e}")
        import traceback
        traceback.print_exc()
        await callback.answer(f"Ошибка: {e}")
    
    print(f"[DEBUG] === КОНЕЦ ВЫБОРА ТИПА УЧЕНИКА ===")

# Обработчики для верификации учеников администраторами
@dp.callback_query(lambda c: c.data.startswith('admin_verify:'))
async def handle_admin_student_verification(callback: CallbackQuery):
    """Обработчик для переключения статуса постоянный/временный ученик"""
    try:
        # Парсим callback_data: admin_verify:lesson_code:student_index или admin_verify:point:groupp:free:student_index
        parts = callback.data.split(':')
        print(f"[DEBUG] Разбираем callback_data: '{callback.data}' -> {parts}")
        
        # Проверяем новый формат: admin_verify:lesson_code:student_index
        if len(parts) == 3:
            lesson_code = parts[1]
            student_index = int(parts[2])
            
            # Получаем параметры урока по коду
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                await callback.answer("Ошибка: урок не найден")
                return
                
            print(f"[DEBUG] Новый формат верификации: lesson_code={lesson_code}, student_index={student_index}")
            print(f"[DEBUG] Параметры урока: point={point}, groupp={groupp}, free={free}")
        else:
            # Старый формат: admin_verify:point:groupp:free:student_index
            print(f"[DEBUG] Старый формат верификации: {parts}")
            
            if len(parts) < 5:
                print(f"[ERROR] Неправильный формат callback_data: {callback.data}")
                await callback.answer("Ошибка в данных урока")
                return
            
            # Простой разбор по аналогии с работающим кодом пагинации
            point = parts[1].replace('_', ' ')  # Восстанавливаем пробелы
            groupp = parts[2]                    # Без изменений
            # Free - это все элементы между groupp и student_index
            free_parts = parts[3:-1]             # Берем все части кроме последней (student_index)
            free = ':'.join(free_parts)          # Собираем время обратно
            student_index = int(parts[-1])       # Последний элемент - индекс ученика
        
        print(f"[DEBUG] Разобранные данные: point='{point}', groupp='{groupp}', free='{free}', student_index={student_index}")
        
        # Получаем список новых учеников для этого урока
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT name_s, is_permanent FROM lessons 
            WHERE point = ? AND groupp = ? AND free = ? 
            AND (student_rowid IS NULL OR student_rowid = '' OR column_d IS NULL OR column_d = '')
            AND is_send = 0
            ORDER BY id
        """, (point, groupp, free))
        
        new_students = cursor.fetchall()
        conn.close()
        
        if student_index >= len(new_students):
            await callback.answer("Ученик не найден")
            return
        
        student_name, current_status = new_students[student_index]
        new_status = 0 if current_status == 1 else 1
        
        # Обновляем статус в базе
        print(f"[DEBUG] === ОБНОВЛЕНИЕ СТАТУСА В БАЗЕ ===")
        print(f"[DEBUG] Обновляем ученика: {student_name}")
        print(f"[DEBUG] Новый статус: {new_status}")
        print(f"[DEBUG] Условия: point='{point}', groupp='{groupp}', free='{free}'")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print(f"[DEBUG] === НАЧАЛО UPDATE ===")
        print(f"[DEBUG] Параметры UPDATE:")
        print(f"  - new_status: {new_status}")
        print(f"  - point: '{point}'")
        print(f"  - groupp: '{groupp}'")
        print(f"  - free: '{free}'")
        print(f"  - student_name: '{student_name}'")
        
        # Сначала проверим, что есть в базе
        cursor.execute("""
            SELECT id, point, groupp, free, name_s, is_permanent 
            FROM lessons 
            WHERE point = ? AND groupp = ? AND free = ? AND name_s = ?
        """, (point, groupp, free, student_name))
        
        existing_record = cursor.fetchone()
        print(f"[DEBUG] Найдена запись в базе: {existing_record}")
        
        if existing_record:
            print(f"[DEBUG] ✓ Запись найдена, выполняем UPDATE")
            # Выполняем UPDATE
            cursor.execute("""
                UPDATE lessons 
                SET is_permanent = ? 
                WHERE point = ? AND groupp = ? AND free = ? AND name_s = ?
            """, (new_status, point, groupp, free, student_name))
            
            rows_affected = cursor.rowcount
            print(f"[DEBUG] UPDATE выполнен, затронуто строк: {rows_affected}")
            
            conn.commit()
            print(f"[DEBUG] ✓ Изменения сохранены в базе")
        else:
            print(f"[ERROR] ✗ Запись НЕ найдена в базе!")
        
        conn.close()
        
        print(f"[DEBUG] === КОНЕЦ UPDATE ===")
        
        # Обновляем текст кнопки
        new_button_text = f"{'✅' if new_status == 1 else '❌'} {student_name}"
        
        print(f"[DEBUG] Обновляем кнопку: {student_name} -> {new_button_text}")
        
        # Создаем новую клавиатуру с обновленной кнопкой
        keyboard_buttons = []
        
        # Получаем всех новых учеников для обновления клавиатуры
        print(f"[DEBUG] === ОБНОВЛЕНИЕ КЛАВИАТУРЫ ===")
        print(f"[DEBUG] Ищем учеников для: point='{point}', groupp='{groupp}', free='{free}'")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name_s, is_permanent FROM lessons 
            WHERE point = ? AND groupp = ? AND free = ? 
            AND (student_rowid IS NULL OR student_rowid = '' OR column_d IS NULL OR column_d = '')
            AND is_send = 0
            ORDER BY id
        """, (point, groupp, free))
        
        all_new_students = cursor.fetchall()
        print(f"[DEBUG] Найдено учеников для клавиатуры: {len(all_new_students)}")
        for i, (name_s, is_perm) in enumerate(all_new_students):
            print(f"[DEBUG] Ученик {i}: {name_s} (is_permanent={is_perm})")
        
        conn.close()
        
        # Получаем lesson_code для этого урока
        lesson_code = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT lesson_code FROM lessons 
                WHERE point = ? AND groupp = ? AND free = ? 
                LIMIT 1
            """, (point, groupp, free))
            result = cursor.fetchone()
            if result and result[0]:
                lesson_code = result[0]
                print(f"[DEBUG] Найден lesson_code для обновления клавиатуры: {lesson_code}")
            else:
                print(f"[DEBUG] lesson_code не найден для обновления клавиатуры, используем старый формат")
            conn.close()
        except Exception as e:
            print(f"[DEBUG] Ошибка при поиске lesson_code для обновления клавиатуры: {e}, используем старый формат")
        
        # Создаем кнопки для всех учеников
        for i, (name_s, is_perm) in enumerate(all_new_students):
            button_text = f"{'✅' if is_perm == 1 else '❌'} {name_s}"
            
            # Используем lesson_code если доступен, иначе старый формат
            if lesson_code:
                callback_data = f"admin_verify:{lesson_code}:{i}"
            else:
                callback_data = f"admin_verify:{point}:{groupp}:{free}:{i}"
            print(f"[DEBUG] Создаем кнопку: '{button_text}' -> '{callback_data}'")
            keyboard_buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
        
        print(f"[DEBUG] === КОНЕЦ ОБНОВЛЕНИЯ КЛАВИАТУРЫ ===")
        
        # Добавляем кнопку "Отправить учеников"
        # Используем lesson_code если доступен, иначе старый формат
        if lesson_code:
            send_button_callback = f"admin_send:{lesson_code}"
        else:
            send_button_callback = f"admin_send:{point}:{groupp}:{free}"
        keyboard_buttons.append([InlineKeyboardButton(text="Отправить учеников", callback_data=send_button_callback)])
        
        new_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Обновляем сообщение с новой клавиатурой
        await callback.message.edit_reply_markup(reply_markup=new_keyboard)
        await callback.answer(f"Статус изменен на {'постоянный' if new_status == 1 else 'временный'}")
        
    except Exception as e:
        print(f"[ERROR] Ошибка в handle_admin_student_verification: {e}")
        await callback.answer("Ошибка при изменении статуса")

@dp.callback_query(lambda c: c.data.startswith('admin_send:'))
async def handle_admin_send(callback: CallbackQuery):
    """Обработчик для отправки верифицированных учеников на webhook"""
    try:
        # Парсим callback_data: admin_send:lesson_code или admin_send:point:groupp:free
        parts = callback.data.split(':')
        print(f"[DEBUG] Разбираем callback_data: '{callback.data}' -> {parts}")
        
        # Проверяем новый формат: admin_send:lesson_code
        if len(parts) == 2:
            lesson_code = parts[1]
            
            # Получаем параметры урока по коду
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                await callback.answer("Ошибка: урок не найден")
                return
                
            print(f"[DEBUG] Новый формат админ отправки: lesson_code={lesson_code}")
            print(f"[DEBUG] Параметры урока: point={point}, groupp={groupp}, free={free}")
        else:
            # Старый формат: admin_send:point:groupp:free
            print(f"[DEBUG] Старый формат админ отправки: {parts}")
            
            if len(parts) < 4:
                print(f"[ERROR] Неправильный формат callback_data: {callback.data}")
                await callback.answer("Ошибка в данных урока")
                return
            
            # Простой разбор по аналогии с работающим кодом пагинации
            point = parts[1].replace('_', ' ')  # Восстанавливаем пробелы
            groupp = parts[2]                    # Без изменений
            # Free - это все элементы после groupp
            free_parts = parts[3:]               # Берем все части после groupp
            free = ':'.join(free_parts)          # Собираем время обратно
        
        print(f"[DEBUG] Админ отправка верифицированных: point={point}, groupp={groupp}, free={free}")
        
        # Получаем всех учеников для этого урока (по аналогии с работающим кодом)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print(f"[DEBUG] Ищем всех учеников для: point='{point}', groupp='{groupp}', free='{free}'")
        
        # Получаем только новых постоянных присутствующих неотправленных учеников
        cursor.execute("""
            SELECT point, groupp, name_s, column_d, present, is_permanent 
            FROM lessons 
            WHERE point = ? AND groupp = ? AND free = ?
            AND (student_rowid IS NULL OR student_rowid = '' OR column_d IS NULL OR column_d = '')
            AND is_permanent = 1
            AND present = '1'
            AND is_send = 0
        """, (point, groupp, free))
        
        permanent_students = cursor.fetchall()
        print(f"[DEBUG] SQL запрос выполнен, найдено новых постоянных присутствующих неотправленных учеников: {len(permanent_students)}")
        
        # Выводим всех учеников для отладки
        for i, student in enumerate(permanent_students):
            print(f"[DEBUG] Ученик {i}: {student}")
        
        conn.close()
        
        if not permanent_students:
            await callback.answer("Нет выбранных постоянных учеников")
            return
        
        print(f"[DEBUG] Найдено {len(permanent_students)} постоянных учеников для отправки")
        
        # Получаем имя преподавателя из users по telegram_id (по аналогии с существующим кодом)
        teacher_name = "Неизвестный"
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (callback.from_user.id,))
        teacher_name_row = cursor.fetchone()
        if teacher_name_row:
            teacher_name = teacher_name_row[0]
        conn.close()
        
        print(f"[DEBUG] Имя преподавателя: {teacher_name}")
        
        # Формируем данные для отправки
        data_to_send = {
            "data": [
                {
                    "point": student[0],
                    "Groupp": student[1],
                    "name": student[2],
                    "column_d": student[3] if student[3] else "",
                    "present": student[4] if student[4] else "1",
                    "teacher": teacher_name
                }
                for student in permanent_students
            ]
        }
        
        print(f"[DEBUG] Сформированы данные для отправки:")
        print(f"[DEBUG] - Всего учеников: {len(data_to_send['data'])}")
        print(f"[DEBUG] - Учитель: {teacher_name}")
        for i, student_data in enumerate(data_to_send['data']):
            print(f"[DEBUG] - Ученик {i}: {student_data}")
        
        print(f"[DEBUG] Отправка данных на webhook: {data_to_send}")
        
        # Отправляем на webhook
        response = requests.post(
            WEBHOOK_ADMIN_VERIFY_URL,
            json=data_to_send,
            timeout=30
        )
        
        print(f"[DEBUG] Статус отправки на webhook: {response.status_code}")
        
        if response.status_code == 200:
            # Убираем клавиатуру
            await callback.message.edit_reply_markup(reply_markup=None)
            
            # Отправляем сообщение об успехе
            await callback.message.edit_text(
                text=f"✅ Информация отправлена\nСадик: {point}\nГруппа: {groupp}\nВремя: {free}\nОтправлено учеников: {len(permanent_students)}"
            )
            
            await callback.answer("Информация отправлена")
            
            # Проставляем is_send = 1 для всех новых учеников этого урока
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE lessons 
                SET is_send = 1 
                WHERE point = ? AND groupp = ? AND free = ? 
                AND (student_rowid IS NULL OR student_rowid = '' OR column_d IS NULL OR column_d = '')
                AND is_send = 0
            """, (point, groupp, free))
            conn.commit()
            conn.close()
            print(f"[DEBUG ADMIN] Проставлено is_send = 1 для новых учеников урока {point} {groupp} {free}")
        else:
            await callback.answer(f"Ошибка отправки: {response.status_code}")
            
    except Exception as e:
        print(f"[ERROR] Ошибка в handle_admin_send: {e}")
        await callback.answer("Ошибка при отправке данных")

# Команда для загрузки фотографий
@dp.message(Command("foto"))
async def start_photo_upload(message: Message, state: FSMContext):
    # ПРОВЕРКА ВРЕМЕНИ
    kaz_time = datetime.now(timezone("Asia/Ho_Chi_Minh"))
    current_hour = kaz_time.hour
    
    if current_hour >= 19:
        await message.answer("⏰ Уроки не доступны", show_alert=True)
        return
    
    user_id = message.from_user.id
    print(f"[DEBUG] === НАЧАЛО /foto ===")
    print(f"[DEBUG] user_id: {user_id}")
    print(f"[DEBUG] message.from_user.first_name: {message.from_user.first_name}")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем имя преподавателя
    cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row:
        print(f"[DEBUG] ✗ Пользователь не найден в users")
        await message.answer("Вы не зарегистрированы как преподаватель.")
        conn.close()
        return
    
    teacher_name = row[0]
    print(f"[DEBUG] ✓ Преподаватель найден: '{teacher_name}'")
    
    # Получаем уроки преподавателя (как преподаватель или ассистент, без проверки даты)
    print(f"[DEBUG] Ищем уроки для пользователя '{teacher_name}' (как преподаватель или ассистент)")
    cursor.execute("""
        SELECT Point, Groupp, Time_L, DateLL
        FROM schedule 
        WHERE Teacher = ? OR Assist = ?
        ORDER BY Time_L
    """, (teacher_name, teacher_name))
    
    lessons = cursor.fetchall()
    print(f"[DEBUG] Найдено уроков: {len(lessons)}")
    for lesson in lessons:
        print(f"  - Point: '{lesson[0]}', Groupp: '{lesson[1]}', Time_L: '{lesson[2]}', DateLL: '{lesson[3]}'")
    
    conn.close()
    
    if not lessons:
        print(f"[DEBUG] ✗ Уроки не найдены")
        await message.answer("У вас нет уроков на сегодня.")
        return
    
    # Создаем кнопки для выбора урока
    print(f"[DEBUG] Создаем кнопки для {len(lessons)} уроков")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for i, (point, groupp, time_l, date_ll) in enumerate(lessons):
        btn_text = f"{point}, {groupp}, {time_l}"
        
        # Используем простой callback_data с индексом как в рабочем коде edit_lesson
        callback_data = f"select_lesson_photo:{i}"
        print(f"[DEBUG] Кнопка {i+1}: '{btn_text}' -> '{callback_data}'")
        
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text=btn_text, callback_data=callback_data)
        ])
    
    # Сохраняем данные уроков для конкретного пользователя
    user_id = message.from_user.id
    lessons_data_photo[user_id] = lessons
    print(f"[DEBUG] Сохранены уроки для пользователя {user_id}: {len(lessons)} уроков")
    
    print(f"[DEBUG] Клавиатура создана: {len(keyboard.inline_keyboard)} кнопок")
    print(f"[DEBUG] Отправляем сообщение с кнопками")
    
    await message.answer("Выберите урок для загрузки фото и видео:", reply_markup=keyboard)
    await state.set_state(PhotoUpload.waiting_for_lesson_selection)
    print(f"[DEBUG] Состояние установлено: PhotoUpload.waiting_for_lesson_selection")
    print(f"[DEBUG] === КОНЕЦ /foto ===")

# Обработчик выбора урока для загрузки фото
@dp.callback_query(lambda c: c.data.startswith('select_lesson_photo:'))
async def handle_lesson_selection_for_photo(callback: CallbackQuery, state: FSMContext):
    print(f"[DEBUG] === ВЫБОР УРОКА ДЛЯ ФОТО ===")
    print(f"[DEBUG] callback.data: {callback.data}")
    
    try:
        parts = callback.data.split(':')
        print(f"[DEBUG] parts: {parts}")
        print(f"[DEBUG] len(parts): {len(parts)}")
        
        if len(parts) < 2:
            print(f"[ERROR] Недостаточно частей в callback.data")
            await callback.answer("Ошибка: неверный формат данных")
            return
        
        # Получаем индекс урока из callback_data
        lesson_index = int(parts[1])
        print(f"[DEBUG] Индекс урока: {lesson_index}")
        
        # Получаем данные урока для конкретного пользователя
        user_id = callback.from_user.id
        user_lessons = lessons_data_photo.get(user_id, [])

        if not user_lessons:
            await callback.answer("Ошибка: данные уроков не найдены. Попробуйте снова команду /foto")
            return

        if lesson_index < len(user_lessons):
            point, groupp, time_l, date_ll = user_lessons[lesson_index]
            print(f"[DEBUG] Данные урока для пользователя {user_id}: point={point}, groupp={groupp}, time_l={time_l}, date_ll={date_ll}")
        else:
            await callback.answer("Ошибка: урок не найден")
            return
        
        # Очищаем старое состояние ПЕРЕД установкой новых данных
        await state.clear()
        print(f"[DEBUG] Старое состояние очищено")
        
        # Сохраняем данные урока в состоянии
        await state.update_data(
            point=point,
            groupp=groupp,
            time_l=time_l,
            date_ll=date_ll
        )
        print(f"[DEBUG] Новые данные сохранены в состоянии")
        
        # Создаем клавиатуру с кнопкой "Завершить"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Завершить", callback_data="finish_photo_upload")]
        ])
        
        await callback.message.edit_text(
            f"Загрузите фото и видео с урока:\n"
            f"Садик: {point}\n"
            f"Группа: {groupp}\n"
            f"Время: {time_l}\n"
            f"Дата: {date_ll}\n\n"
            f"Отправьте фото и видео (можно несколько сразу)\n\n"
            f"Когда закончите, нажмите 'Завершить'",
            reply_markup=keyboard
        )
        
        await state.set_state(PhotoUpload.waiting_for_photos)
        print(f"[DEBUG] Состояние установлено: PhotoUpload.waiting_for_photos")
        await callback.answer()
        
    except Exception as e:
        print(f"[ERROR] Ошибка в handle_lesson_selection_for_photo: {e}")
        import traceback
        traceback.print_exc()
        
        # Очищаем данные пользователя при ошибке
        user_id = callback.from_user.id
        if user_id in lessons_data_photo:
            del lessons_data_photo[user_id]
            print(f"[DEBUG] Очищены данные уроков для пользователя {user_id} из-за ошибки")
        
        await callback.answer(f"Ошибка: {e}")
    
    print(f"[DEBUG] === КОНЕЦ ВЫБОРА УРОКА ДЛЯ ФОТО ===")

# Обработчик загрузки фото и видео
@dp.message(StateFilter(PhotoUpload.waiting_for_photos))
async def handle_photo_upload(message: Message, state: FSMContext):
    
    if not message.photo and not message.video:
        await message.answer("Пожалуйста, отправьте фото или видео.")
        return
    
    data = await state.get_data()
    point = data.get('point')
    groupp = data.get('groupp')
    time_l = data.get('time_l')
    date_ll = data.get('date_ll')
    
    print(f"[DEBUG] === ЗАГРУЗКА ФАЙЛА ===")
    print(f"[DEBUG] point: '{point}', groupp: '{groupp}', time_l: '{time_l}', date_ll: '{date_ll}'")
    
    # Получаем информацию о файле (фото или видео)
    if message.photo:
        file_obj = message.photo[-1]  # Берем самое большое разрешение
        file_type = 'photo'
        print(f"[DEBUG] Фото: file_id={file_obj.file_id}, size={file_obj.file_size}")
    else:
        file_obj = message.video
        file_type = 'video'
        print(f"[DEBUG] Видео: file_id={file_obj.file_id}, size={file_obj.file_size}")
    
    file_id = file_obj.file_id
    file_unique_id = file_obj.file_unique_id
    file_size = file_obj.file_size
    
    # Сохраняем файл в БД
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Проверяем количество файлов ДО добавления нового
        cursor.execute("""
            SELECT COUNT(*) FROM fotoalbum 
            WHERE kindergarten = ? AND groupp = ? AND date = ? AND time = ?
        """, (point, groupp, date_ll, time_l))
        
        existing_file_count = cursor.fetchone()[0]
        print(f"[DEBUG] Файлов уже в БД: {existing_file_count}")
        
        # Сохраняем файл в БД
        cursor.execute("""
            INSERT INTO fotoalbum (kindergarten, groupp, teacher, date, time, file_id, file_unique_id, file_size, file_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (point, groupp, message.from_user.first_name, date_ll, time_l, file_id, file_unique_id, file_size, file_type))
        
        conn.commit()
        print(f"[DEBUG] Файл сохранен в БД")
        
        # Новое количество файлов после добавления
        new_file_count = existing_file_count + 1
        print(f"[DEBUG] Новое количество файлов: {new_file_count}")
        
        # Простое подтверждение загрузки файла
        await message.answer(f"✅ Файл #{new_file_count} сохранен!")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при сохранении файла: {e}")
        print(f"[ERROR] Ошибка сохранения файла: {e}")
    finally:
        conn.close()
    
    print(f"[DEBUG] === КОНЕЦ ЗАГРУЗКИ ФАЙЛА ===")

# Обработчик кнопки "Закончить"
@dp.callback_query(lambda c: c.data == "finish_photo_upload")
async def handle_finish_photo_upload(callback: CallbackQuery, state: FSMContext):
    print(f"[DEBUG] === КНОПКА ЗАКОНЧИТЬ ===")
    print(f"[DEBUG] callback.message.message_id: {callback.message.message_id}")
    
    data = await state.get_data()
    print(f"[DEBUG STATE] === АНАЛИЗ FSM СОСТОЯНИЯ ===")
    print(f"[DEBUG STATE] Все данные в состоянии: {data}")
    print(f"[DEBUG STATE] Тип данных: {type(data)}")
    
    point = data.get('point')
    groupp = data.get('groupp')
    time_l = data.get('time_l')
    date_ll = data.get('date_ll')
    
    print(f"[DEBUG STATE] Извлеченные данные:")
    print(f"[DEBUG STATE] - point: '{point}' (тип: {type(point)})")
    print(f"[DEBUG STATE] - groupp: '{groupp}' (тип: {type(groupp)})")
    print(f"[DEBUG STATE] - time_l: '{time_l}' (тип: {type(time_l)})")
    print(f"[DEBUG STATE] - date_ll: '{date_ll}' (тип: {type(date_ll)})")
    print(f"[DEBUG STATE] === КОНЕЦ АНАЛИЗА FSM ===")
    
    print(f"[DEBUG] Данные урока: point='{point}', groupp='{groupp}', time_l='{time_l}', date_ll='{date_ll}'")
    
    # Обновляем статус в таблице schedule
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            UPDATE schedule 
            SET foto = 'done' 
            WHERE Point = ? AND Groupp = ? AND Time_L = ? AND DateLL = ?
        """, (point, groupp, time_l, date_ll))
        
        conn.commit()
        print(f"[DEBUG] Статус в schedule обновлен")
        
        # Уведомляем DoubleA и Account
        cursor.execute("SELECT telegram_id FROM users WHERE status IN ('DoubleA', 'Account')")
        admins = cursor.fetchall()
        print(f"[DEBUG] Найдено получателей уведомлений: {len(admins)}")
        
        # Получаем имя и ник преподавателя из базы данных
        cursor.execute("SELECT name, nik_name FROM users WHERE telegram_id = ?", (callback.from_user.id,))
        user_data = cursor.fetchone()
        user_name, nik_name = user_data if user_data else ("Неизвестный", "")
        
        admin_message = f"📸 Файлы с урока загружены!\n"
        admin_message += f"Садик: {point}\n"
        admin_message += f"Группа: {groupp}\n"
        admin_message += f"Время: {time_l}\n"
        admin_message += f"Дата: {date_ll}\n"
        admin_message += f"Преподаватель: {user_name}"
        if nik_name:
            admin_message += f" ({nik_name})"
        
        # Создаем кнопку для выгрузки фото (новый подход с таблицей)
        # Сохраняем данные урока в таблицу export_lessons и получаем ID
        print(f"[DEBUG BUTTON] === НОВЫЙ ПОДХОД С ТАБЛИЦЕЙ ===")
        print(f"[DEBUG BUTTON] Попытка INSERT в export_lessons:")
        print(f"[DEBUG BUTTON] - point: '{point}'")
        print(f"[DEBUG BUTTON] - groupp: '{groupp}'")
        print(f"[DEBUG BUTTON] - time_l: '{time_l}'")
        print(f"[DEBUG BUTTON] - date_ll: '{date_ll}'")
        
        try:
            # Получаем modul и theme из таблицы schedule
            cursor.execute("""
                SELECT modul, theme FROM schedule 
                WHERE Point = ? AND Groupp = ? AND Time_L = ? AND DateLL = ?
            """, (point, groupp, time_l, date_ll))
            
            schedule_data = cursor.fetchone()
            modul = schedule_data[0] if schedule_data and schedule_data[0] else ""
            theme = schedule_data[1] if schedule_data and schedule_data[1] else ""
            
            print(f"[DEBUG BUTTON] Получены данные из schedule: modul='{modul}', theme='{theme}'")
            
            cursor.execute("""
                INSERT INTO export_lessons (point, groupp, time_l, date_ll, modul, theme)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (point, groupp, time_l, date_ll, modul, theme))
            export_id = cursor.lastrowid
            print(f"[DEBUG BUTTON] ✓ INSERT выполнен успешно, export_id: {export_id}")
            
            # Фиксируем изменения в БД
            conn.commit()
            print(f"[DEBUG BUTTON] ✓ Изменения зафиксированы в БД")
        except Exception as e:
            print(f"[DEBUG BUTTON] ❌ ОШИБКА INSERT: {e}")
            raise
        
        print(f"[DEBUG BUTTON] Данные урока сохранены в export_lessons[{export_id}]: {point}, {groupp}, {time_l}, {date_ll}")
        
        # Создаем callback_data только с ID урока
        callback_data = f"export_photos:{export_id}"
        print(f"[DEBUG BUTTON] Созданный callback_data: '{callback_data}'")
        print(f"[DEBUG BUTTON] Длина callback_data: {len(callback_data)}")
        print(f"[DEBUG BUTTON] === КОНЕЦ СОЗДАНИЯ КНОПКИ ===")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Выгрузить файлы", callback_data=callback_data)]
        ])
        
        for admin in admins:
            print(f"[DEBUG BUTTON] === ОТПРАВКА СООБЩЕНИЯ АДМИНУ ===")
            print(f"[DEBUG BUTTON] Admin ID: {admin[0]}")
            print(f"[DEBUG BUTTON] Текст сообщения: '{admin_message}'")
            print(f"[DEBUG BUTTON] Клавиатура: {keyboard}")
            print(f"[DEBUG BUTTON] Попытка отправки...")
            
            try:
                await bot.send_message(
                    chat_id=admin[0], 
                    text=admin_message,
                    reply_markup=keyboard
                )
                print(f"[DEBUG BUTTON] ✓ Уведомление успешно отправлено админу {admin[0]}")
            except Exception as e:
                print(f"[DEBUG BUTTON] ❌ ОШИБКА отправки админу {admin[0]}: {e}")
                print(f"[DEBUG BUTTON] Тип ошибки: {type(e)}")
                import traceback
                traceback.print_exc()
                raise  # Перебрасываем ошибку дальше
            
            print(f"[DEBUG BUTTON] === КОНЕЦ ОТПРАВКИ ===")
        
        await callback.message.edit_text("✅ Загрузка файлов завершена!")
        
        # Очищаем данные уроков для этого пользователя
        user_id = callback.from_user.id
        if user_id in lessons_data_photo:
            del lessons_data_photo[user_id]
            print(f"[DEBUG] Очищены данные уроков для пользователя {user_id}")
        
        await state.clear()
        print(f"[DEBUG] Состояние очищено")
        
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}")
        print(f"[ERROR] Ошибка завершения загрузки: {e}")
        import traceback
        traceback.print_exc()
        
        # Очищаем данные пользователя при ошибке
        user_id = callback.from_user.id
        if user_id in lessons_data_photo:
            del lessons_data_photo[user_id]
            print(f"[DEBUG] Очищены данные уроков для пользователя {user_id} из-за ошибки")
    finally:
        conn.close()
    
    print(f"[DEBUG] === КОНЕЦ КНОПКИ ЗАКОНЧИТЬ ===")


async def create_zip_parts(files, archive_name, max_size_mb=45):
    """
    Создает ZIP архивы, разбивая файлы на части по размеру
    
    Args:
        files: Список файлов для архивирования
        archive_name: Базовое имя архива
        max_size_mb: Максимальный размер части в МБ (по умолчанию 45MB)
    
    Returns:
        list: Список кортежей (part_data, part_filename, part_number, total_parts)
    """
    max_size_bytes = max_size_mb * 1024 * 1024  # Конвертируем в байты
    
    print(f"[DEBUG ZIP SPLIT] Создаем архивы с максимальным размером {max_size_mb} MB")
    print(f"[DEBUG ZIP SPLIT] Всего файлов для архивирования: {len(files)}")
    
    # Если файлов мало, создаем один архив
    if len(files) <= 10:  # Эвристика: если файлов мало, скорее всего поместится в один архив
        print(f"[DEBUG ZIP SPLIT] Мало файлов, создаем один архив")
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for i, (file_id, file_unique_id, file_size, file_type) in enumerate(files, 1):
                try:
                    # Получаем файл по file_id
                    file_info = await bot.get_file(file_id)
                    file_data = await bot.download_file(file_info.file_path)
                    
                    # Определяем расширение файла на основе типа
                    file_extension = '.jpg' if file_type == 'photo' else '.mp4'
                    
                    # Создаем имя файла в архиве
                    file_name = f"{file_type}_{i:03d}{file_extension}"
                    
                    # Добавляем файл в архив
                    zip_file.writestr(file_name, file_data.read())
                    
                except Exception as e:
                    print(f"[ERROR ZIP SPLIT] Ошибка при обработке файла {i}: {e}")
                    continue
        
        zip_buffer.seek(0)
        zip_data = zip_buffer.getvalue()
        
        # Проверяем размер
        if len(zip_data) <= max_size_bytes:
            print(f"[DEBUG ZIP SPLIT] Один архив помещается: {len(zip_data) / (1024*1024):.2f} MB")
            return [(zip_data, archive_name, 1, 1)]
        else:
            print(f"[DEBUG ZIP SPLIT] Один архив слишком большой: {len(zip_data) / (1024*1024):.2f} MB, разбиваем")
    
    # Разбиваем файлы на группы для создания нескольких архивов
    parts = []
    current_part = 1
    current_files = []
    current_size = 0
    
    for i, (file_id, file_unique_id, file_size, file_type) in enumerate(files):
        # Проверяем, поместится ли файл в текущую часть
        if current_size + file_size > max_size_bytes and current_files:
            # Создаем архив из текущих файлов (БЕЗ текущего файла)
            part_data = await create_zip_from_files(current_files, current_part)
            part_filename = f"{archive_name}_{current_part}.zip"
            parts.append((part_data, part_filename, current_part, 0))  # total_parts будет обновлен позже
            
            print(f"[DEBUG ZIP SPLIT] Создан архив {current_part}: {len(part_data) / (1024*1024):.2f} MB, файлов: {len(current_files)}")
            
            # Начинаем новую часть с текущим файлом
            current_part += 1
            current_files = [(file_id, file_unique_id, file_size, file_type)]
            current_size = file_size
        else:
            # Добавляем файл в текущую часть
            current_files.append((file_id, file_unique_id, file_size, file_type))
            current_size += file_size
    
    # Создаем последний архив
    if current_files:
        part_data = await create_zip_from_files(current_files, current_part)
        part_filename = f"{archive_name}_{current_part}.zip"
        parts.append((part_data, part_filename, current_part, 0))
        
        print(f"[DEBUG ZIP SPLIT] Создан архив {current_part}: {len(part_data) / (1024*1024):.2f} MB, файлов: {len(current_files)}")
    
    # Обновляем total_parts для всех частей
    total_parts = len(parts)
    for i in range(len(parts)):
        parts[i] = (parts[i][0], parts[i][1], parts[i][2], total_parts)
    
    print(f"[DEBUG ZIP SPLIT] Создано {total_parts} частей")
    return parts


async def create_zip_from_files(files, part_number):
    """
    Создает ZIP архив из списка файлов
    """
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for i, (file_id, file_unique_id, file_size, file_type) in enumerate(files, 1):
            try:
                # Получаем файл по file_id
                file_info = await bot.get_file(file_id)
                file_data = await bot.download_file(file_info.file_path)
                
                # Определяем расширение файла на основе типа
                file_extension = '.jpg' if file_type == 'photo' else '.mp4'
                
                # Создаем имя файла в архиве
                file_name = f"{file_type}_{i:03d}{file_extension}"
                
                # Добавляем файл в архив
                zip_file.writestr(file_name, file_data.read())
                
            except Exception as e:
                print(f"[ERROR ZIP CREATE] Ошибка при обработке файла {i} в части {part_number}: {e}")
                continue
    
    zip_buffer.seek(0)
    return zip_buffer.getvalue()


# Обработчик экспорта фото для админа
@dp.callback_query(lambda c: c.data.startswith('export_photos:'))
async def handle_export_photos(callback: CallbackQuery):
    try:
        print(f"[DEBUG EXPORT] === ОБРАБОТКА КНОПКИ ЭКСПОРТА ===")
        print(f"[DEBUG EXPORT] Полный callback.data: '{callback.data}'")
        print(f"[DEBUG EXPORT] Длина callback.data: {len(callback.data)}")
        
        parts = callback.data.split(':')
        print(f"[DEBUG EXPORT] Разбитые части: {parts}")
        print(f"[DEBUG EXPORT] Количество частей: {len(parts)}")
        
        if len(parts) < 2:
            print(f"[DEBUG EXPORT] ОШИБКА: недостаточно частей (нужно 2, есть {len(parts)})")
            await callback.answer("Ошибка: неверный формат данных")
            return
        
        # Получаем ID урока из callback_data
        export_id = int(parts[1])
        print(f"[DEBUG EXPORT] ID урока для экспорта: {export_id}")
        
        # Получаем данные урока из таблицы export_lessons
        conn = get_db_connection()
        cursor = conn.cursor()
        
        print(f"[DEBUG EXPORT] Поиск урока с ID {export_id} в таблице export_lessons...")
        
        # Проверяем, существует ли таблица
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='export_lessons'")
        table_exists = cursor.fetchone()
        if not table_exists:
            print(f"[DEBUG EXPORT] ❌ Таблица export_lessons НЕ СУЩЕСТВУЕТ!")
            await callback.answer("Ошибка: таблица export_lessons не найдена")
            return
        
        print(f"[DEBUG EXPORT] ✓ Таблица export_lessons существует")
        
        # Проверяем количество записей в таблице
        cursor.execute("SELECT COUNT(*) FROM export_lessons")
        total_records = cursor.fetchone()[0]
        print(f"[DEBUG EXPORT] Всего записей в таблице export_lessons: {total_records}")
        
        # Ищем конкретную запись
        cursor.execute("""
            SELECT point, groupp, time_l, date_ll, modul, theme 
            FROM export_lessons 
            WHERE id = ?
        """, (export_id,))
        
        lesson_data = cursor.fetchone()
        if not lesson_data:
            print(f"[DEBUG EXPORT] ❌ Урок с ID {export_id} не найден в таблице export_lessons")
            print(f"[DEBUG EXPORT] Попробуем найти все записи...")
            
            # Показываем все записи для отладки
            cursor.execute("SELECT id, point, groupp, time_l, date_ll, modul, theme FROM export_lessons LIMIT 10")
            all_records = cursor.fetchall()
            print(f"[DEBUG EXPORT] Первые 10 записей в таблице:")
            for record in all_records:
                print(f"[DEBUG EXPORT] - ID: {record[0]}, Point: {record[1]}, Group: {record[2]}, Time: {record[3]}, Date: {record[4]}, Modul: {record[5]}, Theme: {record[6]}")
            
            await callback.answer("Данные урока не найдены")
            return
        
        point, groupp, time_l, date_ll, modul, theme = lesson_data
        print(f"[DEBUG EXPORT] Данные урока получены из таблицы export_lessons:")
        print(f"[DEBUG EXPORT] - point: '{point}'")
        print(f"[DEBUG EXPORT] - groupp: '{groupp}'")
        print(f"[DEBUG EXPORT] - time_l: '{time_l}'")
        print(f"[DEBUG EXPORT] - date_ll: '{date_ll}'")
        print(f"[DEBUG EXPORT] - modul: '{modul}'")
        print(f"[DEBUG EXPORT] - theme: '{theme}'")
        
        print(f"[DEBUG EXPORT] === КОНЕЦ ОБРАБОТКИ КНОПКИ ===")
        
        # Создаем заблокированную клавиатуру для прогресса
        keyboard_blocked = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏳ Обрабатываю...", callback_data="processing")]
        ])
        
        # Получаем ссылки через вебхук на основе модуля
        mass_link = ""
        picture_link = ""
        
        if modul and theme:
            try:
                # Обновляем прогресс - запрос к вебхуку
                await callback.message.edit_text(
                    "🔄 Получаю ссылки через вебхук...\n"
                    "Пожалуйста, подождите.",
                    reply_markup=keyboard_blocked
                )
                
                webhook_url = None
                if modul == "Собирай":
                    webhook_url = "https://hook.eu2.make.com/qi573yyxi48wtbt7atvsw1x17sfcmy88"
                elif modul == "Конструируй":
                    webhook_url = "https://hook.eu2.make.com/r1mygjngqkpusjsj2caqru900q4xxixg"
                elif modul == "Программируй":
                    webhook_url = "https://hook.eu2.make.com/t0ncjfd7c29dwrwncjwbzfyegesvyxtk"
                elif modul == "Школьники":
                    webhook_url = "https://hook.eu2.make.com/hj7ofzzbwpnuyfyntiqq6p3tstq6tu91"
                elif modul == "Scratch":
                    webhook_url = "https://hook.eu2.make.com/3ciprue991krd9osvj5t0ppzlh7pxnmf"
                
                if webhook_url:
                    print(f"[DEBUG EXPORT] Отправляем запрос к вебхуку: {webhook_url}")
                    print(f"[DEBUG EXPORT] Данные запроса: theme='{theme}'")
                    try:
                        response = requests.post(webhook_url, json={"theme": theme}, timeout=30)
                        print(f"[DEBUG EXPORT] Статус ответа: {response.status_code}")
                        print(f"[DEBUG EXPORT] Содержимое ответа: '{response.text[:200]}...'")
                        
                        if response.status_code == 200:
                            webhook_data = response.json()
                            mass_link = webhook_data.get("mass", "")
                            picture_link = webhook_data.get("picture", "")
                            print(f"[DEBUG EXPORT] Вебхук ответил: mass='{mass_link}', picture='{picture_link}'")
                        else:
                            print(f"[ERROR EXPORT] Вебхук вернул статус {response.status_code}")
                    except requests.exceptions.RequestException as req_e:
                        print(f"[ERROR EXPORT] Ошибка сети при запросе к вебхуку: {req_e}")
                        raise req_e
                    except ValueError as json_e:
                        print(f"[ERROR EXPORT] Ошибка парсинга JSON: {json_e}")
                        print(f"[ERROR EXPORT] Ответ вебхука: '{response.text}'")
                        raise json_e
                else:
                    print(f"[DEBUG EXPORT] Модуль '{modul}' не соответствует известным вебхукам")
            except Exception as e:
                print(f"[ERROR EXPORT] Ошибка при запросе к вебхуку: {e}")
        else:
            print(f"[DEBUG EXPORT] Модуль или тема пустые: modul='{modul}', theme='{theme}'")
        
        # Получаем все файлы с урока
        
        cursor.execute("""
            SELECT file_id, file_unique_id, file_size, file_type
            FROM fotoalbum 
            WHERE kindergarten = ? AND groupp = ? AND date = ? AND time = ?
        """, (point, groupp, date_ll, time_l))
        
        files = cursor.fetchall()
        conn.close()
        
        if not files:
            await callback.answer("Файлы не найдены")
            return
        
        # Показываем прогресс начала обработки
        await callback.message.edit_text(
            "🔄 Обрабатываю запрос...\n"
            "Пожалуйста, не нажимайте кнопку повторно.",
            reply_markup=keyboard_blocked
        )
        
        try:
            # Создаем название архива (с временем)
            archive_name = f"{point}_{groupp}_{date_ll}_{time_l}.zip"
            # Заменяем недопустимые символы в имени файла
            archive_name = "".join(c for c in archive_name if c.isalnum() or c in (' ', '-', '_', '.')).rstrip()
            
            # Пытаемся использовать новую логику с разбивкой
            try:
                # Создаем архивы, разбивая файлы на части если нужно
                archive_parts = await create_zip_parts(files, archive_name)
                
                # Формируем базовую подпись архива с ссылками
                base_caption = f"📸 ZIP архив с файлами\n"
                base_caption += f"Садик: {point}\n"
                base_caption += f"Группа: {groupp}\n"
                base_caption += f"Время: {time_l}\n"
                base_caption += f"Дата: {date_ll}\n"
                base_caption += f"Всего файлов: {len(files)}"
                
                # Добавляем ссылки на сообщение и изображение
                if mass_link:
                    base_caption += f"\nСообщение: <a href=\"{mass_link}\">ссылка</a>"
                else:
                    base_caption += f"\nСообщение: _"
                
                if picture_link:
                    base_caption += f"\nИмидж: <a href=\"{picture_link}\">ссылка</a>"
                else:
                    base_caption += f"\nИмидж: _"
                
                # Отправляем части архива
                total_parts = len(archive_parts)
                sent_parts = 0
                
                for i, (part_data, part_filename, part_number, total_parts) in enumerate(archive_parts):
                    try:
                        # Обновляем прогресс отправки
                        if total_parts > 1:
                            await callback.message.edit_text(
                                f"📤 Отправляю архив {part_number} из {total_parts}...\n"
                                f"Пожалуйста, подождите.",
                                reply_markup=keyboard_blocked
                            )
                        
                        # Формируем подпись для архива
                        if total_parts > 1:
                            part_caption = f"{base_caption}\n\n📦 Архив {part_number} из {total_parts}"
                        else:
                            part_caption = base_caption
                        
                        # Отправляем архив
                        await bot.send_document(
                            chat_id=callback.from_user.id,
                            document=BufferedInputFile(part_data, filename=part_filename),
                            caption=part_caption,
                            parse_mode='HTML'
                        )
                        
                        sent_parts += 1
                        print(f"[DEBUG ZIP SEND] Отправлена часть {part_number}/{total_parts}: {part_filename}")
                        
                    except Exception as e:
                        print(f"[ERROR ZIP SEND] Ошибка отправки части {part_number}: {e}")
                        # Продолжаем отправку остальных частей
                        continue
                
                # Проверяем, что хотя бы одна часть была отправлена
                if sent_parts == 0:
                    raise Exception("Не удалось отправить ни одной части архива")
                
                # Финальное сообщение
                if total_parts > 1:
                    await callback.message.edit_text(f"✅ ZIP архивы созданы и отправлены!\n"
                                                   f"Базовое название: {archive_name}\n"
                                                   f"Всего файлов: {len(files)}\n"
                                                   f"Архивов: {sent_parts}/{total_parts}")
                else:
                    await callback.message.edit_text(f"✅ ZIP архив создан и отправлен!\n"
                                                   f"Название: {archive_name}\n"
                                                   f"Файлов: {len(files)}")
                
            except Exception as split_error:
                print(f"[ERROR ZIP SPLIT] Ошибка при разбивке архива: {split_error}")
                print(f"[ERROR ZIP SPLIT] Fallback на старый метод отправки")
                
                # Fallback на старый метод - создаем один ZIP архив
                try:
                    print(f"[DEBUG ZIP FALLBACK] Создаем один ZIP архив (старый метод)")
                    
                    # Создаем ZIP архив в памяти (старый метод)
                    zip_buffer = io.BytesIO()
                    
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                        # Скачиваем и добавляем каждый файл в архив
                        for i, (file_id, file_unique_id, file_size, file_type) in enumerate(files, 1):
                            try:
                                # Получаем файл по file_id
                                file_info = await bot.get_file(file_id)
                                file_data = await bot.download_file(file_info.file_path)
                                
                                # Определяем расширение файла на основе типа
                                file_extension = '.jpg' if file_type == 'photo' else '.mp4'
                                
                                # Создаем имя файла в архиве
                                file_name = f"{file_type}_{i:03d}{file_extension}"
                                
                                # Добавляем файл в архив
                                zip_file.writestr(file_name, file_data.read())
                                
                                # Обновляем прогресс каждые 5 файлов
                                if i % 5 == 0:
                                    await callback.message.edit_text(
                                        f"🔄 Обработано {i} из {len(files)} файлов...\n"
                                        "Пожалуйста, подождите.",
                                        reply_markup=keyboard_blocked
                                    )
                                
                            except Exception as e:
                                print(f"[ERROR FALLBACK] Ошибка при обработке файла {i}: {e}")
                                # Продолжаем со следующим файлом
                                continue
                    
                    # Перемещаем указатель в начало буфера
                    zip_buffer.seek(0)
                    
                    # Формируем подпись архива с ссылками (старый формат)
                    caption = f"📸 ZIP архив с файлами\n"
                    caption += f"Садик: {point}\n"
                    caption += f"Группа: {groupp}\n"
                    caption += f"Время: {time_l}\n"
                    caption += f"Дата: {date_ll}\n"
                    caption += f"Всего файлов: {len(files)}"
                    
                    # Добавляем ссылки на сообщение и изображение
                    if mass_link:
                        caption += f"\nСообщение: <a href=\"{mass_link}\">ссылка</a>"
                    else:
                        caption += f"\nСообщение: _"
                    
                    if picture_link:
                        caption += f"\nИмидж: <a href=\"{picture_link}\">ссылка</a>"
                    else:
                        caption += f"\nИмидж: _"
                    
                    # Отправляем ZIP архив (старый метод)
                    await bot.send_document(
                        chat_id=callback.from_user.id,
                        document=BufferedInputFile(zip_buffer.getvalue(), filename=archive_name),
                        caption=caption,
                        parse_mode='HTML'
                    )
                    
                    await callback.message.edit_text(f"✅ ZIP архив создан и отправлен!\n"
                                                   f"Название: {archive_name}\n"
                                                   f"Файлов: {len(files)}\n"
                                                   f"⚠️ Использован старый метод отправки")
                    
                except Exception as fallback_error:
                    print(f"[ERROR ZIP FALLBACK] Ошибка при fallback отправке: {fallback_error}")
                    raise fallback_error
            
        except Exception as e:
            await callback.message.edit_text(f"❌ Ошибка при создании ZIP архива: {e}")
            print(f"[ERROR] Ошибка создания ZIP: {e}")
        
        await callback.answer("ZIP архив готов!")
        
    except Exception as e:
        await callback.answer(f"❌ Ошибка: {e}")
        print(f"[ERROR] Ошибка экспорта файлов: {e}")

# Обработчик заблокированной кнопки (показывает, что идет обработка)
@dp.callback_query(lambda c: c.data == "processing")
async def handle_processing_button(callback: CallbackQuery):
    await callback.answer("⏳ Идет обработка, пожалуйста, подождите...", show_alert=True)

# Команда для добавления полей modul и theme в таблицу export_lessons

# Команда для обновления структуры БД
@dp.message(Command("update_db_structure"))
async def update_db_structure(message: Message):
    user_id = message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Проверяем, является ли пользователь администратором
        cursor.execute("SELECT status FROM users WHERE telegram_id = ?", (user_id,))
        user = cursor.fetchone()
        
        if not user or user[0] != "Admin":
            await message.answer("❌ У вас нет прав для выполнения этой команды")
            return
        
        await message.answer("🔄 Начинаю обновление структуры БД...")
        
        # 1. Добавляем колонку foto в таблицу schedule (если её нет)
        cursor.execute("PRAGMA table_info(schedule)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'foto' not in columns:
            cursor.execute("ALTER TABLE schedule ADD COLUMN foto TEXT")
            await message.answer("✅ Колонка 'foto' добавлена в таблицу 'schedule'")
        else:
            await message.answer("ℹ️ Колонка 'foto' уже существует в таблице 'schedule'")
        
        # 2. Создаем таблицу fotoalbum (если её нет)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fotoalbum (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kindergarten TEXT,
                groupp TEXT,
                teacher TEXT,
                date TEXT,
                time TEXT,
                file_id TEXT,
                file_unique_id TEXT,
                file_size INTEGER,
                upload_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await message.answer("✅ Таблица 'fotoalbum' создана/проверена")
        
        conn.commit()
        
        await message.answer("🎉 Структура БД успешно обновлена!")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении БД: {e}")
        print(f"[ERROR] Ошибка обновления структуры БД: {e}")
    finally:
        conn.close()


@dp.message(Command("add_is_send_column"))
async def add_is_send_column_command(message: Message):
    """Команда для добавления колонки is_send в таблицу lessons"""
    # Проверяем, является ли пользователь администратором
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE telegram_id = ?", (message.from_user.id,))
    user = cursor.fetchone()
    
    if not user or user[0] != "Admin":
        await message.answer("У вас нет прав для выполнения этой команды")
        conn.close()
        return
    
    conn.close()
    
    add_is_send_column()
    await message.answer("✅ Колонка is_send добавлена в таблицу lessons")


# ============================================================================
# ФУНКЦИИ ДЛЯ ПЕРВИЧНОЙ ОТПРАВКИ (автоматическая за 10 минут до урока)
# ============================================================================

async def create_primary_keyboard(teacher_id, point, groupp, free, page=0, message_id=None, lesson_code=None):
    """Создает клавиатуру для первичной отправки (автоматическая за 10 минут до урока)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    print(f"[DEBUG PRIMARY] Создание клавиатуры для первичной отправки:")
    print(f"  - point: {point}")
    print(f"  - groupp: {groupp}")
    print(f"  - free: {free}")
    print(f"  - page: {page}")
    print(f"  - lesson_code: {lesson_code}")
    
    # Получаем всех учеников
    cursor.execute("""
        SELECT id, name_s, present 
        FROM lessons 
        WHERE point = ? AND groupp = ? AND free = ?
        ORDER BY name_s
    """, (point, groupp, free))
    all_students = cursor.fetchall()
    
    if not all_students:
        await bot.send_message(teacher_id, f"Нет учеников для группы {groupp} ({point})")
        conn.close()
        return
    
    # Пагинация
    start_index = page * STUDENTS_PER_PAGE
    end_index = start_index + STUDENTS_PER_PAGE
    students_page = all_students[start_index:end_index]
    total_pages = (len(all_students) + STUDENTS_PER_PAGE - 1) // STUDENTS_PER_PAGE
    
    # Создаем клавиатуру
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    # Добавляем учеников текущей страницы
    for student in students_page:
        student_id, name_s, present = student
        is_present = present == "1"
        
        callback_data = f"primary_student:{student_id}:{page}"
        button_text = f"✅ {name_s}" if is_present else name_s
        
        print(f"[DEBUG PRIMARY] Создание кнопки ученика:")
        print(f"  - student_id: {student_id} (тип: {type(student_id)})")
        print(f"  - page: {page} (тип: {type(page)})")
        print(f"  - callback_data: '{callback_data}' (длина: {len(callback_data)})")
        print(f"  - button_text: '{button_text}'")
        
        try:
            keyboard.inline_keyboard.append([InlineKeyboardButton(
                text=button_text,
                callback_data=callback_data
            )])
            print(f"[DEBUG PRIMARY] Кнопка создана успешно")
        except Exception as e:
            print(f"[ERROR PRIMARY] Ошибка создания кнопки ученика {name_s}: {e}")
            print(f"[ERROR PRIMARY] callback_data: '{callback_data}'")
            print(f"[ERROR PRIMARY] button_text: '{button_text}'")
    
    # Добавляем кнопки навигации
    if page > 0:
        callback_data = f"primary_page:{lesson_code}:prev:{page}" if lesson_code else f"primary_page:{point}:{groupp}:{free}:prev:{page}"
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)])
    
    if end_index < len(all_students):
        callback_data = f"primary_page:{lesson_code}:next:{page}" if lesson_code else f"primary_page:{point}:{groupp}:{free}:next:{page}"
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="➡️ Вперед", callback_data=callback_data)])
    
    # Кнопка добавления ученика
    if lesson_code:
        add_callback = f"add_primary_student:{lesson_code}"
    else:
        add_callback = f"add_primary_student:{point}:{groupp}:{free}"
    
    keyboard.inline_keyboard.append([InlineKeyboardButton(
        text="➕ Добавить ученика",
        callback_data=add_callback
    )])
    
    # Кнопка отправки данных
    present_count = sum(1 for _, _, present in all_students if present == "1")
    total_count = len(all_students)
    
    if lesson_code:
        send_callback = f"primary_send:{lesson_code}"
    else:
        send_callback = f"primary_send:{point}:{groupp}:{free}"
    
    keyboard.inline_keyboard.append([InlineKeyboardButton(
        text=f"Отправить данные ({present_count}/{total_count})",
        callback_data=send_callback
    )])
    
    # Отправляем или обновляем сообщение
    page_info = f" (Страница {page + 1}/{total_pages})" if total_pages > 1 else ""
    message_text = f"Отметьте присутствующих учеников ({groupp}, {point}){page_info}:"
    
    if message_id is None:
        await bot.send_message(teacher_id, message_text, reply_markup=keyboard)
    else:
        await bot.edit_message_text(
            chat_id=teacher_id,
            message_id=message_id,
            text=message_text,
            reply_markup=keyboard
        )
    
    conn.close()


async def handle_primary_student(callback: CallbackQuery):
    """Обработка клика по ученику в первичной отправке"""
    try:
        print(f"[DEBUG PRIMARY] Обработка клика по ученику:")
        print(f"  - callback.data: '{callback.data}'")
        print(f"  - длина: {len(callback.data)}")
        
        data_parts = callback.data.split(':')
        print(f"  - data_parts: {data_parts}")
        print(f"  - количество частей: {len(data_parts)}")
        
        if len(data_parts) < 3:
            print(f"[ERROR PRIMARY] Неправильный формат callback_data: {callback.data}")
            await callback.answer("Ошибка в данных кнопки")
            return
            
        student_id = int(data_parts[1])
        page = int(data_parts[2])
        
        print(f"  - student_id: {student_id}")
        print(f"  - page: {page}")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Получаем данные ученика
        cursor.execute("SELECT point, groupp, free FROM lessons WHERE id = ?", (student_id,))
        student_data = cursor.fetchone()
        
        if not student_data:
            await callback.answer("Ученик не найден")
            conn.close()
            return
        
        point, groupp, free = student_data
        
        # Переключаем статус присутствия
        cursor.execute("""
            UPDATE lessons 
            SET present = CASE WHEN present = '1' THEN '0' ELSE '1' END 
            WHERE id = ?
        """, (student_id,))
        conn.commit()
        
        # Получаем lesson_code для этого урока
        lesson_code = None
        try:
            cursor.execute("""
                SELECT lesson_code FROM lessons 
                WHERE point = ? AND groupp = ? AND free = ? 
                AND lesson_code IS NOT NULL 
                LIMIT 1
            """, (point, groupp, free))
            result = cursor.fetchone()
            if result and result[0]:
                lesson_code = result[0]
                print(f"[DEBUG] Найден lesson_code для handle_primary_student: {lesson_code}")
            else:
                print(f"[DEBUG] lesson_code не найден для handle_primary_student, используем старый формат")
        except Exception as e:
            print(f"[DEBUG] Ошибка при поиске lesson_code для handle_primary_student: {e}, используем старый формат")
        
        # Обновляем список учеников
        await create_primary_keyboard(
            callback.from_user.id,
            point,
            groupp,
            free,
            page=page,
            message_id=callback.message.message_id,
            lesson_code=lesson_code
        )
        
        await callback.answer()
        conn.close()
        
    except Exception as e:
        print(f"[ERROR PRIMARY] Ошибка в handle_primary_student: {e}")
        await callback.answer("Ошибка при обновлении статуса ученика")


async def handle_primary_pagination(callback: CallbackQuery):
    """Обработка навигации в первичной отправке"""
    try:
        data_parts = callback.data.split(':')
        
        if len(data_parts) == 4:  # Новый формат: primary_page:lesson_code:prev/next:page
            lesson_code = data_parts[1]
            direction = data_parts[2]
            current_page = int(data_parts[3])
            
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                await callback.answer("Урок не найден")
                return
        else:  # Старый формат: primary_page:point:groupp:free:prev/next:page
            point = data_parts[1].replace('_', ' ')
            groupp = data_parts[2]
            free = ':'.join(data_parts[3:-2])
            direction = data_parts[-2]
            current_page = int(data_parts[-1])
        
        new_page = current_page - 1 if direction == "prev" else current_page + 1
        
        await create_primary_keyboard(
            callback.from_user.id,
            point,
            groupp,
            free,
            page=new_page,
            message_id=callback.message.message_id,
            lesson_code=lesson_code
        )
        
        await callback.answer()
        
    except Exception as e:
        print(f"[ERROR PRIMARY] Ошибка в handle_primary_pagination: {e}")
        await callback.answer("Ошибка при навигации")


async def handle_primary_send(callback: CallbackQuery):
    """Обработка отправки данных в первичной отправке (WEBHOOK_ATTENDANCE_URL)"""
    try:
        # Убираем клавиатуру
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except:
            pass
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Получаем параметры урока
        data_parts = callback.data.split(':')
        
        if len(data_parts) == 2:  # Новый формат: primary_send:lesson_code
            lesson_code = data_parts[1]
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                await callback.answer("Урок не найден")
                return
        else:  # Старый формат: primary_send:point:groupp:free
            point = data_parts[1].replace('_', ' ')
            groupp = data_parts[2]
            free = ':'.join(data_parts[3:])
        
        print(f"[DEBUG PRIMARY] Отправка данных для первичной отправки:")
        print(f"  - point: {point}")
        print(f"  - groupp: {groupp}")
        print(f"  - free: {free}")
        
        # Получаем всех учеников (только присутствующих)
        cursor.execute("""
            SELECT point, groupp, name_s, student_rowid, column_d, is_permanent, present 
            FROM lessons 
            WHERE point = ? AND groupp = ? AND free = ? AND present = '1'
        """, (point, groupp, free))
        all_present_students = cursor.fetchall()
        
        if not all_present_students:
            await callback.answer("Нет данных для отправки")
            conn.close()
            return
        
        # Разделяем на обычных и новых учеников
        regular_students = []
        new_students = []
        
        for student in all_present_students:
            point_val, groupp_val, name_s, student_rowid, column_d, is_permanent, present = student
            
            if student_rowid is None or student_rowid == '' or column_d is None or column_d == '':
                new_students.append((point_val, groupp_val, name_s, is_permanent))
            else:
                present_value = 1 if present == '1' else 0
                regular_students.append((point_val, groupp_val, name_s, column_d, present_value))
        
        # Отправляем webhook для обычных учеников (WEBHOOK_ATTENDANCE_URL)
        if regular_students:
            # Получаем имя преподавателя
            cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (callback.from_user.id,))
            teacher_name_row = cursor.fetchone()
            teacher_name = teacher_name_row[0] if teacher_name_row else "Неизвестный"
            
            # Используем ту же структуру данных, что и в старой функции
            data_to_send = {
                "data": [
                    {
                        "point": student[0],
                        "Groupp": student[1],
                        "name": student[2],
                        "column_d": student[3],
                        "present": student[4],
                        "teacher": teacher_name
                    }
                    for student in regular_students
                ]
            }
            
            try:
                response = requests.post(WEBHOOK_ATTENDANCE_URL, json=data_to_send, timeout=30)
                print(f"[DEBUG PRIMARY] Webhook отправлен: {response.status_code}")
            except Exception as e:
                print(f"[ERROR PRIMARY] Ошибка отправки webhook: {e}")
        
        # Отправляем webhook для новых учеников (WEBHOOK_NEW_STUDENTS_URL)
        if new_students:
            # Получаем имя преподавателя (если еще не получено)
            if 'teacher_name' not in locals():
                cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (callback.from_user.id,))
                teacher_name_row = cursor.fetchone()
                teacher_name = teacher_name_row[0] if teacher_name_row else "Неизвестный"
            
            # Используем ту же структуру данных, что и в старой функции
            new_data_to_send = {
                "data": [
                    {
                        "point": student[0],
                        "Groupp": student[1],
                        "name": student[2],
                        "teacher": teacher_name,
                        "is_permanent": student[3]
                    }
                    for student in new_students
                ]
            }
            
            try:
                response = requests.post(WEBHOOK_NEW_STUDENTS_URL, json=new_data_to_send, timeout=30)
                print(f"[DEBUG PRIMARY] Webhook новых учеников отправлен: {response.status_code}")
            except Exception as e:
                print(f"[ERROR PRIMARY] Ошибка отправки webhook новых учеников: {e}")
        
        # Отправляем новых учеников админам для верификации
        if new_students:
            cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
            admins = cursor.fetchall()
            
            if admins:
                # Получаем lesson_code для кнопок
                lesson_code = None
                try:
                    cursor.execute("""
                        SELECT lesson_code FROM lessons 
                        WHERE point = ? AND groupp = ? AND free = ? AND lesson_code IS NOT NULL
                        LIMIT 1
                    """, (point, groupp, free))
                    result = cursor.fetchone()
                    if result and result[0]:
                        lesson_code = result[0]
                except Exception as e:
                    print(f"[DEBUG PRIMARY] Ошибка при поиске lesson_code: {e}")
                
                # Создаем клавиатуру с новыми учениками
                keyboard_buttons = []
                
                for i, student in enumerate(new_students):
                    point_val, groupp_val, name_s, is_permanent = student
                    
                    # Создаем кнопку с именем ученика и его текущим статусом
                    button_text = f"{'✅' if is_permanent == 1 else '❌'} {name_s}"
                    
                    # Используем lesson_code если доступен, иначе старый формат
                    if lesson_code:
                        callback_data = f"admin_verify:{lesson_code}:{i}"
                    else:
                        callback_data = f"admin_verify:{point}:{groupp}:{free}:{i}"
                    
                    keyboard_buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
                
                # Добавляем кнопку "Отправить учеников"
                if lesson_code:
                    send_button_callback = f"admin_send:{lesson_code}"
                else:
                    send_button_callback = f"admin_send:{point}:{groupp}:{free}"
                
                keyboard_buttons.append([InlineKeyboardButton(text="Отправить учеников", callback_data=send_button_callback)])
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
                
                # Отправляем сообщение всем админам
                admin_verify_message = f"Отметьте постоянных учеников\nСадик: {point}\nГруппа: {groupp}\nВремя: {free}"
                
                for admin in admins:
                    try:
                        await bot.send_message(
                            chat_id=admin[0], 
                            text=admin_verify_message, 
                            reply_markup=keyboard
                        )
                    except Exception as e:
                        print(f"[ERROR PRIMARY] Ошибка отправки админу {admin[0]}: {e}")
        
        # Уведомляем админов, если учеников менее 3
        total_students = len(regular_students) + len(new_students)
        if total_students < 3:
            cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
            admins = cursor.fetchall()
            
            if admins:
                # Определяем правильное окончание для числа
                if total_students == 1:
                    student_word = "ученик"
                else:
                    student_word = "ученика"
                
                admin_message = f"В садике {point}, в группе {groupp}, в {free} - присутствуют {total_students} {student_word}."
                
                for admin in admins:
                    try:
                        await bot.send_message(chat_id=admin[0], text=admin_message)
                    except Exception as e:
                        print(f"[ERROR PRIMARY] Ошибка отправки админу {admin[0]}: {e}")
        
        
        await bot.edit_message_text(
            chat_id=callback.from_user.id,
            message_id=callback.message.message_id,
            text=f"✅ Посещаемость для группы {groupp} ({point}) отправлена."
        )
        
        conn.close()
        await callback.answer()
        
    except Exception as e:
        print(f"[ERROR PRIMARY] Ошибка в handle_primary_send: {e}")
        await callback.answer("Ошибка при отправке данных")


# ============================================================================
# ФУНКЦИИ ДЛЯ ПОВТОРНОЙ ОТПРАВКИ (команда /lessons)
# ============================================================================

async def create_edit_keyboard(teacher_id, point, groupp, free, page=0, message_id=None, lesson_code=None):
    """Создает клавиатуру для повторной отправки (команда /lessons)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    print(f"[DEBUG EDIT] Создание клавиатуры для повторной отправки:")
    print(f"  - point: {point}")
    print(f"  - groupp: {groupp}")
    print(f"  - free: {free}")
    print(f"  - page: {page}")
    print(f"  - lesson_code: {lesson_code}")
    
    # Получаем всех учеников
    cursor.execute("""
        SELECT id, name_s, present 
        FROM lessons 
        WHERE point = ? AND groupp = ? AND free = ?
        ORDER BY name_s
    """, (point, groupp, free))
    all_students = cursor.fetchall()
    
    if not all_students:
        await bot.send_message(teacher_id, f"Нет учеников для группы {groupp} ({point})")
        conn.close()
        return
    
    # Пагинация
    start_index = page * STUDENTS_PER_PAGE
    end_index = start_index + STUDENTS_PER_PAGE
    students_page = all_students[start_index:end_index]
    total_pages = (len(all_students) + STUDENTS_PER_PAGE - 1) // STUDENTS_PER_PAGE
    
    # Создаем клавиатуру
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    
    # Добавляем учеников текущей страницы
    for student in students_page:
        student_id, name_s, present = student
        is_present = present == "1"
        
        callback_data = f"edit_student:{student_id}:{page}"
        button_text = f"✅ {name_s}" if is_present else name_s
        
        try:
            keyboard.inline_keyboard.append([InlineKeyboardButton(
                text=button_text,
                callback_data=callback_data
            )])
        except Exception as e:
            print(f"[ERROR EDIT] Ошибка создания кнопки ученика {name_s}: {e}")
    
    # Добавляем кнопки навигации
    if page > 0:
        callback_data = f"edit_page:{lesson_code}:prev:{page}" if lesson_code else f"edit_page:{point}:{groupp}:{free}:prev:{page}"
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=callback_data)])
    
    if end_index < len(all_students):
        callback_data = f"edit_page:{lesson_code}:next:{page}" if lesson_code else f"edit_page:{point}:{groupp}:{free}:next:{page}"
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="➡️ Вперед", callback_data=callback_data)])
    
    # Кнопка добавления ученика
    if lesson_code:
        add_callback = f"add_edit_student:{lesson_code}"
    else:
        add_callback = f"add_edit_student:{point}:{groupp}:{free}"
    
    keyboard.inline_keyboard.append([InlineKeyboardButton(
        text="➕ Добавить ученика",
        callback_data=add_callback
    )])
    
    # Кнопка отправки данных
    present_count = sum(1 for _, _, present in all_students if present == "1")
    total_count = len(all_students)
    
    if lesson_code:
        send_callback = f"edit_send:{lesson_code}"
    else:
        send_callback = f"edit_send:{point}:{groupp}:{free}"
    
    keyboard.inline_keyboard.append([InlineKeyboardButton(
        text=f"Отправить данные ({present_count}/{total_count})",
        callback_data=send_callback
    )])
    
    # Отправляем или обновляем сообщение
    page_info = f" (Страница {page + 1}/{total_pages})" if total_pages > 1 else ""
    message_text = f"Отметьте присутствующих учеников ({groupp}, {point}){page_info}:"
    
    if message_id is None:
        await bot.send_message(teacher_id, message_text, reply_markup=keyboard)
    else:
        await bot.edit_message_text(
            chat_id=teacher_id,
            message_id=message_id,
            text=message_text,
            reply_markup=keyboard
        )
    
    conn.close()


async def handle_edit_student(callback: CallbackQuery):
    """Обработка клика по ученику в повторной отправке"""
    try:
        data_parts = callback.data.split(':')
        student_id = int(data_parts[1])
        page = int(data_parts[2])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Получаем данные ученика
        cursor.execute("SELECT point, groupp, free FROM lessons WHERE id = ?", (student_id,))
        student_data = cursor.fetchone()
        
        if not student_data:
            await callback.answer("Ученик не найден")
            conn.close()
            return
        
        point, groupp, free = student_data
        
        # Переключаем статус присутствия
        cursor.execute("""
            UPDATE lessons 
            SET present = CASE WHEN present = '1' THEN '0' ELSE '1' END 
            WHERE id = ?
        """, (student_id,))
        conn.commit()
        
        # Получаем lesson_code для этого урока
        lesson_code = None
        try:
            cursor.execute("""
                SELECT lesson_code FROM lessons 
                WHERE point = ? AND groupp = ? AND free = ? 
                AND lesson_code IS NOT NULL 
                LIMIT 1
            """, (point, groupp, free))
            result = cursor.fetchone()
            if result and result[0]:
                lesson_code = result[0]
                print(f"[DEBUG] Найден lesson_code для handle_edit_student: {lesson_code}")
            else:
                print(f"[DEBUG] lesson_code не найден для handle_edit_student, используем старый формат")
        except Exception as e:
            print(f"[DEBUG] Ошибка при поиске lesson_code для handle_edit_student: {e}, используем старый формат")
        
        # Обновляем список учеников
        await create_edit_keyboard(
            callback.from_user.id,
            point,
            groupp,
            free,
            page=page,
            message_id=callback.message.message_id,
            lesson_code=lesson_code
        )
        
        await callback.answer()
        conn.close()
        
    except Exception as e:
        print(f"[ERROR EDIT] Ошибка в handle_edit_student: {e}")
        await callback.answer("Ошибка при обновлении статуса ученика")


async def handle_edit_pagination(callback: CallbackQuery):
    """Обработка навигации в повторной отправке"""
    try:
        data_parts = callback.data.split(':')
        
        if len(data_parts) == 4:  # Новый формат: edit_page:lesson_code:prev/next:page
            lesson_code = data_parts[1]
            direction = data_parts[2]
            current_page = int(data_parts[3])
            
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                await callback.answer("Урок не найден")
                return
        else:  # Старый формат: edit_page:point:groupp:free:prev/next:page
            point = data_parts[1].replace('_', ' ')
            groupp = data_parts[2]
            free = ':'.join(data_parts[3:-2])
            direction = data_parts[-2]
            current_page = int(data_parts[-1])
        
        new_page = current_page - 1 if direction == "prev" else current_page + 1
        
        await create_edit_keyboard(
            callback.from_user.id,
            point,
            groupp,
            free,
            page=new_page,
            message_id=callback.message.message_id,
            lesson_code=lesson_code
        )
        
        await callback.answer()
        
    except Exception as e:
        print(f"[ERROR EDIT] Ошибка в handle_edit_pagination: {e}")
        await callback.answer("Ошибка при навигации")


async def handle_edit_send(callback: CallbackQuery):
    """Обработка отправки данных в повторной отправке (WEBHOOK_LESSONS_EDIT_URL)"""
    try:
        # Убираем клавиатуру
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except:
            pass
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Получаем параметры урока
        data_parts = callback.data.split(':')
        
        if len(data_parts) == 2:  # Новый формат: edit_send:lesson_code
            lesson_code = data_parts[1]
            point, groupp, free = get_lesson_by_code(lesson_code)
            if not point:
                await callback.answer("Урок не найден")
                return
        else:  # Старый формат: edit_send:point:groupp:free
            point = data_parts[1].replace('_', ' ')
            groupp = data_parts[2]
            free = ':'.join(data_parts[3:])
        
        print(f"[DEBUG EDIT] Отправка данных для повторной отправки:")
        print(f"  - point: {point}")
        print(f"  - groupp: {groupp}")
        print(f"  - free: {free}")
        
        # Получаем всех учеников
        cursor.execute("""
            SELECT point, groupp, name_s, student_rowid, column_d, is_permanent, present, is_send
            FROM lessons 
            WHERE point = ? AND groupp = ? AND free = ?
        """, (point, groupp, free))
        all_present_students = cursor.fetchall()
        
        if not all_present_students:
            await callback.answer("Нет данных для отправки")
            conn.close()
            return
        
        # Разделяем на обычных и новых учеников
        regular_students = []
        new_students = []
        
        for student in all_present_students:
            point_val, groupp_val, name_s, student_rowid, column_d, is_permanent, present, is_send = student
            
            if student_rowid is None or student_rowid == '' or column_d is None or column_d == '':
                # Новый ученик - проверяем, не отправлялся ли уже
                if is_send is None or is_send != 1:
                    new_students.append((point_val, groupp_val, name_s, is_permanent))
                    print(f"[DEBUG EDIT] Новый ученик {name_s} будет отправлен (is_send = {is_send})")
                else:
                    print(f"[DEBUG EDIT] Новый ученик {name_s} уже отправлялся (is_send = {is_send})")
            else:
                present_value = 1 if present == '1' else 0
                regular_students.append((point_val, groupp_val, name_s, column_d, present_value))
        
        # Отправляем webhook для всех учеников (WEBHOOK_LESSONS_EDIT_URL)
        all_students_data = []
        for student in all_present_students:
            point_val, groupp_val, name_s, student_rowid, column_d, is_permanent, present, is_send = student
            if student_rowid is not None and student_rowid != '' and column_d is not None and column_d != '':
                present_value = 1 if present == '1' else 0
                all_students_data.append((point_val, groupp_val, name_s, column_d, present_value))
        
        if all_students_data:
            # Получаем имя преподавателя
            cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (callback.from_user.id,))
            teacher_name_row = cursor.fetchone()
            teacher_name = teacher_name_row[0] if teacher_name_row else "Неизвестный"
            
            # Используем ту же структуру данных, что и в старой функции
            data_to_send = {
                "data": [
                    {
                        "point": student[0],
                        "Groupp": student[1],
                        "name": student[2],
                        "column_d": student[3],
                        "present": student[4],
                        "teacher": teacher_name
                    }
                    for student in all_students_data
                ]
            }
            
            try:
                response = requests.post(WEBHOOK_LESSONS_EDIT_URL, json=data_to_send, timeout=50)
                print(f"[DEBUG EDIT] Webhook отправлен: {response.status_code}")
            except Exception as e:
                print(f"[ERROR EDIT] Ошибка отправки webhook: {e}")
        
        # Отправляем webhook для новых учеников (WEBHOOK_NEW_STUDENTS_URL)
        if new_students:
            # Получаем имя преподавателя (если еще не получено)
            if 'teacher_name' not in locals():
                cursor.execute("SELECT name FROM users WHERE telegram_id = ?", (callback.from_user.id,))
                teacher_name_row = cursor.fetchone()
                teacher_name = teacher_name_row[0] if teacher_name_row else "Неизвестный"
            
            # Используем ту же структуру данных, что и в старой функции
            new_data_to_send = {
                "data": [
                    {
                        "point": student[0],
                        "Groupp": student[1],
                        "name": student[2],
                        "teacher": teacher_name,
                        "is_permanent": student[3]
                    }
                    for student in new_students
                ]
            }
            
            try:
                response = requests.post(WEBHOOK_NEW_STUDENTS_URL, json=new_data_to_send, timeout=30)
                print(f"[DEBUG EDIT] Webhook новых учеников отправлен: {response.status_code}")
            except Exception as e:
                print(f"[ERROR EDIT] Ошибка отправки webhook новых учеников: {e}")
        
        # Отправляем новых учеников админам для верификации
        if new_students:
            cursor.execute("SELECT telegram_id FROM users WHERE status IN ('Admin', 'DoubleA')")
            admins = cursor.fetchall()
            
            if admins:
                # Получаем lesson_code для кнопок
                lesson_code = None
                try:
                    cursor.execute("""
                        SELECT lesson_code FROM lessons 
                        WHERE point = ? AND groupp = ? AND free = ? AND lesson_code IS NOT NULL
                        LIMIT 1
                    """, (point, groupp, free))
                    result = cursor.fetchone()
                    if result and result[0]:
                        lesson_code = result[0]
                except Exception as e:
                    print(f"[DEBUG EDIT] Ошибка при поиске lesson_code: {e}")
                
                # Создаем клавиатуру с новыми учениками
                keyboard_buttons = []
                
                for i, student in enumerate(new_students):
                    point_val, groupp_val, name_s, is_permanent = student
                    
                    # Создаем кнопку с именем ученика и его текущим статусом
                    button_text = f"{'✅' if is_permanent == 1 else '❌'} {name_s}"
                    
                    # Используем lesson_code если доступен, иначе старый формат
                    if lesson_code:
                        callback_data = f"admin_verify:{lesson_code}:{i}"
                    else:
                        callback_data = f"admin_verify:{point}:{groupp}:{free}:{i}"
                    
                    keyboard_buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
                
                # Добавляем кнопку "Отправить учеников"
                if lesson_code:
                    send_button_callback = f"admin_send:{lesson_code}"
                else:
                    send_button_callback = f"admin_send:{point}:{groupp}:{free}"
                
                keyboard_buttons.append([InlineKeyboardButton(text="Отправить учеников", callback_data=send_button_callback)])
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
                
                # Отправляем сообщение всем админам
                admin_verify_message = f"Отметьте постоянных учеников\nСадик: {point}\nГруппа: {groupp}\nВремя: {free}"
                
                for admin in admins:
                    try:
                        await bot.send_message(
                            chat_id=admin[0], 
                            text=admin_verify_message, 
                            reply_markup=keyboard
                        )
                    except Exception as e:
                        print(f"[ERROR EDIT] Ошибка отправки админу {admin[0]}: {e}")
        
        
        await bot.edit_message_text(
            chat_id=callback.from_user.id,
            message_id=callback.message.message_id,
            text=f"✅ Посещаемость для группы {groupp} ({point}) отправлена."
        )
        
        conn.close()
        await callback.answer()
        
    except Exception as e:
        print(f"[ERROR EDIT] Ошибка в handle_edit_send: {e}")
        await callback.answer("Ошибка при отправке данных")


# ============================================================================
# НОВЫЕ CALLBACK HANDLERS ДЛЯ РАЗДЕЛЕННОЙ ЛОГИКИ
# ============================================================================

@dp.callback_query(lambda c: c.data.startswith('primary_student:'))
async def handle_primary_student_callback(callback: CallbackQuery):
    """Callback handler для клика по ученику в первичной отправке"""
    await handle_primary_student(callback)


@dp.callback_query(lambda c: c.data.startswith('primary_page:'))
async def handle_primary_pagination_callback(callback: CallbackQuery):
    """Callback handler для навигации в первичной отправке"""
    await handle_primary_pagination(callback)


@dp.callback_query(lambda c: c.data.startswith('primary_send:'))
async def handle_primary_send_callback(callback: CallbackQuery):
    """Callback handler для отправки данных в первичной отправке"""
    await handle_primary_send(callback)


@dp.callback_query(lambda c: c.data.startswith('edit_student:'))
async def handle_edit_student_callback(callback: CallbackQuery):
    """Callback handler для клика по ученику в повторной отправке"""
    await handle_edit_student(callback)


@dp.callback_query(lambda c: c.data.startswith('edit_page:'))
async def handle_edit_pagination_callback(callback: CallbackQuery):
    """Callback handler для навигации в повторной отправке"""
    await handle_edit_pagination(callback)


@dp.callback_query(lambda c: c.data.startswith('edit_send:'))
async def handle_edit_send_callback(callback: CallbackQuery):
    """Callback handler для отправки данных в повторной отправке"""
    await handle_edit_send(callback)


if __name__ == "__main__":
    asyncio.run(main())
