import time

import pytest

from alfabot.notifier import Notifier, TelegramError


class FakeTG:
    """Подменяет Telegram: может «лежать», отклонять сообщения, тормозить."""
    def __init__(self):
        self.down = False
        self.sent = []
        self.reject_next = False
        self.delay = 0.0

    def __call__(self, method, data, files=None, timeout=15):
        if self.delay:
            time.sleep(self.delay)
        if self.down:
            raise TelegramError("нет связи")
        if self.reject_next:
            self.reject_next = False
            raise TelegramError("400: Bad Request", permanent=True)
        self.sent.append((method, data.get("text") or data.get("caption"), files))
        return {"ok": True, "result": []}


def make(tmp_path, tg, threads=False):
    return Notifier(True, "t", "42", outbox_dir=str(tmp_path / "outbox"), start_threads=threads, transport=tg)


def test_messages_survive_outage_and_restart(tmp_path):
    tg = FakeTG()
    tg.down = True
    n = make(tmp_path, tg)
    n.send("🟢 BUY EURUSD")
    n.send_photo(b"\x89PNG fake", "экран")
    n.send("↩️ закрыта")
    assert n.deliver_once() is False and n.pending() == 3 and not n.online

    n2 = make(tmp_path, tg)                      # бот перезапустился, очередь на диске
    assert n2.pending() == 3
    for it in (items := n2._load()):
        it["ts"] -= 3600                         # события были час назад
    n2._save(items)
    tg.down = False
    assert n2.flush(5)
    assert n2.pending() == 0
    texts = [t for _, t, _ in tg.sent]
    assert texts[0].startswith("⏳ Отложено") and "BUY EURUSD" in texts[0]
    assert tg.sent[1][0] == "sendPhoto" and "отложено" in tg.sent[1][1]
    assert "закрыта" in texts[2]
    assert not list((tmp_path / "outbox").glob("photo_*.png"))   # файлы скриншотов удалены


def test_backlog_is_glued_into_few_messages(tmp_path):
    tg = FakeTG()
    n = make(tmp_path, tg)
    for i in range(30):
        n.send(f"🕐 отчёт {i}")
    items = n._load()
    for it in items:
        it["ts"] -= 7200
    n._save(items)
    assert n.flush(5)
    assert len(tg.sent) == 1 and "отчёт 0" in tg.sent[0][1] and "отчёт 29" in tg.sent[0][1]


def test_long_text_and_rejected_message(tmp_path):
    tg = FakeTG()
    n = make(tmp_path, tg)
    tg.reject_next = True
    n.send("плохое")
    n.send("x" * 10000)
    assert n.flush(5)
    assert len(tg.sent) == 1 and len(tg.sent[0][1]) <= 4096


def test_send_never_blocks_trading(tmp_path):
    tg = FakeTG()
    tg.delay = 3.0
    tg.down = True
    n = make(tmp_path, tg, threads=True)
    t0 = time.time()
    for i in range(20):
        n.send(f"m{i}")
    assert time.time() - t0 < 1.0
    n.stop()


def test_stale_and_foreign_commands(tmp_path):
    tg = FakeTG()
    n = make(tmp_path, tg)
    now = int(time.time())
    n.handle_updates([
        {"update_id": 1, "message": {"chat": {"id": 42}, "date": now, "text": "/status"}},
        {"update_id": 2, "message": {"chat": {"id": 42}, "date": now - 3600, "text": "/closeall"}},
        {"update_id": 3, "message": {"chat": {"id": 7}, "date": now, "text": "/closeall"}},
    ])
    assert n.poll_commands() == ["/status"]
    assert any("устарела" in it["text"] for it in n._load())
    assert make(tmp_path, tg)._offset == 4                     # offset сохранён между перезапусками


def test_disabled_does_nothing(tmp_path):
    n = Notifier(False, None, None, outbox_dir=str(tmp_path / "o"), start_threads=False)
    n.send("x")
    assert n.poll_commands() == [] and not (tmp_path / "o").exists()
