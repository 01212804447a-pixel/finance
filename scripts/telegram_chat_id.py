"""Узнать свой chat_id: напишите своему боту любое сообщение и запустите
    python scripts/telegram_chat_id.py <TOKEN>
"""
import sys

import requests

token = sys.argv[1] if len(sys.argv) > 1 else input("Токен бота от @BotFather: ").strip()
r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15).json()
if not r.get("ok"):
    sys.exit(f"Ошибка: {r}")
chats = {(u.get("message") or {}).get("chat", {}).get("id"): (u.get("message") or {}).get("chat", {})
         for u in r["result"] if u.get("message")}
if not chats:
    sys.exit("Сообщений нет. Напишите своему боту в Telegram любое слово и запустите снова.")
for cid, chat in chats.items():
    print(f"chat_id = {cid}  ({chat.get('first_name', '')} @{chat.get('username', '')})")
