"""Sends notifications via a Telegram bot."""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send_telegram(message):
    """Send one text message to your Telegram chat."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    response = requests.post(url, data=payload)
    response.raise_for_status()   # raise an error if Telegram rejected it
    print("Telegram message sent")


if __name__ == "__main__":
    send_telegram("HarkinsWatch test — if you see this, Telegram works!")
