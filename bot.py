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
INVENTORY_SPREADSHEET_ID = os.environ.get("INVENTORY_SPREADSHEET_ID", "1e-Cd3tr0Xm5iukhd-RUDIdUqSr3_nPuiVagO3Hrc_G0")

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
inventory_spreadsheet = None
try:
    inventory_spreadsheet = gc.open_by_key(INVENTORY_SPREADSHEET_ID) if INVENTORY_SPREADSHEET_ID else None
except Exception:
    log.exception("Inventory spreadsheet unavailable; stock movements will not be recorded")
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

# Configure ADMIN_ACCOUNT_JSON as {"login":"admin","password":"CHANGE_ME"}.
try:
    ADMIN_ACCOUNT = json.loads(os.environ.get("ADMIN_ACCOUNT_JSON", "{}"))
    if not isinstance(ADMIN_ACCOUNT, dict):
        ADMIN_ACCOUNT = {}
except json.JSONDecodeError:
    ADMIN_ACCOUNT = {}
    log.exception("ADMIN_ACCOUNT_JSON is not valid JSON")

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
sessions = {}  # chat_id -> authenticated account, cached in memory

INVENTORY_PRODUCTS = [("milk", "Milk 500g", 500), ("chocolate", "Chokolate 1000g", 1000), ("matcha", "Matcha 1000g", 1000), ("coffee", "Cofffee 1000g", 1000), ("cups_l", "Cups L 50ks", 50), ("cups_m", "Cups M 50ks", 50), ("cups_s", "Cups S 50ks", 50), ("lids_l", "Lids L 100ks", 100), ("lids_m", "Lids M 100ks", 100)]
ingredients = [("coffee", "☕ Káva", 1000, "g"), ("milk", "🥛 Mléko", 500, "g"), ("chocolate", "🍫 Čokoláda", 1000, "g"), ("matcha", "🍵 Matcha", 1000, "g")]
STOCKTAKE_ITEMS = [("cups_l", "Kelímky L"), ("cups_m", "Kelímky M"), ("cups_s", "Kelímky S"), ("lids_l", "Víčka L"), ("lids_m", "Víčka M")]
INVENTORY_HEADERS = ["Datum", "Sklad"] + [p[1] for p in INVENTORY_PRODUCTS]

RECEIPT_FIELDS = [
    ("cups_340", "Kelímky 340 ml (balení po 50 ks)", "balení"),
    ("cups_250", "Kelímky 250 ml (balení po 50 ks)", "balení"),
    ("cups_110", "Kelímky 110 ml (balení po 50 ks)", "balení"),
    ("lids_80", "Víčka 80 mm (balení po 100 ks)", "balení"),
    ("lids_90", "Víčka 90 mm (balení po 100 ks)", "balení"),
    ("stirrers", "Míchátka (balení)", "balení"),
    ("syrup", "Sirup (láhve)", "láhev"),
    ("matcha", "Matcha (balení 1 kg)", "balení"),
    ("milk", "Mléko (balení 500 g)", "balení"),
    ("chocolate", "Čokoláda (balení 1 kg)", "balení"),
    ("coffee", "Káva (balení 1 kg)", "balení"),
]

maintenance_steps = [
    {"key":"refill","title":"1 – Doplnění zásob","text":"Doplňte potřebné ingredience a spotřební materiál. Potom do bota zadejte skutečné množství, které jste doplnili do automatu: gramy ingrediencí a kusy kelímků či víček. Nic nevážte."},
    {"key":"mixers","title":"2 – Vyjmutí a umytí mixérů","text":"Vyjměte mixéry a umyjte je přípravkem Sanitol podle pokynů výrobce."},
    {"key":"filters","title":"3 – Pěnové filtry","text":"Zkontrolujte a vyčistěte pěnové filtry na výdechu každého mixéru."},
    {"key":"reinstall","title":"4 – Nasazení a proplach mixérů","text":"Vraťte mixéry na místo, připojte hadičky a otočte nosiče suchých ingrediencí zpět dolů. V servisním menu spusťte proplach mixérů."},
    {"key":"mixers_photo","title":"5 – Fotografie čistých mixérů","text":"Vyfoťte čisté mixéry po umytí a opětovném nasazení."},
    {"key":"brewer","title":"6 – Důkladné čištění spařovací jednotky","text":"Spusťte „Důkladné čištění“, nastavte dobu působení tablety na 8 minut, vložte tabletu do spařovací jednotky a zavřete dvířka."},
    {"key":"during_wait","title":"7 – Úklid během 8minutového čekání","text":"Očistěte stůl přípravkem Sanitol, doplňte cukr a míchátka, otřete pumpičky/dávkovače sirupů a ponořte sirupové trysky na pár vteřin do vroucí vody."},
    {"key":"before_drain","title":"8 – Kontrola vnitřku automatu","text":"Odstraňte rozsypaný prášek a další nečistoty uvnitř automatu. Otřete vnější povrch spařovací jednotky. Jedním kliknutím spusťte program „Úklid“, aby se celý kávovar propláchl."},
    {"key":"drain","title":"9 – Vylití vody a odpadní kapaliny","text":"Vylijte špinavou vodu a nalijte přibližně 60 ml odpadní kapaliny do odpadní nádoby."},
]

WORKER_MENU = {
    "keyboard": [["🧽 Údržba"], ["📦 Příjem zboží"]],
    "resize_keyboard": True,
    "is_persistent": True,
}
ADMIN_MENU = {
    "keyboard": [["🔔 Připomenout údržbu"]],
    "resize_keyboard": True,
    "is_persistent": True,
}
MAIN_MENU = WORKER_MENU

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

def ensure_worksheet(name, headers, book=None):
    book = book or spreadsheet
    try:
        ws = book.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = book.add_worksheet(title=name, rows=1000, cols=max(len(headers), 10))
        ws.append_row(headers)
        return ws
    existing_headers = ws.row_values(1)
    if not existing_headers:
        ws.append_row(headers)
    elif existing_headers != headers:
        # Keep existing report rows, but align the header row with the current report format.
        if ws.col_count < len(headers):
            ws.add_cols(len(headers) - ws.col_count)
        end_col = chr(64 + len(headers))
        ws.update(f"A1:{end_col}1", [headers], value_input_option="RAW")
    return ws

def resolve_inventory_warehouse(account):
    location = str((account or {}).get("location", "")).casefold()
    if "rakovník" in location or "rakovnik" in location:
        return "Rakovnik"
    if "hrádek" in location or "hradek" in location:
        return "Hradek u Rokycan"
    if "železný brod" in location or "zelezny brod" in location:
        raise ValueError("V přehledu zatím není samostatný sklad pro Železný Brod.")
    return "Centralni sklad"

def append_inventory_movement(sheet_name, account, quantities, movement_date=None):
    if inventory_spreadsheet is None:
        raise RuntimeError("Inventární tabulka není dostupná; zkontrolujte sdílení a INVENTORY_SPREADSHEET_ID.")
    warehouse = resolve_inventory_warehouse(account)
    ws = ensure_worksheet(sheet_name, INVENTORY_HEADERS, book=inventory_spreadsheet)
    values = [float(quantities.get(key, 0) or 0) for key, _name, _pack in INVENTORY_PRODUCTS]
    ws.append_row([movement_date or datetime.now().strftime("%Y-%m-%d %H:%M"), warehouse] + values, value_input_option="USER_ENTERED")

def current_inventory_quantity(warehouse, product_key):
    if inventory_spreadsheet is None:
        raise RuntimeError("Inventární tabulka není dostupná.")
    ws = inventory_spreadsheet.worksheet("Přehled")
    headers = ws.row_values(1)
    rows = ws.get_all_values()
    product_name = next(name for key, name, _pack in INVENTORY_PRODUCTS if key == product_key)
    if product_name not in headers:
        raise ValueError(f"V listu Přehled chybí sloupec {product_name}.")
    product_col = headers.index(product_name)
    warehouse_row = next((row for row in rows[1:] if row and row[0].strip() == warehouse), None)
    if not warehouse_row:
        raise ValueError(f"V listu Přehled chybí sklad {warehouse}.")
    raw = warehouse_row[product_col] if product_col < len(warehouse_row) else "0"
    return float(str(raw or "0").replace(",", "."))

def save_stocktake_adjustment(account, stocktake):
    warehouse = resolve_inventory_warehouse(account)
    deltas = {}
    for key, _label in STOCKTAKE_ITEMS:
        counted = float(stocktake[key])
        current = current_inventory_quantity(warehouse, key)
        deltas[key] = counted - current
    receipts = {key: delta for key, delta in deltas.items() if delta > 0}
    issues = {key: -delta for key, delta in deltas.items() if delta < 0}
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    if receipts:
        append_inventory_movement("Příjem zboží", account, receipts, stamp)
    if issues:
        append_inventory_movement("Výdej zboží", account, issues, stamp)

def account_by_login(login):
    supplied_login = str(login).strip().casefold()
    admin_login = str(ADMIN_ACCOUNT.get("login", "")).strip().casefold()
    if admin_login and hmac.compare_digest(admin_login, supplied_login):
        return {"login": str(ADMIN_ACCOUNT.get("login", "")), "password": str(ADMIN_ACCOUNT.get("password", "")), "role": "admin", "location": "MeCoffee – správce", "worksheet": ""}
    for account in LOCATION_ACCOUNTS.values():
        if not isinstance(account, dict):
            continue
        configured_login = str(account.get("login", "")).strip().casefold()
        if configured_login and hmac.compare_digest(configured_login, supplied_login):
            return {**account, "role": "worker"}
    return None

AUTH_SESSIONS_SHEET = "Trvalá přihlášení"
AUTH_SESSION_HEADERS = ["Telegram chat ID", "Přihlašovací účet", "Telegram uživatel", "Přihlášeno"]

def save_auth_session(account, chat_id, username):
    try:
        ws = ensure_worksheet(AUTH_SESSIONS_SHEET, AUTH_SESSION_HEADERS)
        rows = ws.get_all_values()
        row_number = next((idx for idx, row in enumerate(rows[1:], start=2) if row and row[0] == str(chat_id)), None)
        values = [str(chat_id), str(account.get("login", "")), str(username), datetime.now().strftime("%Y-%m-%d %H:%M")]
        if row_number:
            ws.update(f"A{row_number}:D{row_number}", [values], value_input_option="RAW")
        else:
            ws.append_row(values, value_input_option="RAW")
    except Exception:
        log.exception("Could not save persistent login")

def delete_auth_session(chat_id):
    try:
        ws = spreadsheet.worksheet(AUTH_SESSIONS_SHEET)
        rows = ws.get_all_values()
        for idx, row in enumerate(rows[1:], start=2):
            if row and row[0] == str(chat_id):
                ws.delete_rows(idx)
                break
    except gspread.WorksheetNotFound:
        pass
    except Exception:
        log.exception("Could not delete persistent login")

def restore_auth_session(chat_id):
    chat_key = str(chat_id)
    if chat_key in sessions:
        return sessions[chat_key]
    try:
        ws = spreadsheet.worksheet(AUTH_SESSIONS_SHEET)
        rows = ws.get_all_values()
        row = next((row for row in rows[1:] if row and row[0] == chat_key), None)
        if not row or len(row) < 2:
            return None
        account = account_by_login(row[1])
        if not account:
            delete_auth_session(chat_id)
            return None
        safe_account = {
            "login": str(account.get("login", "")),
            "location": str(account.get("location", "")),
            "worksheet": str(account.get("worksheet", "")),
            "role": str(account.get("role", "worker")),
        }
        sessions[chat_key] = safe_account
        return safe_account
    except gspread.WorksheetNotFound:
        return None
    except Exception:
        log.exception("Could not restore persistent login")
        return None

def authenticated_account(chat_id):
    return sessions.get(str(chat_id))

def is_admin(chat_id):

    return (authenticated_account(chat_id) or {}).get("role") == "admin"

def register_notification_recipient(account, chat_id, username):
    if account.get("role") == "admin":
        return
    try:
        headers = ["Přihlašovací účet", "Provozovna", "Telegram chat ID", "Telegram uživatel", "Aktualizováno"]
        ws = ensure_worksheet("Upozornění údržby", headers)
        rows = ws.get_all_values()
        login = str(account.get("login", ""))
        row_number = next((idx for idx, row in enumerate(rows[1:], start=2) if row and row[0] == login), None)
        values = [login, str(account.get("location", "")), str(chat_id), str(username), datetime.now().strftime("%Y-%m-%d %H:%M")]
        if row_number:
            ws.update(f"A{row_number}:E{row_number}", [values], value_input_option="USER_ENTERED")
        else:
            ws.append_row(values, value_input_option="USER_ENTERED")
    except Exception:
        log.exception("Could not register maintenance reminder recipient")

def authenticated_location_names():
    names = []
    for account in LOCATION_ACCOUNTS.values():
        if isinstance(account, dict):
            location = str(account.get("location", "")).strip()
            if location and location not in names:
                names.append(location)
    return names

def send_maintenance_reminder(chat_id, location_name):
    if not is_admin(chat_id):
        send_message(chat_id, "❌ Tuto funkci může používat pouze správce.", WORKER_MENU)
        return
    ws = ensure_worksheet("Upozornění údržby", ["Přihlašovací účet", "Provozovna", "Telegram chat ID", "Telegram uživatel", "Aktualizováno"])
    recipients = [row[2].strip() for row in ws.get_all_values()[1:] if len(row) >= 3 and row[1].strip() == location_name and row[2].strip()]
    if not recipients:
        send_message(chat_id, f"⚠️ Pro provozovnu {location_name} zatím není registrován příjemce. Odpovědný pracovník musí spustit bot a přihlásit se svým účtem.")
        return
    reminder = "🧽 Připomínka údržby\n\nDnes je potřeba provést důkladné čištění automatu MeCoffee. Po dokončení zaznamenejte údržbu v botu."
    sent = 0
    for recipient in dict.fromkeys(recipients):
        try:
            response = requests.post(f"{API_URL}/sendMessage", json={"chat_id": recipient, "text": reminder}, timeout=15)
            response.raise_for_status()
            sent += 1
        except Exception:
            log.exception("Failed to send maintenance reminder")
    send_message(chat_id, f"📨 Připomínka pro {location_name}: odesláno {sent}/{len(set(recipients))}.", ADMIN_MENU)

def start_login(chat_id, forget_saved=False):
    sessions.pop(str(chat_id), None)
    if forget_saved:
        delete_auth_session(chat_id)
    user_data.pop(chat_id, None)
    user_states[chat_id] = "login"
    send_message(chat_id, "Přihlášení do MeCoffee Service Bot\n\nZadejte přihlašovací jméno vaší provozovny:", CANCEL_KEYBOARD)

def show_menu(chat_id):
    account = authenticated_account(chat_id)
    if not account:
        start_login(chat_id)
        return
    keyboard = ADMIN_MENU if account.get("role") == "admin" else WORKER_MENU
    send_message(chat_id, f"MeCoffee Service Bot\nPřihlášená provozovna: {account.get('location', 'neznámá')}\n\nCo potřebujete udělat?", keyboard)

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
        "refill_quantities": {},
    }
    user_states[chat_id] = "maintenance_photo_exterior_before"
    send_message(chat_id, "📸 Automat zvenku – PŘED údržbou. Pošlete fotografii automatu zvenku.", CANCEL_KEYBOARD)

def send_current_step(chat_id):
    data = user_data.get(chat_id, {})
    index = data.get("step_index", 0)
    if index >= len(maintenance_steps):
        user_states[chat_id] = "maintenance_photo_table_after"
        send_message(chat_id, "📸 Stůl – PO údržbě. Vyfoťte čistý stůl.", CANCEL_KEYBOARD)
        return
    step = maintenance_steps[index]
    if step["key"] == "mixers_photo":
        user_states[chat_id] = "maintenance_photo_mixers"
        send_message(chat_id, f"📸 {step['title']}\n\n{step['text']}", CANCEL_KEYBOARD)
        return
    send_message(chat_id, f"🧽 {step['title']}\n\n{step['text']}", STEP_KEYBOARD)
    send_guide(chat_id, step)

def ask_ingredient(chat_id):
    data = user_data.get(chat_id, {})
    index = data.get("ingredient_index", 0)
    if index >= len(ingredients):
        try:
            append_inventory_movement("Výdej zboží", data.get("account", {}), data.get("refill_quantities", {}), data.get("started_at"))
            send_message(chat_id, "✅ Spotřeba ingrediencí byla zapsána do listu Výdej zboží.", CANCEL_KEYBOARD)
        except Exception as exc:
            log.exception("Could not write maintenance consumption to inventory sheet")
            send_message(chat_id, f"⚠️ Zápis spotřeby ingrediencí se nezdařil: {exc}", CANCEL_KEYBOARD)
        data["step_index"] = 1
        user_states[chat_id] = "maintenance_step"
        send_current_step(chat_id)
        return
    key, label, pack_size, unit = ingredients[index]
    send_message(chat_id, f"{label}\n\nKolik jste skutečně doplnili do automatu? Zadejte množství v {unit} (např. {pack_size} nebo 0). Pokud jste tuto položku nedoplňovali, zadejte 0.", CANCEL_KEYBOARD)

def ask_stocktake_item(chat_id):
    data = user_data.get(chat_id, {})
    index = data.get("stocktake_index", 0)
    if index >= len(STOCKTAKE_ITEMS):
        try:
            save_stocktake_adjustment(data.get("account", {}), data.get("stocktake", {}))
            send_message(chat_id, "✅ Zůstatek kelímků a víček byl aktualizován podle inventury.", CANCEL_KEYBOARD)
        except Exception as exc:
            log.exception("Could not save cup/lid stocktake")
            send_message(chat_id, f"⚠️ Nepodařilo se aktualizovat sklad podle inventury: {exc}", CANCEL_KEYBOARD)
        save_maintenance(chat_id)
        return
    key, label = STOCKTAKE_ITEMS[index]
    send_message(chat_id, f"📦 Inventura skladu – {label}\n\nKolik balení nyní zbývá na skladě? Zadejte počet balení (např. 3 nebo 0).", CANCEL_KEYBOARD)

def start_receipt(chat_id, username):
    account = authenticated_account(chat_id)
    if not account:
        start_login(chat_id)
        return
    user_data[chat_id] = {
        "account": account,
        "telegram_user": username,
        "receipt_items": [],
        "receipt_index": 0,
        "receipt_started": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    ask_receipt_field(chat_id)

def ask_receipt_field(chat_id):
    data = user_data.get(chat_id, {})
    index = data.get("receipt_index", 0)
    if index >= len(RECEIPT_FIELDS):
        user_states[chat_id] = "receipt_more"
        send_message(
            chat_id,
            "✅ Základní položky příjmu jsou vyplněny.\n\nMůžete přidat další produkt nebo příjem dokončit.",
            RECEIPT_KEYBOARD,
        )
        return
    _, label, unit = RECEIPT_FIELDS[index]
    user_states[chat_id] = "receipt_field_quantity"
    send_message(
        chat_id,
        f"📦 {label}\n\nZadejte přijaté množství v {unit}. Pokud tuto položku nepřijímáte, zadejte 0.",
        CANCEL_KEYBOARD,
    )

def save_receipt_field_quantity(chat_id, text):
    data = user_data[chat_id]
    index = data.get("receipt_index", 0)
    key, label, unit = RECEIPT_FIELDS[index]
    try:
        quantity = float(text.replace(",", "."))
        if quantity < 0:
            raise ValueError
    except ValueError:
        send_message(chat_id, "Zadejte nezáporné číslo, například 2 nebo 1,5. Zadejte 0, pokud položku nepřijímáte.")
        return
    if quantity > 0:
        data["current_item"] = {"product": label, "quantity": text, "unit": unit, "note": ""}
        if key == "syrup":
            user_states[chat_id] = "receipt_syrup_note"
            send_message(chat_id, "Jaký sirup jste přijali? Uveďte příchuť nebo druh, například karamel, vanilka či pistácie.", CANCEL_KEYBOARD)
            return
        data["receipt_items"].append(data.pop("current_item"))
    data["receipt_index"] = index + 1
    ask_receipt_field(chat_id)

def save_receipt_syrup_note(chat_id, text):
    data = user_data[chat_id]
    note = text.strip()
    if not note:
        send_message(chat_id, "Uveďte příchuť nebo druh sirupu, například karamel či vanilka.")
        return
    data["current_item"]["note"] = note
    data["receipt_items"].append(data.pop("current_item"))
    data["receipt_index"] = data.get("receipt_index", 0) + 1
    ask_receipt_field(chat_id)

def finish_receipt(chat_id):
    data = user_data.get(chat_id, {})
    account = data.get("account", {})
    if not data.get("receipt_items"):
        send_message(chat_id, "Není zadaná žádná položka. Příjem nebyl uložen.", MAIN_MENU)
        user_states.pop(chat_id, None)
        user_data.pop(chat_id, None)
        return
    receipt_headers = ["Datum", "Provozovna", "Přihlašovací účet", "Telegram uživatel", "Produkt", "Množství", "Jednotka", "Poznámka"]
    ws = ensure_worksheet("Příjem zboží", receipt_headers)
    existing_headers = ws.row_values(1)
    if len(existing_headers) < len(receipt_headers):
        ws.update_cell(1, len(receipt_headers), receipt_headers[-1])
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
            item.get("note", ""),
        ])
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    receipt_key_map = {"cups_340": "cups_l", "cups_250": "cups_m", "cups_110": "cups_s", "lids_90": "lids_l", "lids_80": "lids_m", "matcha": "matcha", "milk": "milk", "chocolate": "chocolate", "coffee": "coffee"}
    inventory_quantities = {}
    for item in data["receipt_items"]:
        field = next((key for key, label, _unit in RECEIPT_FIELDS if label == item.get("product")), None)
        target = receipt_key_map.get(field)
        if target:
            inventory_quantities[target] = inventory_quantities.get(target, 0) + float(str(item.get("quantity", "0")).replace(",", "."))
    if inventory_quantities:
        try:
            append_inventory_movement("Příjem zboží", account, inventory_quantities, data.get("receipt_started"))
        except Exception:
            log.exception("Could not write goods receipt to inventory spreadsheet")
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
        "Datum", "Uživatel", "Káva", "Mléko", "Čokoláda", "Matcha", "Kelímky S", "Kelímky M", "Kelímky L",
        "Foto automat před", "Foto stůl před", "Foto vnitřek před", "Foto mixéry čisté",
        "Foto automat po", "Foto stůl po", "Foto vnitřek po", "Provozovna", "Problémy"
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
        data.get("photo_inside_before", ""),
        data.get("photo_mixers_clean", ""),
        data.get("photo_after", ""),
        data.get("photo_table_after", ""),
        data.get("photo_inside_after", ""),
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
    if "callback_query" in data:
        callback = data["callback_query"]
        callback_chat_id = callback.get("message", {}).get("chat", {}).get("id")
        callback_data = callback.get("data", "")
        if callback_chat_id is not None:
            restore_auth_session(callback_chat_id)
        try:
            requests.post(f"{API_URL}/answerCallbackQuery", json={"callback_query_id": callback.get("id")}, timeout=10)
        except Exception:
            log.warning("Could not answer callback query")
        if callback_chat_id is not None and callback_data.startswith("remind:"):
            if not is_admin(callback_chat_id):
                send_message(callback_chat_id, "❌ Tuto funkci může používat pouze správce.", WORKER_MENU)
            elif callback_data == "remind:cancel":
                send_message(callback_chat_id, "Odeslání připomínky zrušeno.", ADMIN_MENU)
            else:
                send_maintenance_reminder(callback_chat_id, callback_data[len("remind:"):])
        return jsonify({"ok": True})
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

    if text == "/start":
        if restore_auth_session(chat_id):
            show_menu(chat_id)
        else:
            start_login(chat_id)
        return jsonify({"ok": True})
    if text == "/login":
        start_login(chat_id, forget_saved=True)
        return jsonify({"ok": True})
    if text == "🚪 Odhlásit":
        sessions.pop(chat_key, None)
        delete_auth_session(chat_id)
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
            "role": str(account.get("role", "worker")),
        }
        sessions[chat_key] = safe_account
        save_auth_session(safe_account, chat_id, username)
        register_notification_recipient(safe_account, chat_id, username)
        user_data.pop(chat_id, None)
        user_states.pop(chat_id, None)
        # Best-effort deletion so the password is not left visible in the chat.
        try:
            requests.post(f"{API_URL}/deleteMessage", json={"chat_id": chat_id, "message_id": msg.get("message_id")}, timeout=10)
        except Exception:
            log.warning("Could not delete password message")
        show_menu(chat_id)
        return jsonify({"ok": True})

    if not authenticated_account(chat_id) and not restore_auth_session(chat_id):
        start_login(chat_id)
        return jsonify({"ok": True})

    if text == "🧽 Údržba":
        begin_maintenance(chat_id, username)
        return jsonify({"ok": True})
    if text == "📦 Příjem zboží":
        start_receipt(chat_id, username)
        return jsonify({"ok": True})
    if text == "🔔 Připomenout údržbu":
        if not is_admin(chat_id):
            send_message(chat_id, "❌ Tuto funkci může používat pouze správce.", WORKER_MENU)
            return jsonify({"ok": True})
        locations = authenticated_location_names()
        if not locations:
            send_message(chat_id, "⚠️ V LOCATION_ACCOUNTS_JSON nejsou nastaveny provozovny.", ADMIN_MENU)
            return jsonify({"ok": True})
        buttons = [[{"text": name, "callback_data": "remind:" + name}] for name in locations]
        buttons.append([{"text": "Zrušit", "callback_data": "remind:cancel"}])
        send_message(chat_id, "Vyberte provozovnu, které chcete poslat připomínku údržby:", inline_keyboard=buttons)
        return jsonify({"ok": True})
    if text == "🚪 Odhlásit":
        sessions.pop(chat_key, None)
        start_login(chat_id)
        return jsonify({"ok": True})

    if state == "maintenance_photo_exterior_before":
        if "photo" not in msg:
            send_message(chat_id, "❗ Je potřeba poslat fotografii automatu zvenku PŘED údržbou. Bez fotografie nelze pokračovat.", CANCEL_KEYBOARD)
            return jsonify({"ok": True})
        user_data[chat_id]["photo_automat_before"] = upload_photo(msg["photo"][-1]["file_id"])
        user_states[chat_id] = "maintenance_photo_table_before"
        send_message(chat_id, "📸 Pracovní stůl – PŘED údržbou. Pošlete fotografii pracovní plochy.", CANCEL_KEYBOARD)
        return jsonify({"ok": True})

    if state == "maintenance_photo_table_before":
        if "photo" not in msg:
            send_message(chat_id, "❗ Je potřeba poslat fotografii pracovního stolu PŘED údržbou. Bez fotografie nelze pokračovat.", CANCEL_KEYBOARD)
            return jsonify({"ok": True})
        user_data[chat_id]["photo_table_before"] = upload_photo(msg["photo"][-1]["file_id"])
        user_states[chat_id] = "maintenance_photo_inside_before"
        send_message(chat_id, "📸 Vnitřek automatu – PŘED údržbou. Pošlete fotografii vnitřku automatu.", CANCEL_KEYBOARD)
        return jsonify({"ok": True})

    if state == "maintenance_photo_inside_before":
        if "photo" not in msg:
            send_message(chat_id, "❗ Je potřeba poslat fotografii vnitřku automatu PŘED údržbou. Bez fotografie nelze pokračovat.", CANCEL_KEYBOARD)
            return jsonify({"ok": True})
        user_data[chat_id]["photo_inside_before"] = upload_photo(msg["photo"][-1]["file_id"])
        user_states[chat_id] = "maintenance_step"
        send_current_step(chat_id)
        return jsonify({"ok": True})

    if state == "maintenance_photo_mixers":
        if "photo" not in msg:
            send_message(chat_id, "❗ Pošlete fotografii čistých mixérů, abyste mohli pokračovat.", CANCEL_KEYBOARD)
            return jsonify({"ok": True})
        user_data[chat_id]["photo_mixers_clean"] = upload_photo(msg["photo"][-1]["file_id"])
        user_data[chat_id]["step_index"] = user_data[chat_id].get("step_index", 0) + 1
        user_states[chat_id] = "maintenance_step"
        send_current_step(chat_id)
        return jsonify({"ok": True})

    if state == "maintenance_step":
        if text == "⚠️ Nahlásit problém":
            log_issue(chat_id)
            return jsonify({"ok": True})
        if text == "✅ Hotovo – další krok":
            if user_data[chat_id].get("step_index", 0) == 0:
                user_data[chat_id]["ingredient_index"] = 0
                user_states[chat_id] = "maintenance_ingredients"
                ask_ingredient(chat_id)
            else:
                user_data[chat_id]["step_index"] = user_data[chat_id].get("step_index", 0) + 1
                send_current_step(chat_id)
            return jsonify({"ok": True})
        send_message(chat_id, "Dokončete aktuální krok tlačítkem „✅ Hotovo – další krok“, nebo nahlaste problém.", STEP_KEYBOARD)
        return jsonify({"ok": True})

    if state == "maintenance_issue":
        user_data.setdefault(chat_id, {}).setdefault("issues", []).append(text or "[Problém bez textového popisu]")
        send_message(chat_id, "⚠️ Problém zaznamenán. Pokračujte v údržbě tlačítkem níže.", STEP_KEYBOARD)
        user_states[chat_id] = "maintenance_step"
        return jsonify({"ok": True})

    if state == "maintenance_ingredients":
        key, label, pack_size, unit = ingredients[user_data[chat_id]["ingredient_index"]]
        try:
            quantity = float(text.replace(",", "."))
            if quantity < 0:
                raise ValueError
        except ValueError:
            send_message(chat_id, f"Zadejte nezáporné množství v {unit}, například {pack_size} nebo 0.")
            return jsonify({"ok": True})
        user_data[chat_id].setdefault("refill_quantities", {})[key] = quantity / pack_size
        user_data[chat_id][key] = text
        user_data[chat_id]["ingredient_index"] += 1
        ask_ingredient(chat_id)
        return jsonify({"ok": True})

    if state == "maintenance_stocktake":
        key, label = STOCKTAKE_ITEMS[user_data[chat_id]["stocktake_index"]]
        try:
            quantity = float(text.replace(",", "."))
            if quantity < 0:
                raise ValueError
        except ValueError:
            send_message(chat_id, "Zadejte nezáporný počet balení, například 3 nebo 0.", CANCEL_KEYBOARD)
            return jsonify({"ok": True})
        user_data[chat_id]["stocktake"][key] = quantity
        user_data[chat_id]["stocktake_index"] += 1
        ask_stocktake_item(chat_id)
        return jsonify({"ok": True})

    if state == "receipt_field_quantity":
        save_receipt_field_quantity(chat_id, text)
        return jsonify({"ok": True})

    if state == "receipt_syrup_note":
        save_receipt_syrup_note(chat_id, text)
        return jsonify({"ok": True})

    if state == "receipt_product":
        if not text:
            send_message(chat_id, "Zadejte název produktu jako text.")
            return jsonify({"ok": True})
        user_data[chat_id]["current_item"] = {"product": text, "note": ""}
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
        user_data[chat_id]["current_item"].setdefault("note", "")
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
