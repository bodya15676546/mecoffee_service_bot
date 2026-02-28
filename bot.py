import os
import requests
from flask import Flask, request, jsonify

TOKEN = os.environ.get("BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)

user_states = {}
user_data = {}

ingredients_order = [
    ("coffee", "☕ Кава"),
    ("milk", "🥛 Молоко"),
    ("chocolate", "🍫 Шоколад"),
    ("cream", "🥃 Ірландський крем"),
    ("cups_m", "🥤 Стакани M"),
    ("cups_l", "🥤 Стакани L"),
]

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    requests.post(f"{API_URL}/sendMessage", json=payload)

def edit_message(chat_id, message_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    requests.post(f"{API_URL}/editMessageText", json=payload)

def ingredient_keyboard(value):
    return {
        "inline_keyboard": [
            [
                {"text": "➖", "callback_data": "minus"},
                {"text": f"{value}", "callback_data": "value"},
                {"text": "➕", "callback_data": "plus"},
            ],
            [
                {"text": "Далі ➡️", "callback_data": "next"}
            ]
        ]
    }

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()

    # Обробка callback кнопок
    if "callback_query" in data:
        query = data["callback_query"]
        chat_id = query["message"]["chat"]["id"]
        message_id = query["message"]["message_id"]
        action = query["data"]

        current_key = user_states.get(chat_id)

        if current_key and current_key in user_data[chat_id]:
            value = user_data[chat_id][current_key]
        else:
            value = 0

        if action == "plus":
            value += 1
        elif action == "minus" and value > 0:
            value -= 1
        elif action == "next":
            # зберегти і перейти до наступного
            current_index = [k for k, _ in ingredients_order].index(current_key)
            if current_index + 1 < len(ingredients_order):
                next_key, next_label = ingredients_order[current_index + 1]
                user_states[chat_id] = next_key
                user_data[chat_id][next_key] = 0
                edit_message(
                    chat_id,
                    message_id,
                    f"{next_label}\n\nКількість пачок:",
                    ingredient_keyboard(0)
                )
                return jsonify({"ok": True})
            else:
                user_states[chat_id] = "photo_after"
                send_message(chat_id, "📸 Зробіть фото автомата ПІСЛЯ очищення")
                return jsonify({"ok": True})

        user_data[chat_id][current_key] = value
        label = dict(ingredients_order)[current_key]

        edit_message(
            chat_id,
            message_id,
            f"{label}\n\nКількість пачок:",
            ingredient_keyboard(value)
        )

        return jsonify({"ok": True})

    if "message" not in data:
        return jsonify({"ok": True})

    message = data["message"]
    chat_id = message["chat"]["id"]

    if chat_id not in user_data:
        user_data[chat_id] = {}

    # START
    if message.get("text") == "/start":
        user_states[chat_id] = "photo_before"
        send_message(chat_id, "📸 Зробіть фото автомата ДО очищення")
        return jsonify({"ok": True})

    # Фото ДО
    if user_states.get(chat_id) == "photo_before" and "photo" in message:
        first_key, first_label = ingredients_order[0]
        user_states[chat_id] = first_key
        user_data[chat_id][first_key] = 0
        send_message(
            chat_id,
            f"{first_label}\n\nКількість пачок:",
            ingredient_keyboard(0)
        )
        return jsonify({"ok": True})

    # Фото ПІСЛЯ
    if user_states.get(chat_id) == "photo_after" and "photo" in message:
        user_states[chat_id] = "photo_table"
        send_message(chat_id, "📸 Зробіть фото столу та зони навколо")
        return jsonify({"ok": True})

    # Фото столу
    if user_states.get(chat_id) == "photo_table" and "photo" in message:
        summary = "✅ Звіт завершено\n\n"
        for key, label in ingredients_order:
            summary += f"{label}: {user_data[chat_id].get(key, 0)}\n"

        send_message(chat_id, summary)

        user_states.pop(chat_id, None)
        user_data.pop(chat_id, None)

        return jsonify({"ok": True})

    return jsonify({"ok": True})

@app.route("/")
def home():
    return "Bot is running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
