import os
import json
import requests
import time

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL"]

API = f"https://api.telegram.org/bot{TOKEN}"

photos = {}
offset = None


def send_message(chat_id, text):
    requests.post(
        f"{API}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text
        },
        timeout=30
    )


def get_updates():
    global offset

    params = {
        "timeout": 25,
        "allowed_updates": ["message"]
    }

    if offset is not None:
        params["offset"] = offset

    r = requests.get(
        f"{API}/getUpdates",
        params=params,
        timeout=35
    )

    return r.json()


def download_photo(file_id):
    r = requests.get(
        f"{API}/getFile",
        params={"file_id": file_id},
        timeout=30
    ).json()

    if not r.get("ok"):
        raise Exception(r)

    file_path = r["result"]["file_path"]

    data = requests.get(
        f"https://api.telegram.org/file/bot{TOKEN}/{file_path}",
        timeout=60
    ).content

    filename = f"photo_{time.time_ns()}.jpg"

    with open(filename, "wb") as f:
        f.write(data)

    return filename


def publish(user_id, chat_id):
    items = photos.get(user_id, [])

    if len(items) < 2:
        send_message(chat_id, "Нужно минимум 2 фотографии.")
        return

    if len(items) > 9:
        send_message(chat_id, "Максимум 9 фотографий.")
        return

    send_message(
        chat_id,
        "📸 Получено фотографий: "
        + str(len(items))
        + "\n\nПубликую карусель..."
    )

    # Здесь будет отправка Rich Message.
    # Пока оставляем этот этап отдельно,
    # чтобы сначала проверить получение фотографий.

    send_message(
        chat_id,
        "Фотографии подготовлены."
    )


def main():
    global offset

    print("BOT STARTED")

    while True:
        try:
            result = get_updates()

            if not result.get("ok"):
                print(result)
                time.sleep(5)
                continue

            for update in result["result"]:
                offset = update["update_id"] + 1

                message = update.get("message")

                if not message:
                    continue

                chat_id = message["chat"]["id"]
                user_id = message["from"]["id"]

                if message.get("text") == "/start":
                    photos[user_id] = []

                    send_message(
                        chat_id,
                        "👋 Привет!\n\n"
                        "Отправляй фотографии по одной.\n"
                        "Можно до 9 фотографий.\n\n"
                        "Когда закончишь — напиши /publish"
                    )

                elif message.get("text") == "/clear":
                    photos[user_id] = []

                    send_message(
                        chat_id,
                        "🗑 Фотографии очищены."
                    )

                elif message.get("text") == "/publish":
                    publish(user_id, chat_id)

                elif message.get("photo"):
                    if user_id not in photos:
                        photos[user_id] = []

                    if len(photos[user_id]) >= 9:
                        send_message(
                            chat_id,
                            "⚠️ Максимум 9 фотографий."
                        )
                        continue

                    photo = message["photo"][-1]

                    filename = download_photo(
                        photo["file_id"]
                    )

                    photos[user_id].append(filename)

                    send_message(
                        chat_id,
                        f"📷 Фото получено: "
                        f"{len(photos[user_id])}/9"
                    )

        except Exception as e:
            print("ERROR:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
