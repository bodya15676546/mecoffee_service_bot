import os
import json
import requests
import gspread

from flask import Flask, request, jsonify
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

TOKEN = os.environ.get("BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{TOKEN}"

google_creds = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

credentials = ServiceAccountCredentials.from_json_keyfile_dict(
    google_creds,
    scope
)

gc = gspread.authorize(credentials)

spreadsheet = gc.open("Mecoffee_Service_Reports")

app = Flask(__name__)

user_states = {}
user_data = {}

machines = {
    "Rakovnik-fitnes": "Rakovnik-fitnes",
    "Zelezny-brod-pizza": "Zelezny-brod-pizza"
}

ingredients = [
    ("coffee", "☕ Káva"),
    ("milk", "🥛 Mléko"),
    ("chocolate", "🍫 Čokoláda"),
    ("matcha", "🍵 Matcha"),
    ("cups_s", "🥤 Kelímky S"),
    ("cups_m", "🥤 Kelímky M"),
    ("cups_l", "🥤 Kelímky L"),
]

def send_message(chat_id, text, keyboard=None):

    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:
        payload["reply_markup"] = keyboard

    requests.post(
        f"{API_URL}/sendMessage",
        json=payload
    )

def upload_photo(file_id):

    file_info = requests.get(
        f"{API_URL}/getFile?file_id={file_id}"
    ).json()

    file_path = file_info["result"]["file_path"]

    return f"https://api.telegram.org/file/bot{TOKEN}/{file_path}"

def already_submitted_today(username, worksheet_name):

    worksheet = spreadsheet.worksheet(worksheet_name)

    today = datetime.now().strftime("%Y-%m-%d")

    records = worksheet.get_all_records()

    for row in records:

        if (
            row["User"] == username
            and str(row["Date"]).startswith(today)
        ):
            return True

    return False

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():

    data = request.get_json()

    if "message" not in data:
        return jsonify({"ok": True})

    msg = data["message"]

    chat_id = msg["chat"]["id"]

    username = msg["from"].get(
        "username",
        "unknown"
    )

    if chat_id not in user_data:
        user_data[chat_id] = {}

    text = msg.get("text", "")

    # START
    if text in ["/start", "➕ Přidat report"]:

        keyboard = {
            "keyboard": [
                ["Rakovnik-fitnes"],
                ["Zelezny-brod-pizza"]
            ],
            "resize_keyboard": True,
            "one_time_keyboard": True
        }

        user_states[chat_id] = "machine_select"

        send_message(
            chat_id,
            "Vyberte automat:",
            keyboard
        )

        return jsonify({"ok": True})

    # MACHINE SELECT
    if user_states.get(chat_id) == "machine_select":

        if text not in machines:
            send_message(chat_id, "❌ Neplatný výběr automatu.")
            return jsonify({"ok": True})

        worksheet_name = machines[text]

        if already_submitted_today(username, worksheet_name):

            send_message(
                chat_id,
                "❌ Dnešní report už byl odeslán."
            )

            return jsonify({"ok": True})

        user_data[chat_id]["worksheet"] = worksheet_name

        user_states[chat_id] = "photo_automat_before"

        send_message(
            chat_id,
            "📸 Udělejte foto automatu PŘED čištěním"
        )

        return jsonify({"ok": True})

    # PHOTO AUTOMAT BEFORE
    if (
        user_states.get(chat_id) == "photo_automat_before"
        and "photo" in msg
    ):

        user_data[chat_id]["photo_automat_before"] = upload_photo(
            msg["photo"][-1]["file_id"]
        )

        user_states[chat_id] = "photo_table_before"

        send_message(
            chat_id,
            "📸 Udělejte foto stolu PŘED čištěním"
        )

        return jsonify({"ok": True})

    # PHOTO TABLE BEFORE
    if (
        user_states.get(chat_id) == "photo_table_before"
        and "photo" in msg
    ):

        user_data[chat_id]["photo_table_before"] = upload_photo(
            msg["photo"][-1]["file_id"]
        )

        first_key, first_label = ingredients[0]

        user_states[chat_id] = first_key

        send_message(
            chat_id,
            f"{first_label}\n\nKolik balení je nyní v automatu po doplnění? (číslo)"
        )

        return jsonify({"ok": True})

    # INGREDIENTS
    current_state = user_states.get(chat_id)

    ingredient_keys = [k for k, _ in ingredients]

    if current_state in ingredient_keys:

        user_data[chat_id][current_state] = text

        index = ingredient_keys.index(current_state)

        if index + 1 < len(ingredients):

            next_key, next_label = ingredients[index + 1]

            user_states[chat_id] = next_key

            send_message(
                chat_id,
                f"{next_label}\n\nKolik balení je nyní v automatu po doplnění? (číslo)"
            )

        else:

            user_states[chat_id] = "photo_after"

            send_message(
                chat_id,
                "📸 Udělejte foto automatu PO vyčištění"
            )

        return jsonify({"ok": True})

    # PHOTO AFTER
    if (
        user_states.get(chat_id) == "photo_after"
        and "photo" in msg
    ):

        user_data[chat_id]["photo_after"] = upload_photo(
            msg["photo"][-1]["file_id"]
        )

        user_states[chat_id] = "photo_table_after"

        send_message(
            chat_id,
            "📸 Udělejte foto stolu PO vyčištění"
        )

        return jsonify({"ok": True})

    # PHOTO TABLE AFTER
    if (
        user_states.get(chat_id) == "photo_table_after"
        and "photo" in msg
    ):

        user_data[chat_id]["photo_table"] = upload_photo(
            msg["photo"][-1]["file_id"]
        )

        worksheet = spreadsheet.worksheet(
            user_data[chat_id]["worksheet"]
        )

        worksheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            username,
            user_data[chat_id].get("coffee", ""),
            user_data[chat_id].get("milk", ""),
            user_data[chat_id].get("chocolate", ""),
            user_data[chat_id].get("matcha", ""),
            user_data[chat_id].get("cups_s", ""),
            user_data[chat_id].get("cups_m", ""),
            user_data[chat_id].get("cups_l", ""),
            user_data[chat_id].get("photo_automat_before", ""),
            user_data[chat_id].get("photo_table_before", ""),
            user_data[chat_id].get("photo_after", ""),
            user_data[chat_id].get("photo_table", "")
        ])

        keyboard = {
            "keyboard": [
                ["➕ Přidat report"]
            ],
            "resize_keyboard": True
        }

        send_message(
            chat_id,
            "✅ Report byl uložen",
            keyboard
        )

        user_states.pop(chat_id, None)
        user_data.pop(chat_id, None)

        return jsonify({"ok": True})

    return jsonify({"ok": True})

@app.route("/")
def home():
    return "Bot is running"

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000))
    )
