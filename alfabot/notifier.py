"""Уведомления и управление ботом через Telegram (необязательно)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class Notifier:
    def __init__(self, enabled: bool, token: str | None, chat_id: str | None):
        self.enabled = bool(enabled and token and chat_id and requests)
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self._offset = 0

    def send(self, text: str) -> None:
        log.info("NOTIFY: %s", text)
        if not self.enabled:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
        except Exception as e:  # уведомления не должны ронять бота
            log.warning("telegram send failed: %s", e)

    def send_photo(self, png: bytes | None, caption: str = "") -> None:
        if not (self.enabled and png):
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendPhoto",
                data={"chat_id": self.chat_id, "caption": caption[:1000]},
                files={"photo": ("terminal.png", png, "image/png")},
                timeout=30,
            )
        except Exception as e:
            log.warning("telegram photo failed: %s", e)

    def poll_commands(self) -> list[str]:
        """Команды из разрешённого чата (/status /pause /resume /closeall /screenshot /stats)."""
        if not self.enabled:
            return []
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={"offset": self._offset, "timeout": 0},
                timeout=10,
            )
            updates = r.json().get("result", [])
        except Exception as e:
            log.warning("telegram poll failed: %s", e)
            return []
        cmds = []
        for u in updates:
            self._offset = max(self._offset, u["update_id"] + 1)
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                continue  # чужие чаты игнорируем
            text = (msg.get("text") or "").strip().split("@")[0].lower()
            if text.startswith("/"):
                cmds.append(text)
        return cmds
