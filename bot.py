import os
import time
import json
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests
import psycopg


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL"]
DATABASE_URL = os.environ["DATABASE_URL"]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

MOSCOW = ZoneInfo("Europe/Moscow")

MAX_PHOTOS = 9


# =========================================================
# ВРЕМЕННЫЕ ДАННЫЕ
# =========================================================

user_photos = {}

waiting_for_schedule = set()

carousels = {}


# =========================================================
# DATABASE
# =========================================================

def db_connect():
    return psycopg.connect(DATABASE_URL)


def init_database():

    print("Connecting to PostgreSQL...")

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id BIGSERIAL PRIMARY KEY,
                    user_chat_id BIGINT NOT NULL,
                    photos JSONB NOT NULL,
                    publish_at TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

        conn.commit()

    print("PostgreSQL connected.")
    print("Database table is ready.")


def save_scheduled_post(
    user_chat_id,
    photo_list,
    publish_at
):

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO scheduled_posts
                    (user_chat_id, photos, publish_at)
                VALUES
                    (%s, %s, %s)
                RETURNING id
                """,
                (
                    user_chat_id,
                    json.dumps(photo_list),
                    publish_at
                )
            )

            post_id = cur.fetchone()[0]

        conn.commit()

    return post_id


def get_scheduled_posts():

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    user_chat_id,
                    photos,
                    publish_at
                FROM scheduled_posts
                WHERE publish_at <= NOW()
                ORDER BY publish_at ASC
                """
            )

            rows = cur.fetchall()

    return rows


def get_future_posts():

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    id,
                    user_chat_id,
                    photos,
                    publish_at
                FROM scheduled_posts
                WHERE publish_at > NOW()
                ORDER BY publish_at ASC
                """
            )

            rows = cur.fetchall()

    return rows


def delete_scheduled_post(post_id):

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                DELETE FROM scheduled_posts
                WHERE id = %s
                """,
                (post_id,)
            )

        conn.commit()


def cancel_scheduled_post(post_id):

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                DELETE FROM scheduled_posts
                WHERE id = %s
                RETURNING id
                """,
                (post_id,)
            )

            deleted = cur.fetchone()

        conn.commit()

    return deleted is not None


# =========================================================
# HTTP SERVER FOR RENDER
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
        ("0.0.0.0", port),
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

        raise Exception(result)

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


def answer_callback(
    callback_id
):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
        }
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


def edit_message_media(
    chat_id,
    message_id,
    file_id,
    caption,
    reply_markup
):

    media = {
        "type": "photo",
        "media": file_id,
        "caption": caption
    }

    return telegram(
        "editMessageMedia",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "media": json.dumps(media),
            "reply_markup": json.dumps(
                reply_markup
            )
        }
    )


# =========================================================
# CAROUSEL KEYBOARD
# =========================================================

def carousel_keyboard(
    carousel_id,
    index,
    total
):

    buttons = []

    if index > 0:

        buttons.append(
            {
                "text": "⬅️",
                "callback_data":
                    f"prev:{carousel_id}"
            }
        )

    buttons.append(
        {
            "text": f"{index + 1} / {total}",
            "callback_data":
                f"info:{carousel_id}"
        }
    )

    if index < total - 1:

        buttons.append(
            {
                "text": "➡️",
                "callback_data":
                    f"next:{carousel_id}"
            }
        )

    return {
        "inline_keyboard": [
            buttons
        ]
    }


# =========================================================
# PUBLISH CAROUSEL
# =========================================================

def publish_carousel(
    photo_list
):

    if len(photo_list) < 2:

        return False

    photo_list = photo_list[
        :MAX_PHOTOS
    ]

    carousel_id = str(
        int(time.time() * 1000)
    )

    carousels[carousel_id] = {
        "photos": photo_list,
        "index": 0
    }

    caption = (
        f"📸 1 / {len(photo_list)}"
    )

    keyboard = carousel_keyboard(
        carousel_id,
        0,
        len(photo_list)
    )

    try:

        result = send_photo(
            CHANNEL,
            photo_list[0],
            caption,
            keyboard
        )

        carousels[carousel_id][
            "message_id"
        ] = result["message_id"]

        return True

    except Exception as e:

        print(
            "Publish error:",
            e
        )

        return False


# =========================================================
# CAROUSEL NAVIGATION
# =========================================================

def change_carousel(
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
    carousel_id = parts[1]

    carousel = carousels.get(
        carousel_id
    )

    if not carousel:

        answer_callback(
            callback_id
        )

        return

    photo_list = carousel["photos"]

    index = carousel["index"]

    if action == "next":

        if index < len(photo_list) - 1:

            index += 1

    elif action == "prev":

        if index > 0:

            index -= 1

    elif action == "info":

        answer_callback(
            callback_id
        )

        return

    carousel["index"] = index

    caption = (
        f"📸 {index + 1} / "
        f"{len(photo_list)}"
    )

    keyboard = carousel_keyboard(
        carousel_id,
        index,
        len(photo_list)
    )

    try:

        edit_message_media(
            CHANNEL,
            message["message_id"],
            photo_list[index],
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
# SCHEDULE
# =========================================================

def schedule_carousel(
    chat_id,
    date_text
):

    try:

        local_dt = datetime.strptime(
            date_text.strip(),
            "%d.%m.%Y %H:%M"
        )

        local_dt = local_dt.replace(
            tzinfo=MOSCOW
        )

        utc_dt = local_dt.astimezone(
            timezone.utc
        )

    except ValueError:

        send_message(
            chat_id,
            "❌ Неверный формат даты.\n\n"
            "Используй:\n"
            "27.08.2026 18:30\n\n"
            "🕐 Время указывается по Москве (МСК)."
        )

        return

    now = datetime.now(
        timezone.utc
    )

    if utc_dt <= now:

        send_message(
            chat_id,
            "❌ Это время уже прошло.\n\n"
            "Укажи будущую дату и время."
        )

        return

    photo_list = user_photos.get(
        chat_id,
        []
    )

    if len(photo_list) < 2:

        send_message(
            chat_id,
            "❌ Нужно минимум 2 фотографии."
        )

        return

    photo_list = photo_list[
        :MAX_PHOTOS
    ]

    try:

        post_id = save_scheduled_post(
            chat_id,
            photo_list,
            utc_dt
        )

    except Exception as e:

        print(
            "Database save error:",
            e
        )

        send_message(
            chat_id,
            "❌ Не удалось сохранить расписание "
            "в базу данных."
        )

        return

    user_photos[chat_id] = []

    send_message(
        chat_id,
        "✅ Карусель запланирована!\n\n"
        f"📅 {local_dt.strftime('%d.%m.%Y')}\n"
        f"🕐 {local_dt.strftime('%H:%M')} МСК\n"
        f"📸 Фотографий: {len(photo_list)}\n"
        f"🆔 №{post_id}"
    )


# =========================================================
# SCHEDULE LIST
# =========================================================

def send_schedule_list(
    chat_id
):

    try:

        rows = get_future_posts()

    except Exception as e:

        print(
            "Schedule list database error:",
            e
        )

        send_message(
            chat_id,
            "❌ Не удалось получить расписание."
        )

        return

    if not rows:

        send_message(
            chat_id,
            "📅 Запланированных публикаций нет."
        )

        return

    text = (
        "📅 Запланированные публикации:\n\n"
    )

    for (
        post_id,
        user_chat_id,
        photo_json,
        publish_at
    ) in rows:

        if isinstance(
            photo_json,
            str
        ):

            photo_list = json.loads(
                photo_json
            )

        else:

            photo_list = photo_json

        moscow_dt = publish_at.astimezone(
            MOSCOW
        )

        text += (
            f"🆔 {post_id}\n"
            f"📅 {moscow_dt.strftime('%d.%m.%Y')}\n"
            f"🕐 {moscow_dt.strftime('%H:%M')} МСК\n"
            f"📸 {len(photo_list)} фото\n\n"
        )

    send_message(
        chat_id,
        text
    )


# =========================================================
# CANCEL SCHEDULED POST
# =========================================================

def handle_cancel(
    chat_id,
    text
):

    parts = text.split()

    if len(parts) != 2:

        send_message(
            chat_id,
            "❌ Неверная команда.\n\n"
            "Используй:\n"
            "/cancel 1"
        )

        return

    if not parts[1].isdigit():

        send_message(
            chat_id,
            "❌ ID должен быть числом.\n\n"
            "Например:\n"
            "/cancel 1"
        )

        return

    post_id = int(parts[1])

    try:

        deleted = cancel_scheduled_post(
            post_id
        )

    except Exception as e:

        print(
            "Cancel error:",
            e
        )

        send_message(
            chat_id,
            "❌ Ошибка при удалении публикации."
        )

        return

    if deleted:

        send_message(
            chat_id,
            f"🗑 Публикация №{post_id} "
            "удалена из расписания."
        )

    else:

        send_message(
            chat_id,
            f"❌ Публикация №{post_id} "
            "не найдена."
        )


# =========================================================
# SCHEDULER
# =========================================================

def scheduler_loop():

    print(
        "Scheduler started."
    )

    print(
        "Timezone: Europe/Moscow"
    )

    while True:

        try:

            rows = get_scheduled_posts()

            for (
                post_id,
                chat_id,
                photo_json,
                publish_at
            ) in rows:

                if isinstance(
                    photo_json,
                    str
                ):

                    photo_list = json.loads(
                        photo_json
                    )

                else:

                    photo_list = photo_json

                print(
                    f"Publishing scheduled post #{post_id}"
                )

                success = publish_carousel(
                    photo_list
                )

                if success:

                    delete_scheduled_post(
                        post_id
                    )

                    moscow_dt = publish_at.astimezone(
                        MOSCOW
                    )

                    send_message(
                        chat_id,
                        "✅ Запланированная "
                        "карусель опубликована!\n\n"
                        f"📅 {moscow_dt.strftime('%d.%m.%Y')}\n"
                        f"🕐 {moscow_dt.strftime('%H:%M')} МСК\n"
                        f"🆔 №{post_id}"
                    )

                else:

                    print(
                        f"Failed to publish "
                        f"scheduled post #{post_id}"
                    )

        except Exception as e:

            print(
                "Scheduler error:",
                e
            )

        time.sleep(10)


# =========================================================
# UPDATE PROCESSING
# =========================================================

def process_update(
    update
):

    # -----------------------------------------------------
    # CALLBACK
    # -----------------------------------------------------

    callback_query = update.get(
        "callback_query"
    )

    if callback_query:

        change_carousel(
            callback_query
        )

        return

    # -----------------------------------------------------
    # MESSAGE
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
            "/cancel ID — удалить публикацию\n"
            "/clear — очистить фотографии\n\n"
            "🕐 Время — московское (МСК)."
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
            "🗑 Фотографии очищены."
        )

        return

    # -----------------------------------------------------
    # SCHEDULE LIST
    # -----------------------------------------------------

    if text == "/schedule_list":

        send_schedule_list(
            chat_id
        )

        return

    # -----------------------------------------------------
    # CANCEL
    # -----------------------------------------------------

    if text.startswith("/cancel"):

        handle_cancel(
            chat_id,
            text
        )

        return

    # -----------------------------------------------------
    # SCHEDULE
    # -----------------------------------------------------

    if text == "/schedule":

        photo_list = user_photos.get(
            chat_id,
            []
        )

        if len(photo_list) < 2:

            send_message(
                chat_id,
                "❌ Сначала отправь минимум "
                "2 фотографии."
            )

            return

        waiting_for_schedule.add(
            chat_id
        )

        send_message(
            chat_id,
            "📅 Введи дату и время публикации.\n\n"
            "Например:\n"
            "28.08.2026 18:30\n\n"
            "🕐 Время — по Москве (МСК)."
        )

        return

    # -----------------------------------------------------
    # WAITING FOR DATE
    # -----------------------------------------------------

    if chat_id in waiting_for_schedule:

        waiting_for_schedule.discard(
            chat_id
        )

        schedule_carousel(
            chat_id,
            text
        )

        return

    # -----------------------------------------------------
    # PUBLISH NOW
    # -----------------------------------------------------

    if text == "/publish":

        photo_list = user_photos.get(
            chat_id,
            []
        )

        if len(photo_list) < 2:

            send_message(
                chat_id,
                "❌ Нужно минимум 2 фотографии."
            )

            return

        success = publish_carousel(
            photo_list
        )

        if success:

            user_photos[chat_id] = []

            send_message(
                chat_id,
                "✅ Карусель опубликована."
            )

        else:

            send_message(
                chat_id,
                "❌ Не удалось опубликовать карусель."
            )

        return

    # -----------------------------------------------------
    # PHOTO
    # -----------------------------------------------------

    if message.get("photo"):

        user_photos.setdefault(
            chat_id,
            []
        )

        if len(user_photos[chat_id]) >= MAX_PHOTOS:

            send_message(
                chat_id,
                "⚠️ Максимум 9 фотографий."
            )

            return

        file_id = message[
            "photo"
        ][-1]["file_id"]

        user_photos[chat_id].append(
            file_id
        )

        count = len(
            user_photos[chat_id]
        )

        send_message(
            chat_id,
            f"📷 Фото добавлено: {count}/9\n\n"
            "Можешь отправить ещё фотографии "
            "или написать:\n"
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
        "TELEGRAM CAROUSEL BOT"
    )

    print(
        "Бот запущен."
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

                time.sleep(5)

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
                "Telegram connection error:",
                e
            )

            time.sleep(5)


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    init_database()

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    scheduler_thread = threading.Thread(
        target=scheduler_loop,
        daemon=True
    )

    scheduler_thread.start()

    run_bot()
