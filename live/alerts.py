"""Telegram alerts for the live trading bot.

Setup (one-time):
1. Message @BotFather on Telegram, run /newbot, copy the token it gives you.
2. Message your new bot anything, then visit
   https://api.telegram.org/bot<TOKEN>/getUpdates to find your numeric chat id.
3. On the VPS, set environment variables TELEGRAM_BOT_TOKEN and
   TELEGRAM_CHAT_ID (never commit these to the repo).
"""

import json
import os
import urllib.request

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def send(message):
    """Best-effort alert: a failed Telegram send must never crash or
    block the trading loop, so any error here is caught and just
    printed instead of raised.
    """
    print(message)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(request, timeout=10)
    except Exception as e:
        print("[Telegram send failed: %s]" % e)
