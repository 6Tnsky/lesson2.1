"""
Обработчики для загрузки и экспорта фотографий
"""

from aiogram import F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from datetime import datetime, timedelta
from pytz import timezone
import requests
import asyncio
import os
import zipfile
import io

# Импорты состояний и данных
from states.photo_upload import PhotoUpload
from constants.photo_data import lessons_data_photo

async def start_photo_upload(message: Message, state: FSMContext, get_db_connection):
    # ПРОВЕРКА ВРЕМЕНИ
    kaz_time = datetime.now(timezone("Asia/Almaty"))
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

async def handle_photo_upload(message: Message, state: FSMContext, get_db_connection):
    
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

async def handle_finish_photo_upload(callback: CallbackQuery, state: FSMContext, get_db_connection, bot):
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

async def handle_export_photos(callback: CallbackQuery, get_db_connection, create_zip_parts, bot):
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
        print(f"[DEBUG EXPORT] Вызываем get_db_connection()...")
        conn = get_db_connection()
        print(f"[DEBUG EXPORT] get_db_connection() выполнен успешно")
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
        
        # Получаем все файлы с урока
        print(f"[DEBUG EXPORT] Ищем файлы в fotoalbum с параметрами:")
        print(f"[DEBUG EXPORT] - kindergarten = '{point}'")
        print(f"[DEBUG EXPORT] - groupp = '{groupp}'")
        print(f"[DEBUG EXPORT] - date = '{date_ll}'")
        print(f"[DEBUG EXPORT] - time = '{time_l}'")
        
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
        print(f"[DEBUG EXPORT] Найдено файлов в fotoalbum: {len(files)}")
        for i, file_data in enumerate(files):
            print(f"[DEBUG EXPORT] Файл {i+1}: {file_data}")
        
        conn.close()
        
        if not files:
            print(f"[DEBUG EXPORT] ❌ Файлы не найдены!")
            await callback.answer("Файлы не найдены")
            return
        
        print(f"[DEBUG EXPORT] ✅ Файлы найдены, продолжаем создание архива...")
        
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
                archive_parts = await create_zip_parts(files, archive_name, bot)
                
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
                
                for i, (zip_data, part_filename, part_number, total_parts) in enumerate(archive_parts):
                    # Обновляем прогресс
                    progress_text = f"🔄 Отправляю архив...\nЧасть {i+1} из {total_parts}"
                    await callback.message.edit_text(progress_text, reply_markup=keyboard_blocked)
                    
                    # Формируем подпись для части архива
                    if total_parts > 1:
                        caption = f"{base_caption}\n\n📦 Часть {i+1} из {total_parts}"
                    else:
                        caption = base_caption
                    
                    # Отправляем часть архива
                    await bot.send_document(
                        chat_id=callback.from_user.id,
                        document=BufferedInputFile(zip_data, filename=part_filename),
                        caption=caption,
                        parse_mode='HTML'
                    )
                    sent_parts += 1
                    print(f"[DEBUG EXPORT] Отправлена часть {i+1}/{total_parts}: {part_filename}")
                
                # Финальное сообщение
                await callback.message.edit_text(f"✅ ZIP архив создан и отправлен!\n"
                                               f"Название: {archive_name}\n"
                                               f"Файлов: {len(files)}")
                
            except Exception as zip_error:
                print(f"[ERROR ZIP] Ошибка при создании zip частей: {zip_error}")
                print(f"[ERROR ZIP] Пробуем fallback метод...")
                
                # Fallback: создаем один большой ZIP архив (старый метод)
                try:
                    import zipfile
                    import io
                    
                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                        for file_data in files:
                            file_id, file_unique_id, file_size, file_type = file_data
                            # Добавляем файл в архив (используем file_id как имя файла)
                            zip_file.writestr(f"{file_id}_{file_type}", f"File ID: {file_id}")
                    
                    zip_buffer.seek(0)
                    
                    # Формируем подпись архива
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

async def handle_processing_button(callback: CallbackQuery):
    await callback.answer("⏳ Идет обработка, пожалуйста, подождите...", show_alert=True)

async def create_zip_parts(files, archive_name, bot, max_size_mb=45):
    """
    Создает ZIP архивы, разбивая файлы на части по размеру
    
    Args:
        files: Список файлов для архивирования
        archive_name: Базовое имя архива
        bot: Экземпляр бота для скачивания файлов
        max_size_mb: Максимальный размер части в МБ (по умолчанию 45MB)
    
    Returns:
        list: Список кортежей (part_data, part_filename, part_number, total_parts)
    """
    print(f"[DEBUG BOT CHECK] bot в create_zip_parts: {type(bot)}")
    print(f"[DEBUG BOT CHECK] bot token: {bot.token[:10]}...")
    max_size_bytes = max_size_mb * 1024 * 1024  # Конвертируем в байты
    
    print(f"[DEBUG ZIP SPLIT] Создаем архивы с максимальным размером {max_size_mb} MB")
    print(f"[DEBUG ZIP SPLIT] Всего файлов для архивирования: {len(files)}")
    
    # Разбиваем файлы на группы для создания нескольких архивов
    parts = []
    current_part = 1
    current_files = []
    current_size = 0
    
    for i, (file_id, file_unique_id, file_size, file_type) in enumerate(files):
        # Проверяем, поместится ли файл в текущую часть
        if current_size + file_size > max_size_bytes and current_files:
            # Создаем архив из текущих файлов (БЕЗ текущего файла)
            part_data = await create_zip_from_files(current_files, current_part, bot)
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
        part_data = await create_zip_from_files(current_files, current_part, bot)
        part_filename = f"{archive_name}_{current_part}.zip"
        parts.append((part_data, part_filename, current_part, 0))
        
        print(f"[DEBUG ZIP SPLIT] Создан архив {current_part}: {len(part_data) / (1024*1024):.2f} MB, файлов: {len(current_files)}")
    
    # Обновляем total_parts для всех частей
    total_parts = len(parts)
    for i in range(len(parts)):
        parts[i] = (parts[i][0], parts[i][1], parts[i][2], total_parts)
    
    print(f"[DEBUG ZIP SPLIT] Создано {total_parts} частей")
    return parts

async def create_zip_from_files(files, part_number, bot):
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

