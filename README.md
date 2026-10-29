# Personal Finance Bot

A simple personal finance tracker that uses a Telegram bot and GitHub Actions to log expenses and income into a Google Sheet.

- No paid servers — everything runs on GitHub Actions.
- You send a Telegram message like `12.50 lunch food` or `+3000 salary income`.
- The bot logs it to Google Sheets.
- You can also use `/today`, `/month`, or `/undo`.

---

## How it works

1. You send a message to your Telegram bot.
2. The bot saves the Telegram message ID in the sheet.
3. GitHub Actions runs `main.py` every 5 minutes.
4. `main.py` checks Telegram for new messages and writes new ones to Google Sheets.
5. Your Telegram reply confirms the entry.

---

## Quick start

1. Create a Telegram bot and get its token.
2. Get your Telegram chat ID.
3. Prepare a Google Sheet and a Google Cloud service account.
4. Create a GitHub repo, push the code, and add the secrets.
5. Run the workflow manually and test with a Telegram message.

---

## 1. Create the Telegram bot

1. Open Telegram and search for **@BotFather**.
2. Send:

   ```text
   /newbot
   ```

3. Choose a name (example: `MoneyTracker`).
4. Choose a username (example: `money_tracker_bot`).
5. BotFather gives you a token like:

   ```text
   123456:ABC-dEFghIJKLMNOPqRSTuv
   ```

Save this token. You will paste it into GitHub Secrets.

---

## 2. Find your Telegram chat ID

1. Start a chat with your bot in Telegram.
2. Send the bot any message (for example: `hello`).
3. In a browser, open:

   ```text
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```

   Replace `YOUR_BOT_TOKEN` with your bot token.

4. You will see JSON like:

   ```json
   {
     "update_id": 123,
     "message": {
       "chat": {
         "id": 123456789
       }
     }
   }
   ```

5. Copy the number after `"id":`. This is your chat ID.

6. Go to GitHub → **Settings → Secrets and variables → Actions**.
7. Add a secret named `TELEGRAM_CHAT_ID` with that number.

---

## 3. Prepare your Google Sheet

Share the sheet with your Google service account email and note the sheet ID.

1. Open your Google Sheet.
2. Look at the URL: `https://docs.google.com/spreadsheets/d/1A2B3C4D5E6F7G8H9/edit`
3. The sheet ID is:

   ```text
   1A2B3C4D5E6F7G8H9
   ```

You will need this later for the `SPREADSHEET_ID` secret.

---

## 4. Create a Google Cloud service account

This service account gives GitHub permission to edit your sheet.

### Step 1: Enable the Sheets API

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a project, or select an existing one.
3. Search for **Google Sheets API** and enable it.

### Step 2: Create a service account

1. Go to **IAM & Admin → Service Accounts**.
2. Click **Create Service Account**.
3. Give it a name, such as `FinanceBot`.
4. Click **Create and Continue**.
5. Skip roles and click **Done**.

You should see a service account email:

```text
finance-bot@your-project-id.iam.gserviceaccount.com
```

### Step 3: Create a JSON key

1. Click the service account.
2. Go to the **Keys** tab.
3. Click **Add Key → Create new key**.
4. Choose **JSON**.
5. Download the JSON file.

### Step 4: Share the Google Sheet

1. Open your Google Sheet.
2. Click **Share**.
3. Paste the service account email.
4. Give it **Editor** or **Owner** access.
5. Click **Share**.

---

## 5. Create a GitHub repository

1. Go to [GitHub.com](https://github.com) and log in.
2. Click **+** and choose **New repository**.
3. Name it: `Financial`
4. Make it **Public**.
5. Click **Create repository**.

You will push the files from this project into that repository.

---

## 6. Add GitHub Secrets

1. In your new repo, go to **Settings → Secrets and variables → Actions**.
2. Click **New repository secret**.

Add these four secrets:

| Secret | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | The bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Your chat ID from getUpdates |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Paste the entire JSON key content exactly |
| `SPREADSHEET_ID` | The sheet ID from your Google Sheet URL |

> **Important:** Do not add quotes, spaces, or trailing newlines to the JSON secret.

---

## 7. Push the code

On your computer, run:

```bash
cd Financial

git init
git add .
git commit -m "Initial finance bot"

git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/Financial.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your GitHub username.

---

## 8. Test the bot

1. Go to the **Actions** tab in GitHub.
2. Click **Finance Bot**.
3. Click **Run workflow**.
4. Confirm.

Then send a message to your Telegram bot:

```text
12.50 lunch food
```

The bot should reply:

```text
✅ Logged: -12.50 | lunch food | food
```

---

## 9. Try commands

You can send these commands to the bot:

| Command | What it does |
|---|---|
| `/today` | Show today's totals |
| `/month` | Show this month's income, expenses, balance |
| `/undo` | Delete the last entry you added |

---

## Troubleshooting

### `403 permission denied`

The service account does not have permission to edit the sheet.

- Make sure the service account email is a **shared editor** on the sheet.
- Reload the sheet if you changed permissions recently.

### `Invalid token`

The Telegram token is wrong or expired.

- Use @BotFather → **My bots** to copy a fresh token.
- Test the token with:

  ```text
  https://api.telegram.org/botYOUR_TOKEN/getMe
  ```

### Duplicate entries

The script uses Telegram `update_id` to avoid duplicates.
If duplicates still appear, check that the Config tab in your sheet contains the latest `last_processed_update_id`.

### Bot does not reply

- Check that `TELEGRAM_CHAT_ID` is correct.
- Send the bot a message as a normal chat (not in a group, unless the bot is promoted).

### Need to test locally

Run the demo mode locally:

```bash
pip install -r requirements.txt
python main.py
```

Then type transactions like `12.50 lunch food`.

---

## Safety notes

- Never commit `GOOGLE_SERVICE_ACCOUNT_JSON` to the repository.
- Keep GitHub secrets private.
- Read the Telegram `getUpdates` URL in your own browser before trusting it.
