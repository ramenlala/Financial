#!/usr/bin/env python3
"""
Personal Finance Tracker for Telegram
=====================================
- Sends/receives messages via Telegram Bot.
- Writes transactions to Google Sheets.
- Stores the last processed Telegram update_id in the Config tab.
- Uses GitHub Actions to poll every 5 minutes + manual trigger.

Works in two modes:
  1. Bot mode (requires Telegram secrets) - normally used in GitHub Actions.
  2. Demo mode (no Telegram secrets) - good for local testing / screenshots.
"""

import os
import re
import sys
import time
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SG_TZ = ZoneInfo("Asia/Singapore")

CONFIG_TAB = "Config"
CONFIG_ROW = 2
CONFIG_LAST_UPDATE_ID = "last_processed_update_id"

# Transactions tab and column positions
TRANSACTIONS_HEADER = [
    "Date",
    "Time",
    "Type",
    "Amount",
    "Description",
    "Category",
    "Telegram Message ID",
]

# Telegram chat id is a security check: only this chat id may use the bot.
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "").strip()
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()


# ---------------------------------------------------------------------------
# Settings / environment
# ---------------------------------------------------------------------------

def load_settings():
    """
    Read required settings from the environment.
    Returns:
        str: path to a temporary JSON file containing the service account key.
    """
    load_dotenv()

    required = {
        "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "GOOGLE_SERVICE_ACCOUNT_JSON": SERVICE_ACCOUNT_JSON,
        "SPREADSHEET_ID": SPREADSHEET_ID,
    }

    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"Missing required settings: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    # If the JSON is a string starting with '{', treat it as inline JSON
    # and write it to a temporary file (needed by gspread).
    if SERVICE_ACCOUNT_JSON.startswith("{"):
        fd, tmp_path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(SERVICE_ACCOUNT_JSON)
        return tmp_path

    # Otherwise assume it is a path to an existing JSON key file.
    return SERVICE_ACCOUNT_JSON


# ---------------------------------------------------------------------------
# Google Sheets helpers
# ---------------------------------------------------------------------------

def get_sheet_context(service_account_path, spreadsheet_id):
    """
    Open the Google Sheet, ensure Config and Transactions tabs exist.
    """
    creds = Credentials.from_service_account_file(
        service_account_path,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )

    client = gspread.authorize(creds)
    sheet = client.open_by_key(spreadsheet_id)

    # Ensure Config tab exists.
    if CONFIG_TAB not in sheet.sheetnames:
        sheet.add_worksheet(title=CONFIG_TAB, rows=1, cols=2)
        sheet.update_cell(CONFIG_TAB, 1, 1, "last_processed_update_id")
        sheet.update_cell(CONFIG_TAB, 1, 2, 0)

    config_ws = sheet.worksheet(CONFIG_TAB)

    # Ensure Transactions tab exists.
    if "Transactions" not in sheet.sheetnames:
        sheet.add_worksheet(title="Transactions", rows=1, cols=len(TRANSACTIONS_HEADER))
        transactions_ws = sheet.worksheet("Transactions")
        transactions_ws.update_row(1, TRANSACTIONS_HEADER)
    else:
        transactions_ws = sheet.worksheet("Transactions")

    return sheet, config_ws, transactions_ws


def read_last_update_id(config_ws):
    """
    Read the last_processed_update_id from the Config tab.
    """
    try:
        value = config_ws.cell(CONFIG_ROW, 2).value
        return int(value)
    except (TypeError, ValueError):
        return 0


def write_last_update_id(config_ws, update_id):
    """
    Save the last_processed_update_id to the Config tab.
    """
    config_ws.update_cell(CONFIG_ROW, 2, int(update_id))


def create_row_values(message_id, parsed):
    """
    Build a row for the Transactions tab from parsed data.
    """
    amount = parsed["amount"]
    if parsed["type"] == "expense":
        amount_for_sheet = -abs(amount)
        sheet_type = "Expense"
    else:
        amount_for_sheet = abs(amount)
        sheet_type = "Income"

    date_str = datetime.now(SG_TZ).strftime("%Y-%m-%d")
    time_str = datetime.now(SG_TZ).strftime("%H:%M:%S")

    return [
        date_str,
        time_str,
        sheet_type,
        amount_for_sheet,
        parsed["description"],
        parsed["category"],
        message_id,
    ]


def append_transaction(transactions_ws, row_values):
    """
    Append a row to the Transactions tab.
    """
    transactions_ws.append_row(row_values)


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def get_bot(token):
    """
    Return a Telegram Bot instance.
    """
    from telegram import Bot
    from telegram.error import TelegramError

    return Bot(token=token)


def send_telegram_reply(bot, chat_id, text):
    """
    Send a message back to the user in Telegram.
    """
    try:
        bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        print(f"Failed to send Telegram reply: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def parse_message(text):
    """
    Parse a user message into a transaction.

    Expected formats:
      - "12.50 lunch food"      -> expense (signless amount is expense)
      - "+3000 salary income"   -> income
      - "-12.50 lunch food"     -> expense (explicit minus)

    Returns:
        dict or None if parsing failed.
    """
    text = text.strip()
    if not text:
        return None

    sign = None
    amount_text = text

    if text.startswith("+"):
        sign = "income"
        amount_text = text[1:]
    elif text.startswith("-"):
        sign = "expense"
        amount_text = text[1:]

    # Known categories (you can add more).
    known_categories = {
        "food", "income", "transport", "shopping", "bills",
        "health", "entertainment", "other", "uncategorized",
    }

    tokens = amount_text.split()
    if not tokens:
        return None

    # If last token is a known category, treat it as the category.
    if tokens[-1].lower() in known_categories:
        category = tokens[-1].lower()
        description = " ".join(tokens[:-1]).strip()
    else:
        category = "Uncategorized"
        description = amount_text.strip()

    # If description is empty after extracting category, use the category
    # as the description.
    if not description:
        description = category
        category = "Uncategorized"

    amount = None
    if is_number(description):
        amount = float(description)
    else:
        return None

    # Default type based on sign.
    if sign == "income":
        tx_type = "income"
    elif sign == "expense":
        tx_type = "expense"
    else:
        # No sign: amount is treated as expense.
        tx_type = "expense"

    return {
        "type": tx_type,
        "amount": amount,
        "description": description,
        "category": category,
    }


def is_number(s):
    """
    Return True if s is a valid number (int or float).
    """
    s = s.strip()
    if not s:
        return False
    if s.count(".") > 1:
        return False
    try:
        float(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def handle_command(command, chat_id, transactions_ws):
    """
    Handle /today, /month, /undo.
    """
    command = command.strip().lower()

    if command == "/today":
        totals = calculate_totals(transactions_ws, "today")
        reply = (
            "📅 Today Totals\n"
            f"💰 Income: ${totals['income_total']:,.2f}\n"
            f"🧾 Expenses: ${totals['expense_total']:,.2f}\n"
            f"⚖️ Balance: ${totals['balance']:,.2f}"
        )
        send_telegram_reply(chat_id, reply)

    elif command == "/month":
        totals = calculate_totals(transactions_ws, "month")
        reply = (
            "📆 Month Totals\n"
            f"💰 Income: ${totals['income_total']:,.2f}\n"
            f"🧾 Expenses: ${totals['expense_total']:,.2f}\n"
            f"⚖️ Balance: ${totals['balance']:,.2f}"
        )
        send_telegram_reply(chat_id, reply)

    elif command == "/undo":
        deleted = undo_last_transaction(transactions_ws, chat_id)
        if deleted:
            send_telegram_reply(chat_id, "🗑️ Last entry deleted.")
        else:
            send_telegram_reply(chat_id, "⚠️ No entry found to undo.")

    else:
        send_telegram_reply(
            chat_id,
            "Commands: /today, /month, /undo",
        )


def calculate_totals(transactions_ws, period):
    """
    Calculate totals from the Transactions tab.
    This is a simple version that sums all rows. You can extend this later
    to filter by date if you want strict /today and /month behaviour.
    """
    rows = transactions_ws.get_all_values()

    income = 0.0
    expense = 0.0

    for row in rows[1:]:
        if len(row) < 4:
            continue

        type_text = row[2].lower() if row[2] else ""
        amount_text = row[3]

        try:
            amount = float(amount_text)
        except ValueError:
            continue

        if type_text == "income":
            income += amount
        elif type_text == "expense":
            expense += amount

    balance = income - expense

    return {
        "income_total": round(income, 2),
        "expense_total": round(expense, 2),
        "balance": round(balance, 2),
    }


def undo_last_transaction(transactions_ws, chat_id):
    """
    Delete the most recent transaction row that belongs to this chat.
    The Telegram Message ID is stored in column G (index 6).
    """
    rows = transactions_ws.get_all_values()

    if not rows:
        return False

    # Search from the most recent row backwards.
    for i in range(len(rows) - 1, -1, -1):
        row = rows[i]
        if len(row) > 6 and str(row[6]).strip() == str(chat_id):
            sheet_row = i + 1  # convert to 1-based row number
            transactions_ws.delete_rows(sheet_row)
            return True

    return False


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

def process_message(bot, parsed, chat_id, message_id, transactions_ws):
    """
    Write a parsed message to Google Sheets and reply to the user.
    """
    row_values = create_row_values(message_id, parsed)
    append_transaction(transactions_ws, row_values)

    amount_text = f"-{abs(parsed['amount'])}" if parsed["type"] == "expense" else f"+{parsed['amount']}"
    reply = (
        f"✅ Logged: {amount_text} "
        f"| {parsed['description']} "
        f"| {parsed['category']}"
    )
    send_telegram_reply(bot, chat_id, reply)


def handle_update(bot, update, chat_id, transactions_ws, config_ws, last_processed_id):
    """
    Process one Telegram update if it is a message from the allowed chat.
    """
    # Only act on message updates.
    if not update.message:
        return last_processed_id

    try:
        message = update.message
        message_chat_id = message.chat.id
    except Exception:
        return last_processed_id

    # Security: ignore messages from any chat other than the allowed one.
    if chat_id != str(message_chat_id):
        send_telegram_reply(
            bot,
            message_chat_id,
            "This bot only accepts messages from your Telegram account.",
        )
        return last_processed_id

    # Skip already processed updates.
    if update.update_id <= last_processed_id:
        return last_processed_id

    # Handle commands.
    text = message.text.strip() if message.text else ""
    if text.startswith("/"):
        handle_command(text, message_chat_id, transactions_ws)
        return last_processed_id

    # Parse normal message.
    parsed = parse_message(text)
    if parsed is None:
        send_telegram_reply(
            bot,
            message_chat_id,
            "⚠️ Could not understand that.\n"
            "Try: '12.50 lunch food' or '+3000 salary income'",
        )
        return last_processed_id

    # Save to sheet and reply.
    process_message(bot, parsed, message_chat_id, message.message_id, transactions_ws)

    # Update the last processed update id.
    last_processed_id = update.update_id
    write_last_update_id(config_ws, last_processed_id)

    return last_processed_id


def poll_telegram(bot, transactions_ws, config_ws, chat_id):
    """
    Poll Telegram for new updates every 5 minutes and process them.
    """
    last_processed_id = read_last_update_id(config_ws)

    while True:
        try:
            # offset = last_processed_id + 1 means "only give me new updates".
            updates = bot.get_updates(
                offset=last_processed_id + 1,
                timeout=60,
                allowed_updates=["message"],
            )
        except Exception as e:
            print(f"Telegram getUpdates error: {e}", file=sys.stderr)
            last_processed_id = read_last_update_id(config_ws)
            time.sleep(60)
            continue

        if not updates:
            time.sleep(300)
            continue

        for update in updates:
            last_processed_id = handle_update(
                bot,
                update,
                chat_id,
                transactions_ws,
                config_ws,
                last_processed_id,
            )

        # Save on exit from loop.
        write_last_update_id(config_ws, last_processed_id)
        time.sleep(300)


# ---------------------------------------------------------------------------
# Demo mode (for local testing without Telegram)
# ---------------------------------------------------------------------------

def demo_command_line_mode():
    """
    Allow the user to run simple commands locally without Telegram.
    """
    from io import StringIO

    # Fake Telegram chat id for local demo.
    DUMMY_CHAT_ID = 123456789

    _, config_ws, transactions_ws = get_sheet_context(
        load_settings(), SPREADSHEET_ID
    )

    print("Financial bot - demo mode")
    print("Type your transactions or commands.")
    print("Commands: /today, /month, /undo, /quit")
    print("Example: 12.50 lunch food")
    print("----------------------")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if user_input.lower() in ("/quit", "/exit"):
            break

        if not user_input:
            continue

        # Handle commands.
        if user_input.lower().startswith("/"):
            handle_command(user_input, DUMMY_CHAT_ID, transactions_ws)
            continue

        # Try to parse as normal transaction.
        parsed = parse_message(user_input)
        if parsed is None:
            print("⚠️ Could not understand that. Try: '12.50 lunch food'")
            continue

        process_message(None, parsed, DUMMY_CHAT_ID, None, transactions_ws)
        print("OK")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """
    Entry point.
    """
    # If Telegram secrets are present, run the bot.
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print("Starting finance bot (Telegram mode)...")
        try:
            _, config_ws, transactions_ws = get_sheet_context(
                load_settings(), SPREADSHEET_ID
            )
            bot = get_bot(TELEGRAM_BOT_TOKEN)
            poll_telegram(bot, transactions_ws, config_ws, TELEGRAM_CHAT_ID)
        except Exception as e:
            print(f"Bot failed: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        # No Telegram secrets -> run demo command line mode.
        print("No Telegram secrets found. Running in demo mode.")
        print("Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to your .env or GitHub secrets.")
        print("Run without Telegram to use the local demo below.\n")
        demo_command_line_mode()


if __name__ == "__main__":
    main()
