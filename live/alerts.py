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
import time
import urllib.request

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Observed once, on the VPS: the very first send of a freshly started
# process failed with a certificate error, every later send from the same
# process succeeded, and the exact same startup sequence (import
# MetaTrader5, connect to the broker, launch via Start-Process with a
# hidden window and redirected output - matching supervisor.ps1 exactly)
# could not be reproduced in four separate isolated tests. That points to
# a one-time network hiccup around process start (plausibly Windows
# checking a certificate revocation list under the load of several
# libraries importing at once) rather than a real defect - so retrying
# once, briefly, is what actually protects the message doing the failing
# is the "bot started" line, which fires exactly once per process.
RETRY_DELAY_SECONDS = 3


def send(message):
    """Best-effort alert: a failed Telegram send must never crash or
    block the trading loop, so any error here is caught and just
    printed instead of raised. Retries once after RETRY_DELAY_SECONDS
    before giving up, since the one failure seen in practice was a
    transient one - see the note on RETRY_DELAY_SECONDS above.
    """
    print(message)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()

    for attempt in (1, 2):
        request = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            return
        except Exception as e:
            if attempt == 2:
                print("[Telegram send failed after retry: %s]" % e)
            else:
                time.sleep(RETRY_DELAY_SECONDS)
