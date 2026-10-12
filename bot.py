import os
import json
import hmac
import logging
from datetime import datetime
from urllib.parse import quote

import requests
import gspread
from flask import Flask, request, jsonify
from oauth2client.service_account import ServiceAccountCredentials

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mecoffee-bot")

TOKEN = os.environ.get("BOT_TOKEN", "")
API_URL = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "Mecoffee_Service_Reports")

if not TOKEN:
    log.warning("BOT_TOKEN is not configured")

google_creds_raw = os.environ.get("GOOGLE_CREDENTIALS", "")
if not google_creds_raw:
    raise RuntimeError("Missing GOOGLE_CREDENTIALS environment variable")

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
credentials = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(google_creds_raw), scope
)
gc = gspread.authorize(credentials)
spreadsheet = gc.open(SPREADSHEET_NAME)
app = Flask(__name__)

# Configure these accounts in the hosting provider's environment variables.
# Example LOCATION_ACCOUNTS_JSON:
# {"rakovnik":{"login":"rakovnik","password":"CHANGE_ME","location":"Rakovník – fitness","worksheet":"Rakovnik-fitnes"},
#  "brod":{"login":"brod","password":"CHANGE_ME","location":"Železný Brod – Pizza Padre","worksheet":"Zelezny-brod-pizza"},
#  "hradek":{"login":"hradek","password":"CHANGE_ME","location":"Hrádek u Rokycan","worksheet":"Hradek-u-Rokycan"}}
try:
    LOCATION_ACCOUNTS = json.loads(os.environ.get("LOCATION_ACCOUNTS_JSON", "{}"))
    if not isinstance(LOCATION_ACCOUNTS, dict):
        log.error("LOCATION_ACCOUNTS_JSON must be a JSON object, not %s", type(LOCATION_ACCOUNTS).__name__)
        LOCATION_ACCOUNTS = {}
except json.JSONDecodeError:
    LOCATION_ACCOUNTS = {}
    log.exception("LOCATION_ACCOUNTS_JSON is not valid JSON")

# Log account identifiers only; never log passwords or the raw environment value.
log.info("Location account keys loaded: %s", list(LOCATION_ACCOUNTS.keys()))
log.info(
    "Location logins loaded: %s",
    [str(account.get("login", "")) for account in LOCATION_ACCOUNTS.values() if isinstance(account, dict)],
)

# Optional guide links. Add any combination of PDF, video or web-page URLs.
# Example MAINTENANCE_GUIDES_JSON:
# {"waste":"https://example.com/waste.pdf","drip_tray":"https://example.com/video",
#  "exterior":"https://example.com/cleaning","coffee_unit":"https://example.com/machine-manual.pdf"}
try:
    MAINTENANCE_GUIDES = json.loads(os.environ.get("MAINTENANCE_GUIDES_JSON", "{}"))
except json.JSONDecodeError:
    MAINTENANCE_GUIDES = {}
    log.exception("MAINTENANCE_GUIDES_JSON is not valid JSON")

user_states = {}
user_data = {}
sessions = {}  # chat_id -> authenticated location account; re-login required after restart

ingredients = [
    ("coffee", "☕ Káva"),
    ("milk", "🥛 Mléko"),
    ("chocolate", "🍫 Čokoláda"),
    ("matcha", "🍵 Matcha"),
    ("cups_s", "🥤 Kelímky S"),
    ("cups_m", "🥤 Kelímky M"),
    ("cups_l", "🥤 Kelímky L"),
]

maintenance_steps = [
    {
        "key": "waste",
        "title": "1/4 – Odpad a okolí",
        "text": "Zkontrolujte okolí automatu. Vyprázdněte odpadkový koš a ukliďte nečistoty kolem automatu. Pokud je něco poškozené, nahlaste problém.",
    },
    {
        "key": "drip_tray",
        "title": "2/4 – Odkapávací miska a odpadní nádoba",
        "text": "Opatrně vyjměte odkapávací misku a odpadní nádobu. Vyprázdněte je, opláchněte a vraťte správně na místo. Zkontrolujte, zda nic neteče.",
    },
    {
        "key": "exterior",
        "title": "3/4 – Vnější plochy",
        "text": "Měkkým hadříkem očistěte vnější plochy automatu a prostor kolem výdejního místa. Nepoužívejte agresivní čističe ani nestříkejte kapalinu do otvorů.",
    },
    {
        "key": "coffee_unit",
        "title": "4/4 – Čištění kávovaru",
        "text": "Proveďte běžné čištění podle pokynů pro daný model. Nezasahujte do elektrických částí a neprovádějte servisní úkony, které nejsou součástí běžné údržby.",
    },
]

MAIN_MENU = {
    "keyboard": [["🧽 Údržba"], ["📦 Příjem zboží"]],
    "resize_keyboard": True,
    "is_persistent": True,
}
CANCEL_KEYBOARD = {
    "keyboard": [["❌ Zrušit"], ["🚪 Odhlásit"]],
    "resize_keyboard": True,
}
STEP_KEYBOARD = {
    "keyboard": [["✅ Hotovo – další krok"], ["⚠️ Nahlásit problém"], ["❌ Zrušit"]],
    "resize_keyboard": True,
}
RECEIPT_KEYBOARD = {
    "keyboard": [["➕ Přidat další položku"], ["✅ Dokončit příjem"], ["❌ Zrušit"]],
    "resize_keyboard": True,
}
UNIT_KEYBOARD = {
    "keyboard": [["ks", "balení"], ["g", "kg"], ["ml", "l"], ["❌ Zrušit"]],
    "resize_keyboard": True,
}

def send_message(chat_id, text, keyboard=None, inline_keyboard=None):
    payload = {"chat_id": chat_id, "text": text}
    if keyboard:
        payload["reply_markup"] = keyboard
    if inline_keyboard:
        payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
    try:
        response = requests.post(f"{API_URL}/sendMessage", json=payload, timeout=15)
        response.raise_for_status()
    except Exception:
        log.exception("Telegram sendMessage failed")

def send_guide(chat_id, step):
    url = MAINTENANCE_GUIDES.get(step["key"], "")
    if url and isinstance(url, str) and url.startswith(("https://", "http://")):
        send_message(
            chat_id,
            "Pokud potřebujete podrobnější návod, otevřete ho zde:",
            inline_keyboard=[[{"text": "📖 Otevřít návod", "url": url}]],
        )
    else:
        send_message(chat_id, "📖 Podrobný návod zatím není připojen. Odkaz lze doplnit v nastavení bota.")

def upload_photo(file_id):
    response = requests.get(f"{API_URL}/getFile", params={"file_id": file_id}, timeout=15)
    response.raise_for_status()
    file_path = response.json()["result"]["file_path"]
    return f"https://api.telegram.org/file/bot{TOKEN}/{quote(file_path)}"

def ensure_worksheet(name, headers):
    try:
        ws = spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=name, rows=1000, cols=max(len(headers), 10))
        ws.append_row(headers)
        return ws
    if not ws.row_values(1):
        ws.append_row(headers)
    return ws

def account_by_login(login):
    # Ignore accidental spaces and letter case in the login entered in Telegram.
    supplied_login = str(login).strip().casefold()
    for account in LOCATION_ACCOUNTS.values():
        if not isinstance(account, dict):
            continue
        configured_login = str(account.get("login", "")).strip().casefold()
        if configured_login and hmac.compare_digest(configured_login, supplied_login):
            return account
    return None

def authenticated_account(chat_id):
    return sessions.get(str(chat_id))

def start_login(chat_id):
    sessions.pop(str(chat_id), None)
    user_data.pop(chat_id, None)
    user_states[chat_id] = "login"
    send_message(chat_id, "Přihlášení do MeCoffee Service Bot\n\nZadejte přihlašovací jméno vaší provozovny:", CANCEL_KEYBOARD)

def show_menu(chat_id):
    account = authenticated_account(chat_id)
    if not account:
        start_login(chat_id)
        return
    send_message(
        chat_id,
        f"MeCoffee Service Bot\nPřihlášená provozovna: {account.get('location', 'neznámá')}\n\nCo potřebujete udělat?",
        MAIN_MENU,
    )

def begin_maintenance(chat_id, username):
    account = authenticated_account(chat_id)
    if not account:
        start_login(chat_id)
        return
    user_data[chat_id] = {
        "account": account,
        "telegram_user": username,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "step_index": 0,
        "issues": [],
    }
    user_states[chat_id] = "maintenance_step"
    send_message(chat_id, "📸 Nejprve vyfoťte automat PŘED údržbou.", CANCEL_KEYBOARD)

def send_current_step(chat_id):
    data = user_data.get(chat_id, {})
    index = data.get("step_index", 0)
    if index >= len(maintenance_steps):
        user_states[chat_id] = "maintenance_ingredients"
        data["ingredient_index"] = 0
        send_message(chat_id, "Údržba je hotová. Teď postupně zadejte počet balení, která jsou v automatu po doplnění.", CANCEL_KEYBOARD)
        ask_ingredient(chat_id)
        return
    step = maintenance_steps[index]
    send_message(chat_id, f"🧽 {step['title']}\n\n{step['text']}", STEP_KEYBOARD)
    send_guide(chat_id, step)

def ask_ingredient(chat_id):
    data = user_data.get(chat_id, {})
    index = data.get("ingredient_index", 0)
    if index >= len(ingredients):
        user_states[chat_id] = "maintenance_photo_after"
        send_message(chat_id, "📸 Vyfoťte automat PO údržbě.", CANCEL_KEYBOARD)
        return
    key, label = ingredients[index]
    send_message(chat_id, f"{label}\n\nKolik balení je nyní v automatu po doplnění? Zadejte číslo.", CANCEL_KEYBOARD)

def start_receipt(chat_id, username):
    account = authenticated_account(chat_id)
    if not account:
        start_login(chat_id)
        return
    user_data[chat_id] = {
        "account": account,
        "telegram_user": username,
        "receipt_items": [],
        "receipt_started": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    user_states[chat_id] = "receipt_product"
    send_message(chat_id, "📦 Příjem zboží\n\nZadejte název produktu:", CANCEL_KEYBOARD)

def finish_receipt(chat_id):
    data = user_data.get(chat_id, {})
    account = data.get("account", {})
    if not data.get("receipt_items"):
        send_message(chat_id, "Není zadaná žádná položka. Příjem nebyl uložen.", MAIN_MENU)
        user_states.pop(chat_id, None)
        user_data.pop(chat_id, None)
        return
    ws = ensure_worksheet("Příjem zboží", ["Datum", "Provozovna", "Přihlašovací účet", "Telegram uživatel", "Produkt", "Množství", "Jednotka"])
    rows = []
    for item in data["receipt_items"]:
        rows.append([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            account.get("location", ""),
            account.get("login", ""),
            data.get("telegram_user", ""),
            item["product"],
            item["quantity"],
            item["unit"],
        ])
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    user_states.pop(chat_id, None)
    user_data.pop(chat_id, None)
    send_message(chat_id, f"✅ Příjem uložen. Počet položek: {len(rows)}.", MAIN_MENU)

def log_issue(chat_id):
    user_states[chat_id] = "maintenance_issue"
    send_message(chat_id, "Popište problém co nejkonkrétněji. Pokud můžete, uveďte, co nefunguje a od kdy.", CANCEL_KEYBOARD)

def save_maintenance(chat_id):
    data = user_data.get(chat_id, {})
    account = data.get("account", {})
    sheet_name = account.get("worksheet", "")
    if not sheet_name:
        send_message(chat_id, "❌ U účtu není nastaven list pro report údržby. Kontaktujte správce.")
        return
    headers = [
        "Date", "User", "Káva", "Mléko", "Čokoláda", "Matcha", "Kelímky S", "Kelímky M", "Kelímky L",
        "Foto automat před", "Foto stůl před", "Foto automat po", "Foto stůl po", "Provozovna", "Problémy"
    ]
    ws = ensure_worksheet(sheet_name, headers)
    ws.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        account.get("login", ""),
        data.get("coffee", ""),
        data.get("milk", ""),
        data.get("chocolate", ""),
        data.get("matcha", ""),
        data.get("cups_s", ""),
        data.get("cups_m", ""),
        data.get("cups_l", ""),
        data.get("photo_automat_before", ""),
        data.get("photo_table_before", ""),
        data.get("photo_after", ""),
        data.get("photo_table_after", ""),
        account.get("location", ""),
        " | ".join(data.get("issues", [])),
    ])
    if data.get("issues"):
        issue_ws = ensure_worksheet("Problémy údržby", ["Datum", "Provozovna", "Účet", "Telegram uživatel", "Problém"])
        issue_ws.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            account.get("location", ""),
            account.get("login", ""),
            data.get("telegram_user", ""),
            " | ".join(data["issues"]),
        ])
    user_states.pop(chat_id, None)
    user_data.pop(chat_id, None)
    send_message(chat_id, "✅ Údržba byla uložena.", MAIN_MENU)

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    if "message" not in data:
        return jsonify({"ok": True})

    msg = data["message"]
    chat_id = msg.get("chat", {}).get("id")
    if chat_id is None:
        return jsonify({"ok": True})
    chat_key = str(chat_id)
    sender = msg.get("from", {})
    username = sender.get("username") or sender.get("first_name") or str(sender.get("id", "unknown"))
    text = msg.get("text", "").strip()
    state = user_states.get(chat_id)

    if text in ("/start", "/login"):
        start_login(chat_id)
        return jsonify({"ok": True})
    if text == "🚪 Odhlásit":
        sessions.pop(chat_key, None)
        user_states.pop(chat_id, None)
        user_data.pop(chat_id, None)
        send_message(chat_id, "Byli jste odhlášeni.")
        start_login(chat_id)
        return jsonify({"ok": True})
    if text == "❌ Zrušit":
        user_states.pop(chat_id, None)
        user_data.pop(chat_id, None)
        show_menu(chat_id)
        return jsonify({"ok": True})

    if state == "login":
        account = account_by_login(text)
        if not account:
            send_message(chat_id, "❌ Tento login neznám. Zkuste to znovu, nebo kontaktujte správce.", CANCEL_KEYBOARD)
            return jsonify({"ok": True})
        user_data[chat_id] = {"pending_account": account}
        user_states[chat_id] = "password"
        send_message(chat_id, "Zadejte heslo provozovny:", CANCEL_KEYBOARD)
        return jsonify({"ok": True})

    if state == "password":
        account = user_data.get(chat_id, {}).get("pending_account", {})
        expected = str(account.get("password", ""))
        if not expected or not hmac.compare_digest(text, expected):
            user_data.pop(chat_id, None)
            user_states[chat_id] = "login"
            send_message(chat_id, "❌ Nesprávné heslo. Zadejte znovu přihlašovací jméno:", CANCEL_KEYBOARD)
            return jsonify({"ok": True})
        safe_account = {
            "login": str(account.get("login", "")),
            "location": str(account.get("location", "")),
            "worksheet": str(account.get("worksheet", "")),
        }
        sessions[chat_key] = safe_account
        user_data.pop(chat_id, None)
        user_states.pop(chat_id, None)
        # Best-effort deletion so the password is not left visible in the chat.
        try:
            requests.post(f"{API_URL}/deleteMessage", json={"chat_id": chat_id, "message_id": msg.get("message_id")}, timeout=10)
        except Exception:
            log.warning("Could not delete password message")
        show_menu(chat_id)
        return jsonify({"ok": True})

    if not authenticated_account(chat_id):
        start_login(chat_id)
        return jsonify({"ok": True})

    if text == "🧽 Údržba":
        begin_maintenance(chat_id, username)
        return jsonify({"ok": True})
    if text == "📦 Příjem zboží":
        start_receipt(chat_id, username)
        return jsonify({"ok": True})
    if text == "🚪 Odhlásit":
        sessions.pop(chat_key, None)
        start_login(chat_id)
        return jsonify({"ok": True})

    if state == "maintenance_step":
        if "photo" in msg and "photo_automat_before" not in user_data.get(chat_id, {}):
            user_data[chat_id]["photo_automat_before"] = upload_photo(msg["photo"][-1]["file_id"])
            send_message(chat_id, "📸 Teď vyfoťte pracovní plochu / stůl PŘED údržbou.", CANCEL_KEYBOARD)
            user_states[chat_id] = "maintenance_photo_table_before"
            return jsonify({"ok": True})
        if text == "⚠️ Nahlásit problém":
            log_issue(chat_id)
            return jsonify({"ok": True})
        if text == "✅ Hotovo – další krok":
            user_data[chat_id]["step_index"] = user_data[chat_id].get("step_index", 0) + 1
            send_current_step(chat_id)
            return jsonify({"ok": True})
        send_message(chat_id, "Dokončete aktuální krok tlačítkem „✅ Hotovo – další krok“, nebo nahlaste problém.", STEP_KEYBOARD)
        return jsonify({"ok": True})

    if state == "maintenance_photo_table_before" and "photo" in msg:
        user_data[chat_id]["photo_table_before"] = upload_photo(msg["photo"][-1]["file_id"])
        user_states[chat_id] = "maintenance_step"
        send_current_step(chat_id)
        return jsonify({"ok": True})

    if state == "maintenance_issue":
        user_data.setdefault(chat_id, {}).setdefault("issues", []).append(text or "[Problém bez textového popisu]")
        send_message(chat_id, "⚠️ Problém zaznamenán. Pokračujte v údržbě tlačítkem níže.", STEP_KEYBOARD)
        user_states[chat_id] = "maintenance_step"
        return jsonify({"ok": True})

    if state == "maintenance_ingredients":
        key, label = ingredients[user_data[chat_id]["ingredient_index"]]
        try:
            quantity = float(text.replace(",", "."))
            if quantity < 0:
                raise ValueError
        except ValueError:
            send_message(chat_id, "Zadejte nezáporné číslo, například 2 nebo 1,5.")
            return jsonify({"ok": True})
        user_data[chat_id][key] = text
        user_data[chat_id]["ingredient_index"] += 1
        ask_ingredient(chat_id)
        return jsonify({"ok": True})

    if state == "maintenance_photo_after" and "photo" in msg:
        user_data[chat_id]["photo_after"] = upload_photo(msg["photo"][-1]["file_id"])
        user_states[chat_id] = "maintenance_photo_table_after"
        send_message(chat_id, "📸 Vyfoťte pracovní plochu / stůl PO údržbě.", CANCEL_KEYBOARD)
        return jsonify({"ok": True})

    if state == "maintenance_photo_table_after" and "photo" in msg:
        user_data[chat_id]["photo_table_after"] = upload_photo(msg["photo"][-1]["file_id"])
        save_maintenance(chat_id)
        return jsonify({"ok": True})

    if state == "receipt_product":
        if not text:
            send_message(chat_id, "Zadejte název produktu jako text.")
            return jsonify({"ok": True})
        user_data[chat_id]["current_item"] = {"product": text}
        user_states[chat_id] = "receipt_quantity"
        send_message(chat_id, f"Produkt: {text}\n\nZadejte množství (např. 10 nebo 1,5):", CANCEL_KEYBOARD)
        return jsonify({"ok": True})

    if state == "receipt_quantity":
        try:
            quantity = float(text.replace(",", "."))
            if quantity <= 0:
                raise ValueError
        except ValueError:
            send_message(chat_id, "Zadejte množství větší než 0, například 10 nebo 1,5.")
            return jsonify({"ok": True})
        user_data[chat_id]["current_item"]["quantity"] = text
        user_states[chat_id] = "receipt_unit"
        send_message(chat_id, "Vyberte jednotku:", UNIT_KEYBOARD)
        return jsonify({"ok": True})

    if state == "receipt_unit":
        allowed_units = {"ks", "balení", "g", "kg", "ml", "l"}
        if text not in allowed_units:
            send_message(chat_id, "Vyberte jednu z nabízených jednotek.", UNIT_KEYBOARD)
            return jsonify({"ok": True})
        user_data[chat_id]["current_item"]["unit"] = text
        user_data[chat_id]["receipt_items"].append(user_data[chat_id].pop("current_item"))
        user_states[chat_id] = "receipt_more"
        count = len(user_data[chat_id]["receipt_items"])
        send_message(chat_id, f"✅ Položka přidána. Celkem položek: {count}.\n\nPřidejte další produkt, nebo dokončete příjem.", RECEIPT_KEYBOARD)
        return jsonify({"ok": True})

    if state == "receipt_more":
        if text == "➕ Přidat další položku":
            user_states[chat_id] = "receipt_product"
            send_message(chat_id, "Zadejte název dalšího produktu:", CANCEL_KEYBOARD)
        elif text == "✅ Dokončit příjem":
            finish_receipt(chat_id)
        else:
            send_message(chat_id, "Vyberte „➕ Přidat další položku“ nebo „✅ Dokončit příjem“.", RECEIPT_KEYBOARD)
        return jsonify({"ok": True})

    send_message(chat_id, "Vyberte jednu z možností v menu.", MAIN_MENU)
    return jsonify({"ok": True})

@app.route("/")
def home():
    return "MeCoffee Service Bot is running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
