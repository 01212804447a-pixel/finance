"""Управление веб-терминалом MetaTrader 5 (trade.alfaforex.ru/terminal) через браузер.

Селекторы проверены на живом веб-терминале MetaQuotes (тот же движок, что у Альфа-Форекс):
вход, чтение баланса и позиций, котировки, открытие ордера с SL/TP, изменение стопа, закрытие.
Все селекторы собраны в одном месте (класс Sel), чтобы их было легко поправить после
обновления терминала.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

log = logging.getLogger(__name__)


class Sel:
    login_input = "input[name=login]"
    password_input = "input[name=password]"
    connect_button_text = "Connect to account"
    summary_row = ".bot-panel .tr.sticky"
    summary_currency = ".bot-panel .tr.sticky span.bold"
    trade_tab = '[title="Trade"]'
    position_rows = ".bot-panel .tr[data-id]"
    search_input = 'input[placeholder="Search symbol"]'
    search_item = "button.item"
    search_close = "label.search button.close"
    add_symbol_title = "Add Symbol"
    new_order_button = '[title^="Show Trade Form"]'
    form_panel = ".left-panel"
    form_type_select = ".left-panel select"
    form_volume = ".left-panel .market .volume input"
    form_sl = ".left-panel .market .sl input"
    form_tp = ".left-panel .market .tp input"
    form_comment = ".left-panel .comment input"
    trade_button = "button.trade-button:visible"
    modify_sl = ".modify-form .sl input"
    modify_tp = ".modify-form .tp input"


class TerminalError(RuntimeError):
    pass


@dataclass
class WebPosition:
    ticket: int
    symbol: str
    side: int
    volume: float
    price_open: float
    sl: float
    tp: float
    price_current: float
    profit: float
    comment: str


_NUM_RE = re.compile(r"-?[\d\s  ]*[.,]?\d+")


def parse_number(text: str | None) -> float:
    """'100 000.00' / '100 000,00' / '-0.33' -> float; пусто -> 0.0"""
    if not text:
        return 0.0
    t = text.replace(" ", "").replace(" ", "").replace(" ", "").strip()
    if not t:
        return 0.0
    if "," in t and "." not in t:
        t = t.replace(",", ".")
    t = t.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else 0.0


def parse_summary(text: str) -> dict:
    """Строка итогов под таблицей позиций: 'Balance: 100 000.00 Equity: ... Margin: ... Free margin: ...'"""
    out = {}
    for key, name in [("balance", "Balance"), ("equity", "Equity"), ("margin", "Margin"),
                      ("margin_free", "Free margin"), ("credit", "Credit")]:
        m = re.search(rf"(?<!Free ){name}:\s*(-?[\d\s  .,]+)", text)
        if m:
            out[key] = parse_number(m.group(1))
    return out


RESULT_RE = re.compile(r"#(\d+)\s+(buy|sell)\s+([\d.]+)\s+(\S+)\s+at\s+([\d.]+)", re.I)


def parse_result(text: str) -> dict | None:
    """'#11237443910 buy 0.01 EURUSD at 1.12599 | Done | OK' -> dict"""
    m = RESULT_RE.search(text)
    if not m:
        return None
    return {"ticket": int(m.group(1)), "side": 1 if m.group(2).lower() == "buy" else -1,
            "volume": float(m.group(3)), "symbol": m.group(4), "price": float(m.group(5))}


def rows_to_positions(headers: list[str], rows: list[dict]) -> list[WebPosition]:
    idx = {h: i for i, h in enumerate(headers)}

    def cell(cells, name):
        i = idx.get(name)
        return cells[i] if i is not None and i < len(cells) else ""

    out = []
    for r in rows:
        c = r["cells"]
        typ = cell(c, "Type").strip().lower()
        if typ not in ("buy", "sell"):
            continue  # отложенные ордера и прочее
        out.append(WebPosition(
            ticket=int(r["id"]),
            symbol=cell(c, "Symbol").strip(),
            side=1 if typ == "buy" else -1,
            volume=parse_number(cell(c, "Volume")),
            price_open=parse_number(cell(c, "Open Price")),
            sl=parse_number(cell(c, "Stop Loss")),
            tp=parse_number(cell(c, "Take Profit")),
            price_current=parse_number(cell(c, "Close Price")),
            profit=parse_number(cell(c, "Profit")),
            comment=cell(c, "Comment").strip(),
        ))
    return out


def with_lang(url: str, lang: str = "en") -> str:
    u = urlparse(url)
    q = dict(parse_qsl(u.query))
    q.setdefault("lang", lang)
    return urlunparse(u._replace(query=urlencode(q)))


_READ_TABLE_JS = """() => {
  const ths = [...document.querySelectorAll('.bot-panel .th[title]')].map(e => e.title);
  const rows = [...document.querySelectorAll('.bot-panel .tr[data-id]')].map(r => ({
    id: r.dataset.id,
    cells: [...r.querySelectorAll(':scope > .td')].map(td => td.getAttribute('title') ?? td.innerText)
  }));
  return {ths, rows};
}"""

_READ_QUOTE_JS = """(sym) => {
  const ths = [...document.querySelectorAll('tr th[title]')].map(e => e.title);
  const row = document.querySelector(`tr[title="${sym}"]`);
  if (!row) return null;
  const tds = [...row.querySelectorAll(':scope > td')].map(td => td.innerText.trim());
  return {ths, tds};
}"""


_RESULT_JS = """() => {
  const ok = [...document.querySelectorAll('button.trade-button')]
    .find(b => b.offsetParent && b.innerText.trim() === 'OK');
  if (!ok) return null;
  const box = ok.closest('.status') || ok.closest('.left-panel') || ok.parentElement;
  return box.innerText;
}"""


class WebTerminal:
    """Один браузер, одна вкладка с терминалом. Профиль браузера сохраняется на диск."""

    def __init__(self, url: str, login: str | None, password: str | None, *,
                 headless: bool = True, profile_dir: str = "browser_profile",
                 action_timeout_ms: int = 15000):
        self.url = with_lang(url)
        self.login_value = str(login) if login else None
        self.password_value = password
        self.headless = headless
        self.profile_dir = profile_dir
        self.timeout = action_timeout_ms
        self._pw = None
        self._ctx = None
        self.page = None

    # ---------- жизненный цикл ----------
    def start(self) -> None:
        from playwright.sync_api import sync_playwright
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            self.profile_dir, headless=self.headless, locale="en-US",
            viewport={"width": 1600, "height": 1000},
        )
        self._ctx.set_default_timeout(self.timeout)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self.open()

    def open(self) -> None:
        self.page.goto(self.url, timeout=90000)
        self.ensure_session(wait_s=90)

    def stop(self) -> None:
        for obj in (self._ctx, self._pw):
            try:
                if obj is self._pw and obj:
                    obj.stop()
                elif obj:
                    obj.close()
            except Exception:
                pass
        self._ctx = self._pw = self.page = None

    def restart(self) -> None:
        self.stop()
        self.start()

    def _logged_in(self) -> bool:
        return self.page.locator(Sel.summary_row).filter(has_text="Balance:").count() > 0

    def _login_form_visible(self) -> bool:
        loc = self.page.locator(Sel.login_input)
        return loc.count() > 0 and loc.first.is_visible()

    def ensure_session(self, wait_s: float = 60) -> None:
        """Дожидается терминала; при необходимости вводит логин/пароль."""
        deadline = time.time() + wait_s
        tried_login = False
        while time.time() < deadline:
            if self._logged_in():
                return
            if self._login_form_visible():
                if tried_login:
                    text = self.page.inner_text("body")[:300]
                    if "invalid" in text.lower() or "неверн" in text.lower():
                        raise TerminalError("неверный логин или пароль")
                elif not (self.login_value and self.password_value):
                    raise TerminalError("нужен вход в счёт: задайте ALFA_LOGIN и ALFA_PASSWORD")
                else:
                    log.info("вход в счёт %s", self.login_value)
                    self.page.locator(f"{Sel.login_input}:visible").first.fill(self.login_value)
                    pwd = self.page.locator(f"{Sel.password_input}:visible").first
                    pwd.fill(self.password_value)
                    btn = self.page.locator("button[type=submit]:visible", has_text=Sel.connect_button_text)
                    try:
                        if btn.count():
                            btn.first.click(timeout=5000)
                        else:
                            pwd.press("Enter")
                    except Exception as e:  # кнопка исчезла — вход уже идёт
                        log.debug("click connect: %s", e)
                    tried_login = True
            elif self.page.locator(Sel.trade_tab).count() and not self._logged_in():
                # терминал открыт, но нижняя панель «Торговля» скрыта
                try:
                    self.page.locator(Sel.trade_tab).first.click()
                except Exception:
                    pass
            self.page.wait_for_timeout(1000)
        raise TerminalError("терминал не загрузился (нет строки баланса)")

    # ---------- чтение ----------
    @property
    def title(self) -> str:
        return self.page.title()

    def is_demo(self) -> bool:
        return "demo account" in self.title.lower()

    def account(self) -> dict:
        self.ensure_session()
        text = self.page.locator(Sel.summary_row).first.inner_text()
        info = parse_summary(text)
        cur = self.page.locator(Sel.summary_currency)
        info["currency"] = cur.first.inner_text().strip() if cur.count() else ""
        m = re.match(r"\s*(\d+)\s*-\s*([^:]+):", self.title)
        info["login"] = int(m.group(1)) if m else 0
        info["server"] = m.group(2).strip() if m else ""
        if "equity" not in info:
            raise TerminalError(f"не удалось прочитать средства: {text!r}")
        return info

    def positions(self) -> list[WebPosition]:
        self.ensure_session()
        data = self.page.evaluate(_READ_TABLE_JS)
        return rows_to_positions(data["ths"], data["rows"])

    def _market_watch_has(self, symbol: str) -> bool:
        return self.page.locator(f'tr[title="{symbol}"]').count() > 0

    def search_symbol(self, symbol: str, *, add: bool = False, open_chart: bool = True) -> str:
        """Ищет символ. open_chart — переключить на него график, add — добавить в обзор рынка.
        Возвращает точное имя у брокера (с суффиксом, если он есть)."""
        s = self.page.locator(Sel.search_input).first
        s.click()
        s.fill("")
        s.press_sequentially(symbol, delay=30)
        items = self.page.locator(Sel.search_item)
        try:
            items.first.wait_for(timeout=8000)
        except Exception:
            self._exit_search()
            raise TerminalError(f"символ {symbol} не найден")
        names = [t.split("\n")[0].strip() for t in items.evaluate_all("els => els.map(e => e.title)")]
        exact = [i for i, n in enumerate(names) if n.upper() == symbol.upper()]
        pref = [i for i, n in enumerate(names) if n.upper().startswith(symbol.upper())]
        pick = exact[0] if exact else (sorted(pref, key=lambda i: len(names[i]))[0] if pref else None)
        if pick is None:
            self._exit_search()
            raise TerminalError(f"символ {symbol} не найден (есть: {names[:5]})")
        name = names[pick]
        item = items.nth(pick)
        if add:
            btn = item.locator(f'[title="{Sel.add_symbol_title}"]')
            if btn.count():
                btn.first.click()
                self.page.wait_for_timeout(500)
        if open_chart:
            item.click()
            self.page.wait_for_timeout(800)
        self._exit_search()
        return name

    def _exit_search(self) -> None:
        """Вернуть правую панель из режима поиска в обзор рынка."""
        s = self.page.locator(Sel.search_input).first
        s.fill("")
        close = self.page.locator(Sel.search_close)
        for _ in range(3):
            if close.count() and close.first.is_visible():
                close.first.click()
                self.page.wait_for_timeout(400)
            else:
                break

    def select_symbol(self, symbol: str) -> None:
        if f"[{symbol}," in self.title:
            return
        self.search_symbol(symbol)
        for _ in range(20):
            if f"[{symbol}," in self.title:
                return
            self.page.wait_for_timeout(250)
        raise TerminalError(f"не удалось переключить график на {symbol}")

    def quote(self, symbol: str) -> tuple[float, float]:
        if not self._market_watch_has(symbol):
            self.search_symbol(symbol, add=True, open_chart=False)
        data = self.page.evaluate(_READ_QUOTE_JS, symbol)
        if not data:
            raise TerminalError(f"нет котировки {symbol} в обзоре рынка")
        ths, tds = data["ths"], data["tds"]
        try:
            bid = parse_number(tds[ths.index("Bid")])
            ask = parse_number(tds[ths.index("Ask")])
        except (ValueError, IndexError):
            bid, ask = parse_number(tds[1]), parse_number(tds[2])
        if bid <= 0 or ask <= 0:
            raise TerminalError(f"пустая котировка {symbol}: {tds}")
        return bid, ask

    def quote_digits(self, symbol: str) -> int:
        data = self.page.evaluate(_READ_QUOTE_JS, symbol)
        if not data:
            return 5
        txt = data["tds"][1] if len(data["tds"]) > 1 else ""
        return len(txt.split(".")[1]) if "." in txt else 0

    def screenshot(self) -> bytes:
        return self.page.screenshot()

    # ---------- действия ----------
    def _type(self, selector: str, value: str) -> None:
        """Ввод «как человек»: fill() не всегда обновляет состояние формы терминала."""
        loc = self.page.locator(selector).first
        loc.click()
        loc.fill("")
        loc.press_sequentially(value, delay=20)
        got = loc.input_value().replace(" ", "")
        if got != value:
            raise TerminalError(f"поле {selector}: ожидалось {value}, введено {got}")

    def _wait_result(self, timeout_s: float = 20) -> str:
        """Ждёт ответ сервера на операцию; возвращает его текст и закрывает панель кнопкой OK."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            text = self.page.evaluate(_RESULT_JS)
            if text is not None:
                self.page.locator(Sel.trade_button, has_text=re.compile(r"^\s*OK\s*$")).first.click()
                return text
            reject = self.page.get_by_role("button", name="Reject", exact=True)
            if reject.count() and reject.first.is_visible():  # реквот — отказываемся
                reject.first.click()
                raise TerminalError("реквот, ордер отменён")
            self.page.wait_for_timeout(250)
        raise TerminalError("нет ответа сервера за отведённое время")

    def _ensure_order_form(self) -> None:
        buy = self.page.locator(Sel.trade_button, has_text=re.compile(r"^\s*Buy( by Market)?\s*$"))
        if buy.count() == 0:
            self.page.locator(Sel.new_order_button).first.click()
            buy.first.wait_for(timeout=8000)
        sel = self.page.locator(Sel.form_type_select)
        if sel.count():
            try:
                sel.first.select_option("0")  # Market Execution
            except Exception:
                pass

    def open_market(self, symbol: str, side: int, volume: float, sl: float, tp: float,
                    comment: str, digits: int) -> dict:
        self.ensure_session()
        self.select_symbol(symbol)
        self._ensure_order_form()
        if f"[{symbol}," not in self.title:
            raise TerminalError("символ графика сменился перед отправкой ордера")
        self._type(Sel.form_volume, f"{volume:.2f}")
        self._type(Sel.form_sl, f"{sl:.{digits}f}" if sl else "")
        self._type(Sel.form_tp, f"{tp:.{digits}f}" if tp else "")
        if comment:
            self._type(Sel.form_comment, comment)
        name = r"^\s*Buy( by Market)?\s*$" if side > 0 else r"^\s*Sell( by Market)?\s*$"
        btn = self.page.locator(Sel.trade_button, has_text=re.compile(name)).first
        if btn.is_disabled():
            raise TerminalError("кнопка ордера неактивна (проверьте объём/SL/TP)")
        btn.click()
        text = self._wait_result()
        res = parse_result(text)
        if not res or "done" not in text.lower():
            raise TerminalError(f"ордер отклонён: {text.strip()[:200]}")
        if res["symbol"] != symbol or res["side"] != side:
            raise TerminalError(f"исполнен НЕ тот ордер: {text.strip()[:200]}")
        return res

    def _open_position_panel(self, ticket: int) -> None:
        row = self.page.locator(f'{Sel.position_rows}[data-id="{ticket}"]')
        if row.count() == 0:
            raise TerminalError(f"позиция #{ticket} не найдена")
        row.first.dblclick()
        self.page.locator(".modify-form").first.wait_for(timeout=8000)

    def modify(self, ticket: int, sl: float | None, tp: float | None, digits: int) -> None:
        self.ensure_session()
        self._open_position_panel(ticket)
        if sl is not None:
            self._type(Sel.modify_sl, f"{sl:.{digits}f}")
        if tp is not None:
            self._type(Sel.modify_tp, f"{tp:.{digits}f}")
        btn = self.page.locator(Sel.trade_button, has_text=re.compile(r"^\s*Modify\s*$")).first
        if btn.is_disabled():
            self._close_side_panel()
            return  # значения не изменились
        btn.click()
        text = self._wait_result()
        if "done" not in text.lower():
            raise TerminalError(f"изменение отклонено: {text.strip()[:200]}")

    def close_position(self, ticket: int) -> dict:
        self.ensure_session()
        self._open_position_panel(ticket)
        btn = self.page.locator("button.trade-button.orange:visible", has_text=f"Close #{ticket}").first
        btn.click()
        text = self._wait_result()
        res = parse_result(text)
        if not res or "done" not in text.lower():
            raise TerminalError(f"закрытие отклонено: {text.strip()[:200]}")
        return res

    def _close_side_panel(self) -> None:
        close = self.page.locator(f'{Sel.form_panel} [title="Close"]')
        if close.count():
            try:
                close.first.click()
            except Exception:
                pass
