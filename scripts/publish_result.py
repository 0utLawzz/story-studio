#!/usr/bin/env python3
"""
Logs a render result to a Google Sheet and sends a Telegram notification.
Called from the GitHub Actions workflow after a successful render + Drive
upload (or on failure, with placeholder values).

Usage:
    python scripts/publish_result.py <story_name> <run_number> <drive_link> <release_link> <status>

Needs these environment variables (set as GitHub Secrets in the workflow):
    GDRIVE_SA_FILE       - path to the Google service account JSON key file
    SHEET_ID             - the Google Sheet's ID (from its URL)
    TELEGRAM_BOT_TOKEN   - your Telegram bot's token (from @BotFather)
    TELEGRAM_CHAT_ID     - your Telegram chat ID
"""
import datetime
import os
import sys

import gspread
import requests
from google.oauth2.service_account import Credentials


def log_to_sheet(story_name, run_number, status, drive_link, release_link):
    # The service account must have "Editor" access on this specific Sheet
    # (share the Sheet with the service account's email address -- see
    # setup step A.7 in the chat explanation).
    creds = Credentials.from_service_account_file(
        os.environ["GDRIVE_SA_FILE"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(os.environ["SHEET_ID"]).sheet1
    sheet.append_row([
        story_name,
        run_number,
        status,
        drive_link,
        release_link,
        datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    ])


def notify_telegram(story_name, run_number, status, drive_link, release_link):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    icon = "\u2705" if status == "success" else "\u274c"
    text = (
        f"{icon} *{story_name}* -- run #{run_number} -- {status}\n"
        f"Drive: {drive_link}\n"
        f"Release: {release_link}"
    )
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=20,
    )


def main():
    story_name, run_number, drive_link, release_link, status = sys.argv[1:6]
    # Sheet logging and Telegram notify are kept independent: if the Sheet
    # isn't shared correctly yet (common first-time mistake), you still get
    # the Telegram ping instead of the whole step failing silently.
    try:
        log_to_sheet(story_name, run_number, status, drive_link, release_link)
    except Exception as e:  # noqa: BLE001
        print(f"[publish_result] Sheet logging failed: {e}", file=sys.stderr)
    try:
        notify_telegram(story_name, run_number, status, drive_link, release_link)
    except Exception as e:  # noqa: BLE001
        print(f"[publish_result] Telegram notify failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
