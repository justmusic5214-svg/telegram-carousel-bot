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

waiting_for_description = set()

pending_schedule_dates = {}


# =========================================================
# DATABASE
# =========================================================

def db_connect():
    return psycopg.connect(DATABASE_URL)


def init_database():

    print(
        "Connecting to PostgreSQL...",
        flush=True
    )

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_posts (
                    id BIGSERIAL PRIMARY KEY,
                    user_chat_id BIGINT NOT NULL,
                    photos JSONB NOT NULL,
                    publish_at TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            # Добавляем описание в существующую таблицу.
            # Если колонка уже существует — ничего не произойдёт.
            cur.execute(
                """
                ALTER TABLE scheduled_posts
                ADD COLUMN IF NOT EXISTS description TEXT NOT NULL DEFAULT ''
                """
            )

        conn.commit()

    print(
        "PostgreSQL connected.",
        flush=True
    )

    print(
        "Database table is ready.",
        flush=True
    )


def save_scheduled_post(
    user_chat_id,
    photo_list,
    publish_at,
    description
):

    with db_connect() as conn:

        with conn.cursor() as cur:

            cur.execute(
                """
                INSERT INTO scheduled_posts
                    (
                        user_chat_id,
                        photos,
                        publish_at,
                        description
                    )
                VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s
                    )
                RETURNING id
                """,
                (
                    user_chat_id,
                    json.dumps(photo_list),
                    publish_at,
                    description
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
                    publish_at,
                    description
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
                    publish_at,
                    description
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
        f"HTTP server started on port {port}",
        flush=True
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


# =========================================================
# SEND MESSAGE
# =========================================================

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
            reply_markup,
            ensure_ascii=False
        )

    return telegram(
        "sendMessage",
        data
    )


# =========================================================
# PUBLISH TELEGRAM NATIVE SLIDESHOW
# =========================================================

def publish_carousel(
    photo_list,
    description=""
):

    if len(photo_list) < 2:

        print(
            "Publish error: minimum 2 photos required.",
            flush=True
        )

        return False

    photo_list = photo_list[:MAX_PHOTOS]

    try:

        slideshow_blocks = []

        for file_id in photo_list:

            slideshow_blocks.append(
                {
                    "type": "photo",
                    "photo": {
                        "type": "photo",
                        "media": file_id
                    }
                }
            )

        slideshow = {
            "type": "slideshow",
            "blocks": slideshow_blocks
        }

        # Если описание есть —
        # добавляем его как подпись к slideshow.
        if description:

            slideshow["caption"] = {
                "text": description
            }

        rich_message = {
            "blocks": [
                slideshow
            ]
        }

        result = telegram(
            "sendRichMessage",
            {
                "chat_id": CHANNEL,
                "rich_message": json.dumps(
                    rich_message,
                    ensure_ascii=False
                )
            }
        )

        print(
            "Native Telegram slideshow published:",
            result.get("message_id"),
            flush=True
        )

        return True

    except Exception as e:

        print(
            "Rich slideshow publish error:",
            repr(e),
            flush=True
        )

        return False


# =========================================================
# ASK DESCRIPTION
# =========================================================

def ask_for_description(
    chat_id
):

    waiting_for_description.add(
        chat_id
    )

    send_message(
        chat_id,
        "📝 Напиши описание к публикации.\n\n"
        "Текст будет размещён под фотографиями.\n\n"
        "Если описание не нужно — напиши:\n"
        "/skip"
    )


# =========================================================
# SAVE IMMEDIATE PUBLICATION DESCRIPTION
# =========================================================

def publish_with_description(
    chat_id,
    description
):

    photo_list = user_photos.get(
        chat_id,
        []
    )

    if len(photo_list) < 2:

        waiting_for_description.discard(
            chat_id
        )

        send_message(
            chat_id,
            "❌ Нужно минимум 2 фотографии."
        )

        return

    photo_list = photo_list[:MAX_PHOTOS]

    success = publish_carousel(
        photo_list,
        description
    )

    waiting_for_description.discard(
        chat_id
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
            "❌ Не удалось опубликовать "
            "карусель.\n\n"
            "Подробная ошибка есть в логах Render."
        )


# =========================================================
# SCHEDULE
# =========================================================

def schedule_carousel(
    chat_id,
    date_text,
    description
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
            "03.09.2026 18:30\n\n"
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

    photo_list = photo_list[:MAX_PHOTOS]

    try:

        post_id = save_scheduled_post(
            chat_id,
            photo_list,
            utc_dt,
            description
        )

    except Exception as e:

        print(
            "Database save error:",
            repr(e),
            flush=True
        )

        send_message(
            chat_id,
            "❌ Не удалось сохранить расписание "
            "в базу данных."
        )

        return

    user_photos[chat_id] = []

    pending_schedule_dates.pop(
        chat_id,
        None
    )

    send_message(
        chat_id,
        "✅ Карусель запланирована!\n\n"
        f"📅 {local_dt.strftime('%d.%m.%Y')}\n"
        f"🕐 {local_dt.strftime('%H:%M')} МСК\n"
        f"📸 Фотографий: {len(photo_list)}\n"
        f"📝 Описание: "
        f"{'есть' if description else 'нет'}\n"
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
            repr(e),
            flush=True
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
        publish_at,
        description
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
            f"📸 {len(photo_list)} фото\n"
            f"📝 Описание: "
            f"{'есть' if description else 'нет'}\n\n"
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

    post_id = int(
        parts[1]
    )

    try:

        deleted = cancel_scheduled_post(
            post_id
        )

    except Exception as e:

        print(
            "Cancel error:",
            repr(e),
            flush=True
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
        "Scheduler started.",
        flush=True
    )

    print(
        "Timezone: Europe/Moscow",
        flush=True
    )

    while True:

        try:

            rows = get_scheduled_posts()

            for (
                post_id,
                chat_id,
                photo_json,
                publish_at,
                description
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
                    f"Publishing scheduled post #{post_id}",
                    flush=True
                )

                success = publish_carousel(
                    photo_list,
                    description or ""
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
                        f"scheduled post #{post_id}",
                        flush=True
                    )

        except Exception as e:

            print(
                "Scheduler error:",
                repr(e),
                flush=True
            )

        time.sleep(10)


# =========================================================
# UPDATE PROCESSING
# =========================================================

def process_update(
    update
):

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


    # =====================================================
    # START
    # =====================================================

    if text == "/start":

        user_photos.setdefault(
            chat_id,
            []
        )

        waiting_for_schedule.discard(
            chat_id
        )

        waiting_for_description.discard(
            chat_id
        )

        pending_schedule_dates.pop(
            chat_id,
            None
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
            "После /publish или /schedule "
            "бот попросит описание.\n\n"
            "🕐 Время — московское (МСК)."
        )

        return


    # =====================================================
    # CLEAR
    # =====================================================

    if text == "/clear":

        user_photos[chat_id] = []

        waiting_for_schedule.discard(
            chat_id
        )

        waiting_for_description.discard(
            chat_id
        )

        pending_schedule_dates.pop(
            chat_id,
            None
        )

        send_message(
            chat_id,
            "🗑 Фотографии и данные публикации "
            "очищены."
        )

        return


    # =====================================================
    # SKIP DESCRIPTION
    # =====================================================

    if text == "/skip":

        if chat_id not in waiting_for_description:

            send_message(
                chat_id,
                "ℹ️ Сейчас описание не запрашивается."
            )

            return

        # -------------------------------------------------
        # Описание для обычной публикации
        # -------------------------------------------------

        if chat_id not in pending_schedule_dates:

            publish_with_description(
                chat_id,
                ""
            )

            return

        # -------------------------------------------------
        # Описание для запланированной публикации
        # -------------------------------------------------

        date_text = pending_schedule_dates.get(
            chat_id
        )

        waiting_for_description.discard(
            chat_id
        )

        schedule_carousel(
            chat_id,
            date_text,
            ""
        )

        return


    # =====================================================
    # SCHEDULE LIST
    # =====================================================

    if text == "/schedule_list":

        send_schedule_list(
            chat_id
        )

        return


    # =====================================================
    # CANCEL
    # =====================================================

    if text.startswith("/cancel"):

        handle_cancel(
            chat_id,
            text
        )

        return


    # =====================================================
    # SCHEDULE
    # =====================================================

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
            "03.09.2026 18:30\n\n"
            "🕐 Время — по Москве (МСК)."
        )

        return


    # =====================================================
    # WAITING FOR SCHEDULE DATE
    # =====================================================

    if chat_id in waiting_for_schedule:

        try:

            local_dt = datetime.strptime(
                text.strip(),
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
                "03.09.2026 18:30"
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

        waiting_for_schedule.discard(
            chat_id
        )

        pending_schedule_dates[chat_id] = text

        waiting_for_description.add(
            chat_id
        )

        send_message(
            chat_id,
            "📝 Теперь напиши описание "
            "к публикации.\n\n"
            "Оно будет размещено под "
            "слайдшоу.\n\n"
            "Если описание не нужно — "
            "напиши /skip"
        )

        return


    # =====================================================
    # WAITING FOR DESCRIPTION
    # =====================================================

    if chat_id in waiting_for_description:

        description = text

        # -------------------------------------------------
        # Описание для запланированной публикации
        # -------------------------------------------------

        if chat_id in pending_schedule_dates:

            date_text = pending_schedule_dates.get(
                chat_id
            )

            waiting_for_description.discard(
                chat_id
            )

            schedule_carousel(
                chat_id,
                date_text,
                description
            )

            return

        # -------------------------------------------------
        # Описание для публикации сейчас
        # -------------------------------------------------

        publish_with_description(
            chat_id,
            description
        )

        return


    # =====================================================
    # PUBLISH NOW
    # =====================================================

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

        ask_for_description(
            chat_id
        )

        return


    # =====================================================
    # PHOTO
    # =====================================================

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
            "/schedule — запланировать\n"
            "/clear — очистить"
        )

        return


# =========================================================
# TELEGRAM LONG POLLING
# =========================================================

def run_bot():

    offset = None

    print(
        "===================================",
        flush=True
    )

    print(
        "TELEGRAM CAROUSEL BOT",
        flush=True
    )

    print(
        "Native Telegram Rich Slideshow",
        flush=True
    )

    print(
        "Description support: ON",
        flush=True
    )

    print(
        "Timezone: Europe/Moscow",
        flush=True
    )

    print(
        "Бот запущен.",
        flush=True
    )

    print(
        "===================================",
        flush=True
    )

    while True:

        try:

            params = {
                "timeout": 30,
                "allowed_updates": [
                    "message"
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
                    result,
                    flush=True
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
                        repr(e),
                        flush=True
                    )

        except Exception as e:

            print(
                "Telegram connection error:",
                repr(e),
                flush=True
            )

            time.sleep(5)


# =========================================================
# MAIN
# =========================================================

if __name__ == "__main__":

    init_database()


    # -----------------------------------------------------
    # HTTP SERVER
    # -----------------------------------------------------

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()


    # -----------------------------------------------------
    # SCHEDULER
    # -----------------------------------------------------

    scheduler_thread = threading.Thread(
        target=scheduler_loop,
        daemon=True
    )

    scheduler_thread.start()


    # -----------------------------------------------------
    # BOT
    # -----------------------------------------------------

    run_bot()
