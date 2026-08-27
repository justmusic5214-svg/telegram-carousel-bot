import os
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL"]

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Фото пользователей
photos = {}

# Карусели, которые уже опубликованы
carousels = {}


# =========================================================
# HTTP-СЕРВЕР ДЛЯ RENDER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Telegram carousel bot is running")

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler
    )

    print(f"HTTP server started on port {port}")

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


def send_message(chat_id, text, reply_markup=None):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)

    return telegram("sendMessage", data)


def answer_callback(callback_id):

    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id
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
            "reply_markup": json.dumps(reply_markup)
        }
    )


# =========================================================
# КНОПКИ КАРУСЕЛИ
# =========================================================

def carousel_keyboard(carousel_id, index, total):

    buttons = []

    if index > 0:
        buttons.append(
            {
                "text": "⬅️",
                "callback_data": f"prev:{carousel_id}"
            }
        )

    buttons.append(
        {
            "text": f"{index + 1} / {total}",
            "callback_data": f"info:{carousel_id}"
        }
    )

    if index < total - 1:
        buttons.append(
            {
                "text": "➡️",
                "callback_data": f"next:{carousel_id}"
            }
        )

    return {
        "inline_keyboard": [
            buttons
        ]
    }


# =========================================================
# СОЗДАНИЕ КАРУСЕЛИ
# =========================================================

def publish_carousel(user_chat_id):

    user_photos = photos.get(user_chat_id, [])

    if len(user_photos) < 2:
        send_message(
            user_chat_id,
            "Нужно минимум 2 фотографии."
        )
        return

    if len(user_photos) > 9:
        user_photos = user_photos[:9]

    # Уникальный ID карусели
    import time

    carousel_id = str(
        int(time.time() * 1000)
    )

    carousels[carousel_id] = {
        "photos": user_photos,
        "index": 0
    }

    first_photo = user_photos[0]

    caption = (
        f"📸 1 / {len(user_photos)}"
    )

    keyboard = carousel_keyboard(
        carousel_id,
        0,
        len(user_photos)
    )

    try:

        result = telegram(
            "sendPhoto",
            {
                "chat_id": CHANNEL,
                "photo": first_photo,
                "caption": caption,
                "reply_markup": json.dumps(keyboard)
            }
        )

        # Сохраняем ID сообщения в канале
        carousels[carousel_id]["message_id"] = result["message_id"]

        send_message(
            user_chat_id,
            "✅ Карусель опубликована!\n\n"
            f"Фотографий: {len(user_photos)}\n"
            "В канале используй кнопки ⬅️ ➡️ для перелистывания."
        )

        # Очищаем фотографии пользователя
        photos[user_chat_id] = []

    except Exception as e:

        print("Publish error:", e)

        send_message(
            user_chat_id,
            "❌ Не удалось опубликовать карусель.\n\n"
            "Проверь, что бот является администратором канала."
        )


# =========================================================
# ПЕРЕЛИСТЫВАНИЕ
# =========================================================

def change_carousel(callback_query):

    callback_id = callback_query["id"]

    data = callback_query.get("data", "")

    message = callback_query.get("message")

    if not message:
        answer_callback(callback_id)
        return

    parts = data.split(":")

    if len(parts) != 2:
        answer_callback(callback_id)
        return

    action = parts[0]
    carousel_id = parts[1]

    carousel = carousels.get(carousel_id)

    if not carousel:
        answer_callback(callback_id)
        return

    photos_list = carousel["photos"]

    current_index = carousel["index"]

    if action == "next":

        if current_index < len(photos_list) - 1:
            current_index += 1

    elif action == "prev":

        if current_index > 0:
            current_index -= 1

    elif action == "info":

        answer_callback(
            callback_id
        )

        return

    carousel["index"] = current_index

    file_id = photos_list[current_index]

    caption = (
        f"📸 {current_index + 1} / "
        f"{len(photos_list)}"
    )

    keyboard = carousel_keyboard(
        carousel_id,
        current_index,
        len(photos_list)
    )

    try:

        edit_message_media(
            CHANNEL,
            message["message_id"],
            file_id,
            caption,
            keyboard
        )

    except Exception as e:

        print("Carousel edit error:", e)

    answer_callback(callback_id)


# =========================================================
# ОБРАБОТКА СООБЩЕНИЙ
# =========================================================

def process_update(update):

    # -----------------------------------------------------
    # КНОПКА КАРУСЕЛИ
    # -----------------------------------------------------

    callback_query = update.get("callback_query")

    if callback_query:

        change_carousel(
            callback_query
        )

        return

    # -----------------------------------------------------
    # ОБЫЧНОЕ СООБЩЕНИЕ
    # -----------------------------------------------------

    message = update.get("message")

    if not message:
        return

    chat_id = message["chat"]["id"]

    text = message.get("text", "")

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
            "После этого напиши /publish.\n\n"
            "Я опубликую их как одну карусель "
            "с кнопками ⬅️ ➡️."
        )

        return

    # -----------------------------------------------------
    # CLEAR
    # -----------------------------------------------------

    if text == "/clear":

        photos[chat_id] = []

        send_message(
            chat_id,
            "🗑 Фотографии очищены."
        )

        return

    # -----------------------------------------------------
    # PUBLISH
    # -----------------------------------------------------

    if text == "/publish":

        publish_carousel(
            chat_id
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

        if len(photos[chat_id]) >= 9:

            send_message(
                chat_id,
                "⚠️ Максимум 9 фотографий."
            )

            return

        # Берём самое большое доступное фото
        file_id = message["photo"][-1]["file_id"]

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
            "или написать /publish."
        )

        return


# =========================================================
# ПОЛУЧЕНИЕ ОБНОВЛЕНИЙ
# =========================================================

def run_bot():

    offset = None

    print("Telegram carousel bot started.")

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


# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == "__main__":

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    run_bot()
