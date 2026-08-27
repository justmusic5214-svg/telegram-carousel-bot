import os
import json
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL"]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Москва
MOSCOW = ZoneInfo("Europe/Moscow")

# Максимум фотографий
MAX_PHOTOS = 9


# =========================================================
# ДАННЫЕ
# =========================================================

# Фото, которые пользователь сейчас собирает
photos = {}

# Состояние ожидания даты
schedule_waiting = set()

# Запланированные публикации
scheduled_posts = []

# Уже опубликованные карусели
carousels = {}


# =========================================================
# HTTP-СЕРВЕР ДЛЯ RENDER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(
            b"Telegram carousel bot is running"
        )

    def log_message(self, format, *args):
        pass


def start_web_server():

    port = int(
        os.environ.get("PORT", "10000")
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

def telegram(method, data=None):

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


def answer_callback(callback_id):

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
# КНОПКИ КАРУСЕЛИ
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
# ПУБЛИКАЦИЯ КАРУСЕЛИ
# =========================================================

def publish_carousel(
    user_chat_id,
    user_photos=None
):

    if user_photos is None:
        user_photos = photos.get(
            user_chat_id,
            []
        )

    if len(user_photos) < 2:

        send_message(
            user_chat_id,
            "❌ Нужно минимум 2 фотографии."
        )

        return

    if len(user_photos) > MAX_PHOTOS:

        user_photos = user_photos[
            :MAX_PHOTOS
        ]

    carousel_id = str(
        int(time.time() * 1000)
    )

    carousels[carousel_id] = {
        "photos": list(user_photos),
        "index": 0
    }

    caption = (
        f"📸 1 / {len(user_photos)}"
    )

    keyboard = carousel_keyboard(
        carousel_id,
        0,
        len(user_photos)
    )

    try:

        result = send_photo(
            CHANNEL,
            user_photos[0],
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
# ПЕРЕЛИСТЫВАНИЕ
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

    current_index = carousel[
        "index"
    ]

    if action == "next":

        if current_index < (
            len(photo_list) - 1
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

    carousel["index"] = (
        current_index
    )

    caption = (
        f"📸 {current_index + 1} / "
        f"{len(photo_list)}"
    )

    keyboard = carousel_keyboard(
        carousel_id,
        current_index,
        len(photo_list)
    )

    try:

        edit_message_media(
            CHANNEL,
            message["message_id"],
            photo_list[current_index],
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
# ПЛАНИРОВАНИЕ
# =========================================================

def schedule_carousel(
    user_chat_id,
    date_text
):

    try:

        # Ввод:
        # 27.08.2026 18:30

        scheduled_time = datetime.strptime(
            date_text.strip(),
            "%d.%m.%Y %H:%M"
        )

        # Считаем введённое время московским
        scheduled_time = scheduled_time.replace(
            tzinfo=MOSCOW
        )

    except ValueError:

        send_message(
            user_chat_id,
            "❌ Неверный формат.\n\n"
            "Используй:\n"
            "27.08.2026 18:30\n\n"
            "Время указывается по Москве (МСК)."
        )

        return

    now = datetime.now(
        MOSCOW
    )

    if scheduled_time <= now:

        send_message(
            user_chat_id,
            "❌ Это время уже прошло.\n\n"
            "Укажи будущую дату и время по Москве."
        )

        return

    user_photos = photos.get(
        user_chat_id,
        []
    )

    if len(user_photos) < 2:

        send_message(
            user_chat_id,
            "❌ Для планирования нужно "
            "минимум 2 фотографии."
        )

        return

    if len(user_photos) > MAX_PHOTOS:

        user_photos = user_photos[
            :MAX_PHOTOS
        ]

    scheduled_posts.append(
        {
            "user_chat_id": user_chat_id,
            "photos": list(user_photos),
            "publish_at": scheduled_time.isoformat()
        }
    )

    photos[user_chat_id] = []

    send_message(
        user_chat_id,
        "✅ Карусель запланирована!\n\n"
        f"📅 {scheduled_time.strftime('%d.%m.%Y')}\n"
        f"🕐 {scheduled_time.strftime('%H:%M')} МСК\n"
        f"📸 Фотографий: {len(user_photos)}"
    )


# =========================================================
# ПРОВЕРКА РАСПИСАНИЯ
# =========================================================

def scheduler_loop():

    print(
        "Scheduler started. Timezone: Moscow"
    )

    while True:

        try:

            now = datetime.now(
                MOSCOW
            )

            ready_posts = []

            for post in list(
                scheduled_posts
            ):

                publish_at = datetime.fromisoformat(
                    post["publish_at"]
                )

                if publish_at <= now:

                    ready_posts.append(
                        post
                    )

            for post in ready_posts:

                success = publish_carousel(
                    post["user_chat_id"],
                    post["photos"]
                )

                if success:

                    send_message(
                        post["user_chat_id"],
                        "✅ Запланированная "
                        "карусель опубликована "
                        "в канале."
                    )

                    scheduled_posts.remove(
                        post
                    )

                else:

                    print(
                        "Scheduled publication failed"
                    )

        except Exception as e:

            print(
                "Scheduler error:",
                e
            )

        time.sleep(10)


# =========================================================
# СПИСОК ЗАПЛАНИРОВАННЫХ
# =========================================================

def send_schedule_list(
    chat_id
):

    if not scheduled_posts:

        send_message(
            chat_id,
            "📅 Запланированных публикаций нет."
        )

        return

    text = (
        "📅 Запланированные публикации:\n\n"
    )

    for i, post in enumerate(
        scheduled_posts,
        start=1
    ):

        publish_at = datetime.fromisoformat(
            post["publish_at"]
        )

        count = len(
            post["photos"]
        )

        text += (
            f"{i}. "
            f"{publish_at.strftime('%d.%m.%Y %H:%M')} МСК — "
            f"{count} фото\n"
        )

    send_message(
        chat_id,
        text
    )


# =========================================================
# ОБРАБОТКА СООБЩЕНИЙ
# =========================================================

def process_update(update):

    callback_query = update.get(
        "callback_query"
    )

    if callback_query:

        change_carousel(
            callback_query
        )

        return

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

        photos.setdefault(
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
            "/schedule_list — посмотреть расписание\n"
            "/clear — очистить фотографии\n\n"
            "Время планирования — московское (МСК)."
        )

        return

    # -----------------------------------------------------
    # CLEAR
    # -----------------------------------------------------

    if text == "/clear":

        photos[chat_id] = []

        schedule_waiting.discard(
            chat_id
        )

        send_message(
            chat_id,
            "🗑 Фотографии очищены."
        )

        return

    # -----------------------------------------------------
    # СПИСОК РАСПИСАНИЯ
    # -----------------------------------------------------

    if text == "/schedule_list":

        send_schedule_list(
            chat_id
        )

        return

    # -----------------------------------------------------
    # SCHEDULE
    # -----------------------------------------------------

    if text == "/schedule":

        user_photos = photos.get(
            chat_id,
            []
        )

        if len(user_photos) < 2:

            send_message(
                chat_id,
                "❌ Сначала отправь минимум "
                "2 фотографии."
            )

            return

        schedule_waiting.add(
            chat_id
        )

        send_message(
            chat_id,
            "📅 Введи дату и время публикации.\n\n"
            "Формат:\n"
            "27.08.2026 18:30\n\n"
            "🕐 Время указывается по Москве (МСК)."
        )

        return

    # -----------------------------------------------------
    # ОЖИДАЕМ ДАТУ
    # -----------------------------------------------------

    if chat_id in schedule_waiting:

        schedule_waiting.discard(
            chat_id
        )

        schedule_carousel(
            chat_id,
            text
        )

        return

    # -----------------------------------------------------
    # PUBLISH
    # -----------------------------------------------------

    if text == "/publish":

        user_photos = photos.get(
            chat_id,
            []
        )

        if len(user_photos) < 2:

            send_message(
                chat_id,
                "❌ Нужно минимум 2 фотографии."
            )

            return

        success = publish_carousel(
            chat_id
        )

        if success:

            photos[chat_id] = []

            send_message(
                chat_id,
                "✅ Карусель опубликована."
            )

        else:

            send_message(
                chat_id,
                "❌ Не удалось опубликовать.\n\n"
                "Проверь права бота в канале."
            )

        return

    # -----------------------------------------------------
    # ФОТО
    # -----------------------------------------------------

    if message.get("photo"):

        photos.setdefault(
            chat_id,
            []
        )

        if len(photos[chat_id]) >= MAX_PHOTOS:

            send_message(
                chat_id,
                "⚠️ Максимум 9 фотографий."
            )

            return

        file_id = message[
            "photo"
        ][-1]["file_id"]

        photos[chat_id].append(
            file_id
        )

        count = len(
            photos[chat_id]
        )

        send_message(
            chat_id,
            f"📷 Фото добавлено: {count}/9\n\n"
            "Можешь отправить ещё фотографии "
            "или написать /publish или /schedule."
        )

        return


# =========================================================
# TELEGRAM LONG POLLING
# =========================================================

def run_bot():

    offset = None

    print(
        "Telegram carousel bot started."
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
