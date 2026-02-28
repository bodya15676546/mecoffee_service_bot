import os
import requests
from flask import Flask, request, jsonify

TOKEN = os.environ.get("BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)

# тимчасове збереження стану користувача
user_states = {}

def send_message(chat_id, text):
    requests.post(
        f"{API_URL}/sendMessage",
        json={"chat_id": chat_id, "text": text}
    )

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()

    if "message" not in data:
        return jsonify({"ok": True})

    message = data["message"]
    chat_id = message["chat"]["id"]

    # START
    if message.get("text") == "/start":
        user_states[chat_id] = "photo_before"
        send_message(chat_id, "📸 Зробіть фото автомата ДО очищення")
        return jsonify({"ok": True})

    # Фото ДО
    if user_states.get(chat_id) == "photo_before" and "photo" in message:
        user_states[chat_id] = "photo_after"
        send_message(chat_id, "📸 Зробіть фото автомата ПІСЛЯ очищення")
        return jsonify({"ok": True})

    # Фото ПІСЛЯ
    if user_states.get(chat_id) == "photo_after" and "photo" in message:
        user_states[chat_id] = "photo_table"
        send_message(chat_id, "📸 Зробіть фото столу та зони навколо")
        return jsonify({"ok": True})

    # Фото столу
    if user_states.get(chat_id) == "photo_table" and "photo" in message:
        user_states.pop(chat_id, None)
        send_message(chat_id, "✅ Звіт завершено")
        return jsonify({"ok": True})

    return jsonify({"ok": True})

@app.route("/")
def home():
    return "Bot is running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
