"""Брокер для TradingBot поверх браузерного веб-терминала.

Интерфейс совпадает с alfabot.broker.MT5Broker, поэтому главный цикл бота и риск-менеджмент
работают без изменений.
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pandas as pd

from ..broker import BrokerError, Position
from ..datafeed import DataFeed
from ..risk import SymbolSpec
from .terminal import TerminalError, WebTerminal

log = logging.getLogger(__name__)


class WebBroker:
    label = None

    def __init__(self, terminal: WebTerminal, feed: DataFeed, *, comment_tag: str = "alfabot",
                 timeframe: str = "H1", contract_size: float = 100000.0,
                 contract_sizes: dict | None = None, volume_min: float = 0.01,
                 volume_step: float = 0.01, volume_max: float = 100.0):
        self.t = terminal
        self.feed = feed
        self.tag = comment_tag
        self.timeframe = timeframe
        self.contract_size = contract_size
        self.contract_sizes = {k.upper(): v for k, v in (contract_sizes or {}).items()}
        self.volume_min, self.volume_step, self.volume_max = volume_min, volume_step, volume_max
        self._digits: dict[str, int] = {}
        self._currency = None
        self._rates: dict[str, tuple[float, float]] = {}

    # ---------- подключение ----------
    def connect(self, *_args, **_kw) -> None:
        try:
            if self.t.page is None:
                self.t.start()
            else:
                self.t.open()
        except TerminalError as e:
            raise BrokerError(str(e)) from e

    def is_alive(self) -> bool:
        try:
            self.t.ensure_session(wait_s=30)
            return True
        except Exception as e:
            log.warning("терминал недоступен: %s", e)
            return False

    def shutdown(self) -> None:
        self.t.stop()

    def _wrap(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except TerminalError as e:
            raise BrokerError(str(e)) from e

    # ---------- счёт ----------
    def account(self):
        info = self._wrap(self.t.account)
        self._currency = info.get("currency") or self._currency
        return SimpleNamespace(login=info.get("login", 0), server=info.get("server", ""),
                               currency=info.get("currency", ""), balance=info.get("balance", 0.0),
                               equity=info["equity"], leverage=0)

    def is_demo(self) -> bool:
        return self.t.is_demo()

    # ---------- инструменты ----------
    def resolve_symbol(self, name: str) -> str:
        real = self._wrap(self.t.search_symbol, name, add=True, open_chart=False)
        self._digits[real] = self.t.quote_digits(real)
        return real

    def digits(self, symbol: str) -> int:
        if symbol not in self._digits:
            self._digits[symbol] = self.t.quote_digits(symbol)
        return self._digits[symbol]

    def symbol_info(self, symbol: str):
        d = self.digits(symbol)
        return SimpleNamespace(point=10 ** -d, digits=d)

    def tick(self, symbol: str):
        bid, ask = self._wrap(self.t.quote, symbol)
        try:
            self.feed.store.add_tick(symbol, self.timeframe, bid)
        except Exception as e:
            log.debug("bar store: %s", e)
        return SimpleNamespace(bid=bid, ask=ask)

    def spread_points(self, symbol: str) -> float:
        t = self.tick(symbol)
        return (t.ask - t.bid) / self.symbol_info(symbol).point

    def closed_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        try:
            return self.feed.closed_bars(symbol, timeframe, count)
        except Exception as e:
            raise BrokerError(str(e)) from e

    def _quote_to_account_rate(self, quote_ccy: str) -> float:
        acc = (self._currency or self.account().currency).upper()
        if quote_ccy == acc:
            return 1.0
        cached = self._rates.get(quote_ccy)
        if cached and time.time() - cached[0] < 600:
            return cached[1]
        rate = self._find_rate(quote_ccy, acc)
        self._rates[quote_ccy] = (time.time(), rate)
        return rate

    def _find_rate(self, quote_ccy: str, acc: str) -> float:
        for sym, invert in ((f"{quote_ccy}{acc}", False), (f"{acc}{quote_ccy}", True)):
            try:
                real = self.t.search_symbol(sym, add=True, open_chart=False)
                bid, ask = self.t.quote(real)
                mid = (bid + ask) / 2
                return 1.0 / mid if invert else mid
            except TerminalError:
                continue
        raise BrokerError(f"не найден курс {quote_ccy}->{acc} для расчёта объёма")

    def spec(self, symbol: str) -> SymbolSpec:
        base = symbol.upper()[:6]
        contract = self.contract_sizes.get(base, self.contract_size)
        quote_ccy = base[3:6]
        rate = self._quote_to_account_rate(quote_ccy)
        point = self.symbol_info(symbol).point
        return SymbolSpec(tick_size=point, tick_value=contract * point * rate,
                          volume_min=self.volume_min, volume_max=self.volume_max,
                          volume_step=self.volume_step)

    def min_stop_distance(self, symbol: str) -> float:
        return 0.0

    # ---------- позиции ----------
    def positions(self, symbol: str | None = None) -> list[Position]:
        out = []
        for p in self._wrap(self.t.positions):
            if not p.comment.startswith(self.tag):
                continue  # ручные сделки бот не трогает
            if symbol and p.symbol != symbol:
                continue
            out.append(Position(ticket=p.ticket, symbol=p.symbol, side=p.side, volume=p.volume,
                                price_open=p.price_open, sl=p.sl, tp=p.tp, profit=p.profit))
        return out

    def open(self, symbol, side, volume, sl, tp, comment=None):
        return self._wrap(self.t.open_market, symbol, int(side), float(volume), sl, tp,
                          self.tag, self.digits(symbol))

    def modify_sl(self, pos: Position, sl: float):
        return self._wrap(self.t.modify, pos.ticket, sl, None, self.digits(pos.symbol))

    def close(self, pos: Position, comment: str = ""):
        return self._wrap(self.t.close_position, pos.ticket)

    def screenshot(self) -> bytes | None:
        try:
            return self.t.screenshot()
        except Exception:
            return None
