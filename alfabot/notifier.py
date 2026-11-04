"""Уведомления и управление ботом через Telegram, устойчивые к сбоям связи.

Как это работает:
- send() и send_photo() ничего не ждут: сообщение сразу сохраняется в очередь на диске
  (data/outbox), а отправляет его отдельный фоновый поток. Торговля никогда не ждёт Telegram.
- Если Telegram или интернет недоступны, сообщения копятся в очереди (переживают и перезапуск бота).
  Когда связь вернётся, они уйдут по порядку, склеенные в несколько сообщений, с пометкой
  «отложено» и временем, когда событие произошло.
- Команды (/status и т.д.) принимает отдельный поток. Команды старше 10 минут не выполняются:
  например, /closeall, отправленный, пока бот был выключен, не сработает неожиданно.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

MAX_TEXT = 3900          # лимит Telegram 4096 символов, оставляем запас
MAX_QUEUE = 2000         # больше не храним — самые старые выбрасываются
DELAYED_AFTER_S = 60     # сообщение старше этого считается «отложенным»
COMMAND_MAX_AGE_S = 600


class TelegramError(Exception):
    def __init__(self, msg: str, retry_after: float | None = None, permanent: bool = False):
        super().__init__(msg)
        self.retry_after = retry_after
        self.permanent = permanent


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m %H:%M UTC")


class Notifier:
    def __init__(self, enabled: bool, token: str | None, chat_id: str | None, *,
                 outbox_dir: str = "data/outbox", start_threads: bool = True, transport=None):
        self.enabled = bool(enabled and token and chat_id and (requests or transport))
        self.token = token
        self.chat_id = str(chat_id) if chat_id else None
        self.dir = Path(outbox_dir)
        self._file = self.dir / "outbox.jsonl"
        self._offset_file = self.dir / "tg_offset.txt"
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._commands: queue.Queue[str] = queue.Queue()
        self._transport = transport or self._http
        self.online = True
        self._retry_after: float | None = None
        self._offset = 0
        if self.enabled:
            self.dir.mkdir(parents=True, exist_ok=True)
            try:
                self._offset = int(self._offset_file.read_text().strip())
            except Exception:
                self._offset = 0
            if start_threads:
                threading.Thread(target=self._sender_loop, name="tg-sender", daemon=True).start()
                threading.Thread(target=self._poll_loop, name="tg-poller", daemon=True).start()

    # ------------------------------------------------------------ публичное API
    def send(self, text: str) -> None:
        log.info("NOTIFY: %s", text)
        if self.enabled:
            self._enqueue({"kind": "text", "text": str(text)})

    def send_photo(self, png: bytes | None, caption: str = "") -> None:
        if not (self.enabled and png):
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            name = f"photo_{uuid.uuid4().hex[:10]}.png"
            (self.dir / name).write_bytes(png)
            self._enqueue({"kind": "photo", "file": name, "text": caption[:1000]})
        except Exception as e:
            log.warning("не удалось сохранить скриншот для Telegram: %s", e)

    def poll_commands(self) -> list[str]:
        """Команды, пришедшие с прошлого вызова. Никогда не блокирует."""
        out = []
        while True:
            try:
                out.append(self._commands.get_nowait())
            except queue.Empty:
                return out

    def pending(self) -> int:
        return len(self._load())

    def flush(self, timeout: float = 10.0) -> bool:
        """Попытаться отправить очередь сейчас (для остановки бота)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._load():
                return True
            if not self.deliver_once():
                return False
        return not self._load()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # ------------------------------------------------------------ очередь на диске
    def _load(self) -> list[dict]:
        with self._lock:
            if not self._file.exists():
                return []
            items = []
            for line in self._file.read_text(encoding="utf-8").splitlines():
                try:
                    items.append(json.loads(line))
                except Exception:
                    continue  # повреждённая строка (например, обрыв записи при выключении)
            return items

    def _save(self, items: list[dict]) -> None:
        with self._lock:
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items), encoding="utf-8")
            tmp.replace(self._file)

    def _enqueue(self, item: dict) -> None:
        item.update(id=uuid.uuid4().hex, ts=time.time())
        try:
            with self._lock:
                self.dir.mkdir(parents=True, exist_ok=True)
                with self._file.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            items = self._load()
            if len(items) > MAX_QUEUE:
                for old in items[:-MAX_QUEUE]:
                    self._drop_files(old)
                self._save(items[-MAX_QUEUE:])
        except Exception as e:
            log.warning("очередь Telegram недоступна: %s", e)
        self._wake.set()

    def _drop_files(self, item: dict) -> None:
        if item.get("kind") == "photo":
            try:
                (self.dir / item["file"]).unlink(missing_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------ отправка
    def _http(self, method: str, data: dict, files: dict | None = None, timeout: float = 15) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        try:
            r = requests.post(url, data=data, files=files, timeout=timeout)
        except Exception as e:
            raise TelegramError(f"нет связи: {e}") from e
        try:
            js = r.json()
        except Exception:
            raise TelegramError(f"HTTP {r.status_code}")
        if js.get("ok"):
            return js
        code = js.get("error_code", r.status_code)
        retry = (js.get("parameters") or {}).get("retry_after")
        # 400 — сообщение с ошибкой, повтор не поможет; 401/403/404 — неверный токен или чат: ждём исправления
        raise TelegramError(f"{code}: {js.get('description')}", retry_after=retry, permanent=(code == 400))

    def _batch(self, items: list[dict]) -> tuple[list[dict], dict]:
        """Берёт из начала очереди одно фото или несколько текстов, склеенных в одно сообщение."""
        first = items[0]
        now = time.time()

        def label(it):
            return f"[{_hhmm(it['ts'])}] " if now - it["ts"] > DELAYED_AFTER_S else ""

        if first["kind"] == "photo":
            cap = first.get("text", "")
            if now - first["ts"] > DELAYED_AFTER_S:
                cap = f"⏳ отложено, {_hhmm(first['ts'])}\n{cap}"
            return [first], {"kind": "photo", "file": first["file"], "text": cap[:1000]}
        taken, parts, size = [], [], 0
        for it in items:
            if it["kind"] != "text":
                break
            piece = label(it) + it["text"]
            if taken and size + len(piece) + 2 > MAX_TEXT:
                break
            taken.append(it)
            parts.append(piece[:MAX_TEXT])
            size += len(piece) + 2
            if not label(it):
                break  # свежие сообщения не склеиваем — отправляем по одному
        text = "\n\n".join(parts)
        if any(label(it) for it in taken):
            text = "⏳ Отложено из-за сбоя связи с Telegram:\n\n" + text
        return taken, {"kind": "text", "text": text[:4096]}

    def deliver_once(self) -> bool:
        """Отправляет один пакет. True — успешно (или очередь пуста), False — сбой связи."""
        items = self._load()
        if not items:
            return True
        taken, msg = self._batch(items)
        try:
            if msg["kind"] == "photo":
                path = self.dir / msg["file"]
                if path.exists():
                    with path.open("rb") as f:
                        self._transport("sendPhoto", {"chat_id": self.chat_id, "caption": msg["text"]},
                                        {"photo": ("terminal.png", f.read(), "image/png")})
            else:
                self._transport("sendMessage", {"chat_id": self.chat_id, "text": msg["text"]})
        except TelegramError as e:
            if e.permanent:
                log.warning("Telegram отклонил сообщение, пропускаю: %s", e)
            else:
                if self.online:
                    log.warning("Telegram недоступен (%s) — сообщения копятся в очереди", e)
                self.online = False
                self._retry_after = e.retry_after
                return False
        if not self.online:
            self.online = True
            log.info("связь с Telegram восстановлена")
        ids = {it["id"] for it in taken}
        rest = [it for it in self._load() if it["id"] not in ids]
        self._save(rest)
        for it in taken:
            self._drop_files(it)
        return True

    def _sender_loop(self) -> None:
        backoff = 5.0
        while not self._stop.is_set():
            self._wake.wait(timeout=30)
            self._wake.clear()
            while not self._stop.is_set() and self._load():
                self._retry_after = None
                try:
                    ok = self.deliver_once()
                except Exception as e:  # поток не должен умирать ни при каких ошибках
                    log.warning("ошибка отправки в Telegram: %s", e)
                    ok = False
                if ok:
                    backoff = 5.0
                    time.sleep(1.1)      # не превышать лимит Telegram ~1 сообщение/с в чат
                else:
                    wait = self._retry_after or backoff
                    backoff = min(backoff * 2, 300)
                    self._stop.wait(wait)

    # ------------------------------------------------------------ приём команд
    def _poll_loop(self) -> None:
        backoff = 5.0
        while not self._stop.is_set():
            try:
                js = self._transport("getUpdates", {"offset": self._offset, "timeout": 25}, None, 35)
                self.handle_updates(js.get("result", []))
                backoff = 5.0
            except Exception as e:
                log.debug("опрос команд Telegram: %s", e)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 300)

    def handle_updates(self, updates: list[dict]) -> None:
        now = time.time()
        for u in updates:
            self._offset = max(self._offset, u["update_id"] + 1)
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                continue  # чужие чаты игнорируем
            text = (msg.get("text") or "").strip().split("@")[0].lower()
            if not text.startswith("/"):
                continue
            if now - msg.get("date", now) > COMMAND_MAX_AGE_S:
                self.send(f"⌛ Команда {text} от {_hhmm(msg['date'])} устарела и не выполнена. Отправьте её снова.")
                continue
            self._commands.put(text)
        try:
            self._offset_file.write_text(str(self._offset))
        except Exception:
            pass
