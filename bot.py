import os
import requests
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, request, jsonify
from datetime import datetime

TOKEN = os.environ.get("BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{TOKEN}"

google_creds = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
credentials = ServiceAccountCredentials.from_json_keyfile_dict(google_creds, scope)
gc = gspread.authorize(credentials)
sheet = gc.open("Mecoffee_Service_Reports").sheet1

app = Flask(__name__)

user_states = {}
user_data = {}

ingredients = [
    ("coffee", "☕ Káva"),
    ("milk", "🥛 Mléko"),
    ("chocolate", "🍫 Čokoláda"),
    ("cream", "🥃 Irish Cream"),
    ("cups_m", "🥤 Kelímky M"),
    ("cups_l", "🥤 Kelímky L"),
]

def send_message(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    requests.post(f"{API_URL}/sendMessage", json=payload)

def upload_photo(file_id):
    file_info = requests.get(f"{API_URL}/getFile?file_id={file_id}").json()
    file_path = file_info["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

def already_submitted_today(username):
    today = datetime.now().strftime("%Y-%m-%d")
    records = sheet.get_all_records()
    for row in records:
        if row["User"] == username and row["Date"].startswith(today):
            return True
    return False

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json()

    if "message" not in data:
        return jsonify({"ok": True})

    msg = data["message"]
    chat_id = msg["chat"]["id"]
    username = msg["from"].get("username", "unknown")

    if chat_id not in user_data:
        user_data[chat_id] = {}

    # Кнопка старту
    if msg.get("text") in ["/start", "➕ Přidat report"]:
        if already_submitted_today(username):
            send_message(chat_id, "❌ Dnešní report už byl odeslán.")
            return jsonify({"ok": True})

        user_states[chat_id] = "photo_before"
        send_message(chat_id, "📸 Udělejte foto automatu PŘED čištěním")
        return jsonify({"ok": True})

    # Фото ДО
    if user_states.get(chat_id) == "photo_before" and "photo" in msg:
        user_data[chat_id]["photo_before"] = upload_photo(msg["photo"][-1]["file_id"])
        first_key, first_label = ingredients[0]
        user_states[chat_id] = first_key
        user_data[chat_id][first_key] = 0
        send_message(chat_id, f"{first_label}\nNapište počet balení (číslo):")
        return jsonify({"ok": True})

    # Інгредієнти (простий ввід числа)
    if user_states.get(chat_id) in [k for k, _ in ingredients]:
        current = user_states[chat_id]
        user_data[chat_id][current] = msg.get("text", "0")

        index = [k for k, _ in ingredients].index(current)
        if index + 1 < len(ingredients):
            next_key, next_label = ingredients[index + 1]
            user_states[chat_id] = next_key
            send_message(chat_id, f"{next_label}\nNapište počet balení (číslo):")
        else:
            user_states[chat_id] = "photo_after"
            send_message(chat_id, "📸 Udělejte foto automatu PO vyčištění")
        return jsonify({"ok": True})

    # Фото ПІСЛЯ
    if user_states.get(chat_id) == "photo_after" and "photo" in msg:
        user_data[chat_id]["photo_after"] = upload_photo(msg["photo"][-1]["file_id"])
        user_states[chat_id] = "photo_table"
        send_message(chat_id, "📸 Udělejte foto stolu a okolí")
        return jsonify({"ok": True})

    # Фото столу
    if user_states.get(chat_id) == "photo_table" and "photo" in msg:
        user_data[chat_id]["photo_table"] = upload_photo(msg["photo"][-1]["file_id"])

        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            username,
            user_data[chat_id].get("coffee",0),
            user_data[chat_id].get("milk",0),
            user_data[chat_id].get("chocolate",0),
            user_data[chat_id].get("cream",0),
            user_data[chat_id].get("cups_m",0),
            user_data[chat_id].get("cups_l",0),
            user_data[chat_id].get("photo_before"),
            user_data[chat_id].get("photo_after"),
            user_data[chat_id].get("photo_table")
        ])

        keyboard = {
            "keyboard": [["➕ Přidat report"]],
            "resize_keyboard": True
        }

        send_message(chat_id, "✅ Report uložen", keyboard)

        user_states.pop(chat_id, None)
        user_data.pop(chat_id, None)
        return jsonify({"ok": True})

    return jsonify({"ok": True})

@app.route("/")
def home():
    return "Bot is running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
