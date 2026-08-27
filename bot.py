import os
import json
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import psycopg2


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL"]
DATABASE_URL = os.environ["DATABASE_URL"]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Московское время
MOSCOW = ZoneInfo("Europe/Moscow")

# Максимум фотографий
MAX_PHOTOS = 9


# =========================================================
# ВРЕМЕННЫЕ ДАННЫЕ ПОЛЬЗОВАТЕЛЕЙ
# =========================================================

# Фотографии, которые пользователь ещё не опубликовал
user_photos = {}

# Пользователи, от которых ждём дату/время
waiting_for_schedule = set()


# =========================================================
# DATABASE
# =========================================================

def get_db():

    return psycopg2.connect(
        DATABASE_URL,
        connect_timeout=10
    )


def init_database():

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id BIGSERIAL PRIMARY KEY,
                user_chat_id BIGINT NOT NULL,
                photos TEXT NOT NULL,
                publish_at TIMESTAMPTZ NOT NULL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                message_id BIGINT,
                carousel_index INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

        connection.commit()

        cursor.close()

        print("Database initialized.")

    finally:

        connection.close()


# =========================================================
# HTTP SERVER ДЛЯ RENDER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )

        self.end_headers()

        self.wfile.write(
            b"Telegram carousel bot is running"
        )

    def log_message(self, format, *args):
        pass


def start_web_server():

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    server = HTTPServer(
        (
            "0.0.0.0",
            port
        ),
        HealthHandler
    )

    print(
        f"HTTP server started on port {port}"
    )

    server.serve_forever()


# =========================================================
# TELEGRAM API
# =========================================================

def telegram(
    method,
    data=None
):

    response = requests.post(
        f"{API}/{method}",
        data=data or {},
        timeout=60
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):

        raise Exception(
            result
        )

    return result["result"]


def send_message(
    chat_id,
    text,
    reply_markup=None
):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:

        data["reply_markup"] = json.dumps(
            reply_markup
        )

    return telegram(
        "sendMessage",
        data
    )


def send_photo(
    chat_id,
    photo,
    caption,
    reply_markup
):

    return telegram(
        "sendPhoto",
        {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "reply_markup": json.dumps(
                reply_markup
            )
        }
    )


def answer_callback(
    callback_id
):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_id
        }
    )


def edit_message_media(
    chat_id,
    message_id,
    photo,
    caption,
    reply_markup
):

    media = {
        "type": "photo",
        "media": photo,
        "caption": caption
    }

    return telegram(
        "editMessageMedia",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "media": json.dumps(
                media
            ),
            "reply_markup": json.dumps(
                reply_markup
            )
        }
    )


# =========================================================
# КНОПКИ КАРУСЕЛИ
# =========================================================

def carousel_keyboard(
    post_id,
    index,
    total
):

    buttons = []

    if index > 0:

        buttons.append(
            {
                "text": "⬅️",
                "callback_data":
                    f"prev:{post_id}"
            }
        )

    buttons.append(
        {
            "text":
                f"{index + 1} / {total}",
            "callback_data":
                f"info:{post_id}"
        }
    )

    if index < total - 1:

        buttons.append(
            {
                "text": "➡️",
                "callback_data":
                    f"next:{post_id}"
            }
        )

    return {
        "inline_keyboard": [
            buttons
        ]
    }


# =========================================================
# СОЗДАНИЕ ЗАПЛАНИРОВАННОГО ПОСТА
# =========================================================

def save_scheduled_post(
    user_chat_id,
    photos,
    publish_at
):

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO scheduled_posts
            (
                user_chat_id,
                photos,
                publish_at
            )
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (
                user_chat_id,
                json.dumps(photos),
                publish_at
            )
        )

        post_id = cursor.fetchone()[0]

        connection.commit()

        cursor.close()

        return post_id

    finally:

        connection.close()


# =========================================================
# ПОЛУЧЕНИЕ ЗАПЛАНИРОВАННЫХ ПОСТОВ
# =========================================================

def get_ready_posts():

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                user_chat_id,
                photos,
                publish_at
            FROM scheduled_posts
            WHERE status = 'scheduled'
              AND publish_at <= NOW()
            ORDER BY publish_at ASC
            """
        )

        rows = cursor.fetchall()

        cursor.close()

        return rows

    finally:

        connection.close()


# =========================================================
# ПОЛУЧЕНИЕ ВСЕХ ЗАПЛАНИРОВАННЫХ
# =========================================================

def get_scheduled_posts():

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id,
                photos,
                publish_at
            FROM scheduled_posts
            WHERE status = 'scheduled'
            ORDER BY publish_at ASC
            """
        )

        rows = cursor.fetchall()

        cursor.close()

        return rows

    finally:

        connection.close()


# =========================================================
# ОБНОВЛЕНИЕ ПОСТА
# =========================================================

def update_post_after_publish(
    post_id,
    message_id
):

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE scheduled_posts
            SET
                status = 'published',
                message_id = %s,
                carousel_index = 0
            WHERE id = %s
            """,
            (
                message_id,
                post_id
            )
        )

        connection.commit()

        cursor.close()

    finally:

        connection.close()


def update_carousel_index(
    post_id,
    index
):

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE scheduled_posts
            SET carousel_index = %s
            WHERE id = %s
            """,
            (
                index,
                post_id
            )
        )

        connection.commit()

        cursor.close()

    finally:

        connection.close()


# =========================================================
# ПУБЛИКАЦИЯ КАРУСЕЛИ
# =========================================================

def publish_scheduled_post(
    post
):

    post_id = post[0]
    user_chat_id = post[1]
    photos_json = post[2]

    photos = json.loads(
        photos_json
    )

    if len(photos) < 2:

        print(
            f"Post {post_id}: "
            "not enough photos"
        )

        return False

    if len(photos) > MAX_PHOTOS:

        photos = photos[:MAX_PHOTOS]

    keyboard = carousel_keyboard(
        post_id,
        0,
        len(photos)
    )

    caption = (
        f"📸 1 / {len(photos)}"
    )

    try:

        result = send_photo(
            CHANNEL,
            photos[0],
            caption,
            keyboard
        )

        message_id = result[
            "message_id"
        ]

        update_post_after_publish(
            post_id,
            message_id
        )

        send_message(
            user_chat_id,
            "✅ Запланированная "
            "карусель опубликована!\n\n"
            f"📸 Фотографий: {len(photos)}"
        )

        print(
            f"Post {post_id} "
            "published successfully."
        )

        return True

    except Exception as e:

        print(
            f"Post {post_id} "
            f"publish error: {e}"
        )

        return False


# =========================================================
# ПЛАНИРОВЩИК
# =========================================================

def scheduler_loop():

    print(
        "Scheduler started."
    )

    while True:

        try:

            posts = get_ready_posts()

            for post in posts:

                publish_scheduled_post(
                    post
                )

        except Exception as e:

            print(
                "Scheduler error:",
                e
            )

        # Проверяем каждые 10 секунд
        time.sleep(10)


# =========================================================
# ПЕРЕЛИСТЫВАНИЕ КАРУСЕЛИ
# =========================================================

def handle_carousel_callback(
    callback_query
):

    callback_id = callback_query["id"]

    data = callback_query.get(
        "data",
        ""
    )

    message = callback_query.get(
        "message"
    )

    if not message:

        answer_callback(
            callback_id
        )

        return

    parts = data.split(":")

    if len(parts) != 2:

        answer_callback(
            callback_id
        )

        return

    action = parts[0]

    try:

        post_id = int(
            parts[1]
        )

    except ValueError:

        answer_callback(
            callback_id
        )

        return

    connection = get_db()

    try:

        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                photos,
                carousel_index,
                message_id
            FROM scheduled_posts
            WHERE id = %s
              AND status = 'published'
            """,
            (
                post_id,
            )
        )

        row = cursor.fetchone()

        cursor.close()

    finally:

        connection.close()

    if not row:

        answer_callback(
            callback_id
        )

        return

    photos = json.loads(
        row[0]
    )

    current_index = row[1]

    if action == "next":

        if current_index < (
            len(photos) - 1
        ):

            current_index += 1

    elif action == "prev":

        if current_index > 0:

            current_index -= 1

    elif action == "info":

        answer_callback(
            callback_id
        )

        return

    update_carousel_index(
        post_id,
        current_index
    )

    keyboard = carousel_keyboard(
        post_id,
        current_index,
        len(photos)
    )

    caption = (
        f"📸 {current_index + 1} / "
        f"{len(photos)}"
    )

    try:

        edit_message_media(
            CHANNEL,
            message["message_id"],
            photos[current_index],
            caption,
            keyboard
        )

    except Exception as e:

        print(
            "Carousel edit error:",
            e
        )

    answer_callback(
        callback_id
    )


# =========================================================
# РАСПИСАНИЕ
# =========================================================

def show_schedule(
    chat_id
):

    posts = get_scheduled_posts()

    if not posts:

        send_message(
            chat_id,
            "📅 Запланированных "
            "публикаций нет."
        )

        return

    text = (
        "📅 Запланированные публикации:\n\n"
    )

    for number, row in enumerate(
        posts,
        start=1
    ):

        post_id = row[0]

        photos = json.loads(
            row[1]
        )

        publish_at = row[2]

        if publish_at.tzinfo is None:

            publish_at = publish_at.replace(
                tzinfo=ZoneInfo("UTC")
            )

        moscow_time = publish_at.astimezone(
            MOSCOW
        )

        text += (
            f"{number}. "
            f"ID {post_id}\n"
            f"📅 "
            f"{moscow_time.strftime('%d.%m.%Y')}\n"
            f"🕐 "
            f"{moscow_time.strftime('%H:%M')} МСК\n"
            f"📸 "
            f"{len(photos)} фото\n\n"
        )

    send_message(
        chat_id,
        text
    )


# =========================================================
# ОБРАБОТКА ЗАПЛАНИРОВАНИЯ
# =========================================================

def create_schedule(
    chat_id,
    date_text
):

    try:

        local_datetime = datetime.strptime(
            date_text.strip(),
            "%d.%m.%Y %H:%M"
        )

        # Введённое время считаем московским
        local_datetime = local_datetime.replace(
            tzinfo=MOSCOW
        )

    except ValueError:

        send_message(
            chat_id,
            "❌ Неверный формат даты.\n\n"
            "Нужно написать:\n"
            "27.08.2026 18:30\n\n"
            "🕐 Время — московское (МСК)."
        )

        waiting_for_schedule.add(
            chat_id
        )

        return

    now = datetime.now(
        MOSCOW
    )

    if local_datetime <= now:

        send_message(
            chat_id,
            "❌ Это время уже прошло.\n\n"
            "Укажи будущую дату и время МСК."
        )

        waiting_for_schedule.add(
            chat_id
        )

        return

    photos = user_photos.get(
        chat_id,
        []
    )

    if len(photos) < 2:

        send_message(
            chat_id,
            "❌ Нужно минимум 2 фотографии."
        )

        return

    if len(photos) > MAX_PHOTOS:

        photos = photos[:MAX_PHOTOS]

    post_id = save_scheduled_post(
        chat_id,
        photos,
        local_datetime
    )

    # Очищаем текущую подборку
    user_photos[chat_id] = []

    send_message(
        chat_id,
        "✅ Карусель запланирована!\n\n"
        f"🆔 Пост: {post_id}\n"
        f"📅 {local_datetime.strftime('%d.%m.%Y')}\n"
        f"🕐 {local_datetime.strftime('%H:%M')} МСК\n"
        f"📸 Фотографий: {len(photos)}"
    )


# =========================================================
# ОБРАБОТКА UPDATE
# =========================================================

def process_update(
    update
):

    # -----------------------------------------------------
    # КНОПКА КАРУСЕЛИ
    # -----------------------------------------------------

    callback_query = update.get(
        "callback_query"
    )

    if callback_query:

        handle_carousel_callback(
            callback_query
        )

        return

    # -----------------------------------------------------
    # СООБЩЕНИЕ
    # -----------------------------------------------------

    message = update.get(
        "message"
    )

    if not message:

        return

    chat_id = message[
        "chat"
    ]["id"]

    text = message.get(
        "text",
        ""
    ).strip()

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if text == "/start":

        user_photos.setdefault(
            chat_id,
            []
        )

        send_message(
            chat_id,
            "👋 Привет!\n\n"
            "Отправь от 2 до 9 фотографий.\n\n"
            "Команды:\n"
            "/publish — опубликовать сейчас\n"
            "/schedule — запланировать\n"
            "/schedule_list — расписание\n"
            "/clear — очистить фотографии\n\n"
            "🕐 Время планирования — МСК."
        )

        return

    # -----------------------------------------------------
    # CLEAR
    # -----------------------------------------------------

    if text == "/clear":

        user_photos[chat_id] = []

        waiting_for_schedule.discard(
            chat_id
        )

        send_message(
            chat_id,
            "🗑 Текущие фотографии очищены."
        )

        return

    # -----------------------------------------------------
    # СПИСОК РАСПИСАНИЯ
    # -----------------------------------------------------

    if text == "/schedule_list":

        show_schedule(
            chat_id
        )

        return

    # -----------------------------------------------------
    # SCHEDULE
    # -----------------------------------------------------

    if text == "/schedule":

        photos = user_photos.get(
            chat_id,
            []
        )

        if len(photos) < 2:

            send_message(
                chat_id,
                "❌ Сначала отправь "
                "минимум 2 фотографии."
            )

            return

        waiting_for_schedule.add(
            chat_id
        )

        send_message(
            chat_id,
            "📅 Введи дату и время.\n\n"
            "Пример:\n"
            "28.08.2026 18:30\n\n"
            "🕐 Время указывается "
            "по Москве (МСК)."
        )

        return

    # -----------------------------------------------------
    # ОЖИДАНИЕ ДАТЫ
    # -----------------------------------------------------

    if chat_id in waiting_for_schedule:

        waiting_for_schedule.discard(
            chat_id
        )

        create_schedule(
            chat_id,
            text
        )

        return

    # -----------------------------------------------------
    # PUBLISH
    # -----------------------------------------------------

    if text == "/publish":

        photos = user_photos.get(
            chat_id,
            []
        )

        if len(photos) < 2:

            send_message(
                chat_id,
                "❌ Нужно минимум 2 фотографии."
            )

            return

        if len(photos) > MAX_PHOTOS:

            photos = photos[:MAX_PHOTOS]

        # Для публикации сразу создаём запись
        # в БД, чтобы карусель тоже была постоянной

        connection = get_db()

        try:

            cursor = connection.cursor()

            cursor.execute(
                """
                INSERT INTO scheduled_posts
                (
                    user_chat_id,
                    photos,
                    publish_at,
                    status
                )
                VALUES
                (
                    %s,
                    %s,
                    NOW(),
                    'scheduled'
                )
                RETURNING id
                """,
                (
                    chat_id,
                    json.dumps(photos)
                )
            )

            post_id = cursor.fetchone()[0]

            connection.commit()

            cursor.close()

        finally:

            connection.close()

        user_photos[chat_id] = []

        send_message(
            chat_id,
            "⏳ Публикую карусель..."
        )

        return

    # -----------------------------------------------------
    # ФОТО
    # -----------------------------------------------------

    if message.get("photo"):

        user_photos.setdefault(
            chat_id,
            []
        )

        if len(
            user_photos[chat_id]
        ) >= MAX_PHOTOS:

            send_message(
                chat_id,
                "⚠️ Максимум 9 фотографий."
            )

            return

        file_id = message[
            "photo"
        ][-1]["file_id"]

        user_photos[
            chat_id
        ].append(
            file_id
        )

        count = len(
            user_photos[chat_id]
        )

        send_message(
            chat_id,
            f"📷 Фото добавлено: "
            f"{count}/9\n\n"
            "Можешь отправить ещё "
            "фотографии или написать:\n"
            "/publish — сейчас\n"
            "/schedule — запланировать"
        )

        return


# =========================================================
# TELEGRAM LONG POLLING
# =========================================================

def run_bot():

    offset = None

    print(
        "==================================="
    )

    print(
        " TELEGRAM CAROUSEL BOT"
    )

    print(
        " Бот запущен."
    )

    print(
        " Часовой пояс: Москва (UTC+3)"
    )

    print(
        "==================================="
    )

    while True:

        try:

            params = {
                "timeout": 30,
                "allowed_updates": [
                    "message",
                    "callback_query"
                ]
            }

            if offset is not None:

                params["offset"] = offset

            response = requests.get(
                f"{API}/getUpdates",
                params=params,
                timeout=40
            )

            response.raise_for_status()

            result = response.json()

            if not result.get("ok"):

                print(
                    "Telegram API error:",
                    result
                )

                time.sleep(3)

                continue

            for update in result.get(
                "result",
                []
            ):

                offset = (
                    update["update_id"] + 1
                )

                try:

                    process_update(
                        update
                    )

                except Exception as e:

                    print(
                        "Update processing error:",
                        e
                    )

        except Exception as e:

            print(
                "Connection error:",
                e
            )

            time.sleep(5)


# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == "__main__":

    # Проверяем базу
    try:

        init_database()

    except Exception as e:

        print(
            "DATABASE ERROR:",
            e
        )

        raise

    # Web server
    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    # Планировщик
    scheduler_thread = threading.Thread(
        target=scheduler_loop,
        daemon=True
    )

    scheduler_thread.start()

    # Telegram bot
    run_bot()
