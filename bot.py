import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL = os.environ.get("CHANNEL")

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


# =========================
# ПРОСТОЙ HTTP-СЕРВЕР ДЛЯ RENDER
# =========================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Telegram carousel bot is running")

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"HTTP server started on port {port}")
    server.serve_forever()


# =========================
# TELEGRAM API
# =========================

def telegram(method, data=None):
    url = f"{API_URL}/{method}"

    response = requests.post(
        url,
        data=data or {},
        timeout=60
    )

    response.raise_for_status()
    return response.json()


def send_message(chat_id, text):
    telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def send_media_group(chat_id, media):
    return telegram(
        "sendMediaGroup",
        {
            "chat_id": chat_id,
            "media": __import__("json").dumps(media)
        }
    )


# =========================
# ХРАНЕНИЕ ФОТО
# =========================

photos = {}


# =========================
# ПОЛУЧЕНИЕ FILE_ID
# =========================

def get_photo_file_id(message):
    photo = message.get("photo")

    if not photo:
        return None

    # Берём фотографию максимального качества
    return photo[-1]["file_id"]


# =========================
# ПУБЛИКАЦИЯ КАРУСЕЛИ
# =========================

def publish_carousel(chat_id):

    user_photos = photos.get(chat_id, [])

    if len(user_photos) < 2:
        send_message(
            chat_id,
            "Для карусели нужно минимум 2 фотографии."
        )
        return

    if len(user_photos) > 9:
        user_photos = user_photos[:9]

    media = []

    for i, file_id in enumerate(user_photos):
        item = {
            "type": "photo",
            "media": file_id
        }

        if i == 0:
            item["caption"] = " "
        else:
            item["caption"] = ""

        media.append(item)

    try:
        send_media_group(CHANNEL, media)

        send_message(
            chat_id,
            f"Готово! Опубликовано фотографий: {len(media)}."
        )

        photos[chat_id] = []

    except Exception as e:
        print("Ошибка публикации:", e)

        send_message(
            chat_id,
            "Не удалось опубликовать карусель. Проверь, что бот является администратором канала."
        )


# =========================
# ОБРАБОТКА СООБЩЕНИЙ
# =========================

def process_update(update):

    message = update.get("message")

    if not message:
        return

    chat_id = message["chat"]["id"]

    text = message.get("text", "")

    # Команда START
    if text == "/start":
        photos.setdefault(chat_id, [])

        send_message(
            chat_id,
            "Привет! 👋\n\n"
            "Отправь мне от 2 до 9 фотографий.\n"
            "После этого напиши /publish — и я опубликую их одной каруселью в канале."
        )
        return

    # Команда очистки
    if text == "/clear":
        photos[chat_id] = []

        send_message(
            chat_id,
            "Фотографии очищены. Можешь начать новую карусель."
        )
        return

    # Команда публикации
    if text == "/publish":
        publish_carousel(chat_id)
        return

    # Получили фотографию
    file_id = get_photo_file_id(message)

    if file_id:

        photos.setdefault(chat_id, [])

        if len(photos[chat_id]) >= 9:
            send_message(
                chat_id,
                "Максимум 9 фотографий. Напиши /publish для публикации."
            )
            return

        photos[chat_id].append(file_id)

        count = len(photos[chat_id])

        send_message(
            chat_id,
            f"Фото добавлено: {count}/9\n\n"
            "Можешь отправить ещё фотографии или написать /publish."
        )

        return


# =========================
# ЗАПУСК LONG POLLING
# =========================

def run_bot():

    offset = None

    print("Telegram carousel bot started.")

    while True:

        try:

            data = {
                "timeout": 30,
                "allowed_updates": ["message"]
            }

            if offset is not None:
                data["offset"] = offset

            response = requests.get(
                f"{API_URL}/getUpdates",
                params=data,
                timeout=40
            )

            response.raise_for_status()

            result = response.json()

            if not result.get("ok"):
                print("Telegram API error:", result)
                continue

            for update in result.get("result", []):

                offset = update["update_id"] + 1

                try:
                    process_update(update)
                except Exception as e:
                    print("Update error:", e)

        except Exception as e:

            print("Connection error:", e)


# =========================
# MAIN
# =========================

if __name__ == "__main__":

    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")

    if not CHANNEL:
        raise RuntimeError("CHANNEL is not set")

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    run_bot()
