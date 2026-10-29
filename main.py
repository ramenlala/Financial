#!/usr/bin/env python3
"""
Personal Finance Tracker: Telegram -> Google Sheets
===================================================
Runs once per GitHub Actions run (every 5 minutes + manual trigger):
  1. Reads the last processed Telegram update_id from the "Config" tab.
  2. Fetches new messages from Telegram (getUpdates).
  3. Logs transactions / answers commands, replying in Telegram.
  4. Saves the new last update_id, then exits.

Message formats:
  12.50 lunch food        -> expense, description "lunch", category "food"
  +3000 salary income     -> income
  8 coffee                -> expense, category "Uncategorized"
Commands: /today  /month  /undo  /help
"""

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import requests
from google.oauth2.service_account import Credentials

SG_TZ = ZoneInfo("Asia/Singapore")

TRANSACTIONS_TAB = "Transactions"
CONFIG_TAB = "Config"
HEADER = ["Date", "Time", "Type", "Amount", "Description", "Category", "Telegram Message ID"]

# Words treated as a category when they are the LAST word of a message.
KNOWN_CATEGORIES = {
    "food", "income", "transport", "shopping", "bills", "health",
    "entertainment", "groceries", "rent", "travel", "other", "salary",
}

HELP_TEXT = (
    "How to log:\n"
    "  12.50 lunch food  → expense\n"
    "  +3000 salary income  → income\n"
    "Commands: /today /month /undo"
)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_settings():
    names = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "SPREADSHEET_ID"]
    settings = {n: os.getenv(n, "").strip() for n in names}
    missing = [n for n, v in settings.items() if not v]
    if missing:
        sys.exit(f"Missing GitHub secrets: {', '.join(missing)}")
    return settings


# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------

def open_sheets(service_account_json, spreadsheet_id):
    """Open the spreadsheet and make sure both tabs exist."""
    try:
        info = json.loads(service_account_json)
    except json.JSONDecodeError:
        sys.exit("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the whole key file contents.")

    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    spreadsheet = gspread.authorize(creds).open_by_key(spreadsheet_id)
    existing = {ws.title for ws in spreadsheet.worksheets()}

    if TRANSACTIONS_TAB not in existing:
        ws = spreadsheet.add_worksheet(title=TRANSACTIONS_TAB, rows=1000, cols=len(HEADER))
        ws.append_row(HEADER)
    if CONFIG_TAB not in existing:
        ws = spreadsheet.add_worksheet(title=CONFIG_TAB, rows=10, cols=2)
        ws.update(range_name="A1:B1", values=[["last_update_id", 0]])

    return spreadsheet.worksheet(TRANSACTIONS_TAB), spreadsheet.worksheet(CONFIG_TAB)


def read_last_update_id(config_ws):
    try:
        return int(config_ws.acell("B1").value or 0)
    except (TypeError, ValueError):
        return 0


def write_last_update_id(config_ws, update_id):
    config_ws.update(range_name="B1", values=[[int(update_id)]])


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}"

    def get_updates(self, offset):
        r = requests.get(
            f"{self.base}/getUpdates",
            params={"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])},
            timeout=30,
        )
        data = r.json()
        if not data.get("ok"):
            sys.exit(f"Telegram error: {data.get('description')} (check TELEGRAM_BOT_TOKEN)")
        return data["result"]

    def reply(self, chat_id, text):
        try:
            requests.post(f"{self.base}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=30)
        except requests.RequestException as e:
            print(f"Could not send Telegram reply: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_message(text):
    """Turn '12.50 lunch food' into a transaction dict, or None if invalid."""
    parts = text.strip().split()
    if not parts:
        return None

    amount_text = parts[0].replace("$", "").replace(",", "")
    tx_type = "Expense"
    if amount_text.startswith("+"):
        tx_type = "Income"
        amount_text = amount_text[1:]
    elif amount_text.startswith("-"):
        amount_text = amount_text[1:]

    try:
        amount = round(float(amount_text), 2)
    except ValueError:
        return None
    if amount <= 0:
        return None

    words = parts[1:]
    category = "Uncategorized"
    if len(words) >= 2 and words[-1].lower() in KNOWN_CATEGORIES:
        category = words.pop().lower()
    elif len(words) >= 2:
        # Last word is the category even if it's not in the known list.
        category = words.pop().lower()
    description = " ".join(words) or category

    return {"type": tx_type, "amount": amount, "description": description, "category": category}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def totals(transactions_ws, date_prefix):
    """Sum income/expense for rows whose Date starts with date_prefix."""
    income = expense = 0.0
    for row in transactions_ws.get_all_values()[1:]:
        if len(row) < 4 or not row[0].startswith(date_prefix):
            continue
        try:
            amount = float(str(row[3]).replace(",", ""))
        except ValueError:
            continue
        if row[2] == "Income":
            income += amount
        elif row[2] == "Expense":
            expense += amount
    return income, expense


def handle_command(text, tg, chat_id, transactions_ws):
    command = text.split()[0].split("@")[0].lower()
    now = datetime.now(SG_TZ)

    if command in ("/today", "/month"):
        prefix = now.strftime("%Y-%m-%d") if command == "/today" else now.strftime("%Y-%m")
        label = "Today" if command == "/today" else now.strftime("%B %Y")
        income, expense = totals(transactions_ws, prefix)
        bal = income - expense
        bal_text = f"-${abs(bal):,.2f}" if bal < 0 else f"${bal:,.2f}"
        tg.reply(chat_id, (
            f"📅 {label}\n"
            f"💰 Income: ${income:,.2f}\n"
            f"🧾 Spent: ${expense:,.2f}\n"
            f"⚖️ Balance: {bal_text}"
        ))
    elif command == "/undo":
        rows = transactions_ws.get_all_values()
        if len(rows) <= 1:
            tg.reply(chat_id, "⚠️ Nothing to undo.")
            return
        last = rows[-1]
        transactions_ws.delete_rows(len(rows))
        tg.reply(chat_id, f"🗑️ Deleted: {last[2]} ${last[3]} | {last[4]} | {last[5]}")
    else:
        tg.reply(chat_id, HELP_TEXT)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    s = get_settings()
    tg = Telegram(s["TELEGRAM_BOT_TOKEN"])
    transactions_ws, config_ws = open_sheets(s["GOOGLE_SERVICE_ACCOUNT_JSON"], s["SPREADSHEET_ID"])

    last_id = read_last_update_id(config_ws)
    updates = tg.get_updates(offset=last_id + 1)
    print(f"Last processed update: {last_id}. New updates: {len(updates)}")

    for update in updates:
        update_id = update["update_id"]
        message = update.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()

        try:
            if chat_id is None or not text:
                pass
            elif str(chat_id) != s["TELEGRAM_CHAT_ID"]:
                print(f"Ignored message from unknown chat {chat_id}")
            elif text.startswith("/"):
                handle_command(text, tg, chat_id, transactions_ws)
            else:
                tx = parse_message(text)
                if tx is None:
                    tg.reply(chat_id, "⚠️ Couldn't read that.\n" + HELP_TEXT)
                else:
                    now = datetime.fromtimestamp(message.get("date", 0), SG_TZ)
                    transactions_ws.append_row([
                        now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), tx["type"],
                        tx["amount"], tx["description"], tx["category"], message.get("message_id"),
                    ], value_input_option="USER_ENTERED")
                    sign = "+" if tx["type"] == "Income" else "-"
                    tg.reply(chat_id, f"✅ Logged: {sign}${tx['amount']:,.2f} | {tx['description']} | {tx['category']}")
        finally:
            # Save progress after every message so nothing is logged twice.
            write_last_update_id(config_ws, update_id)

    print("Done.")


if __name__ == "__main__":
    main()
