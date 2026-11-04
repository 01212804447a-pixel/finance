"""Бумажная торговля: реальные котировки из терминала, а сделки виртуальные.

Нужна для проверки бота на реальном счёте Альфа-Форекс без риска: в браузере у Альфы
доступен только реальный сервер.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace

from .broker import BrokerError, Position

log = logging.getLogger(__name__)


@dataclass
class PaperPos:
    ticket: int
    symbol: str
    side: int
    volume: float
    price_open: float
    sl: float
    tp: float
    value_per_price: float   # деньги на 1.0 изменения цены для этого объёма


class PaperBroker:
    """Котировки, свечи и параметры инструментов берутся у market-брокера, ордера только в памяти."""

    label = "БУМАЖНЫЙ"

    def __init__(self, market, state_file: str = "paper_state.json", start_balance: float | None = None):
        self.m = market
        self.state_file = Path(state_file)
        self.start_balance = start_balance
        self.balance = 0.0
        self.currency = ""
        self.pos: list[PaperPos] = []
        self.closed: list[dict] = []
        self._next = 1
        self.login = 0
        self._load()

    # ---------- состояние ----------
    def _load(self):
        if self.state_file.exists():
            d = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.balance = d["balance"]
            self.currency = d.get("currency", "")
            self.pos = [PaperPos(**p) for p in d.get("positions", [])]
            self.closed = d.get("closed", [])
            self._next = d.get("next", 1)

    def _save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps({
            "balance": self.balance, "currency": self.currency, "next": self._next,
            "positions": [asdict(p) for p in self.pos], "closed": self.closed[-500:],
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- делегирование рыночных данных ----------
    def connect(self, *a, **kw):
        self.m.connect(*a, **kw)
        real = self.m.account()
        self.login = real.login
        if not self.state_file.exists():
            self.balance = self.start_balance if self.start_balance else real.equity
            self.currency = real.currency
            self._save()

    def is_alive(self):
        return self.m.is_alive()

    def shutdown(self):
        self.m.shutdown()

    def is_demo(self):
        return True  # реальных ордеров нет

    def resolve_symbol(self, name):
        return self.m.resolve_symbol(name)

    def symbol_info(self, s):
        return self.m.symbol_info(s)

    def tick(self, s):
        return self.m.tick(s)

    def spread_points(self, s):
        return self.m.spread_points(s)

    def closed_bars(self, s, tf, n):
        return self.m.closed_bars(s, tf, n)

    def spec(self, s):
        return self.m.spec(s)

    def min_stop_distance(self, s):
        return self.m.min_stop_distance(s)

    def screenshot(self):
        return getattr(self.m, "screenshot", lambda: None)()

    # ---------- симуляция ----------
    def _mark(self, p: PaperPos):
        t = self.m.tick(p.symbol)
        return t.bid if p.side > 0 else t.ask

    def _pnl(self, p: PaperPos, price: float) -> float:
        return (price - p.price_open) * p.side * p.value_per_price

    def _realize(self, p: PaperPos, price: float, reason: str):
        pnl = self._pnl(p, price)
        self.balance += pnl
        self.pos = [x for x in self.pos if x.ticket != p.ticket]
        self.closed.append({"ticket": p.ticket, "symbol": p.symbol, "side": p.side,
                            "open": p.price_open, "close": price, "pnl": round(pnl, 2), "reason": reason})
        log.info("PAPER закрыта #%s %s %s по %.5f (%s), P/L %.2f", p.ticket, p.symbol,
                 "BUY" if p.side > 0 else "SELL", price, reason, pnl)
        self._save()

    def _check_stops(self):
        for p in list(self.pos):
            price = self._mark(p)
            if p.sl and ((p.side > 0 and price <= p.sl) or (p.side < 0 and price >= p.sl)):
                self._realize(p, p.sl, "стоп")
            elif p.tp and ((p.side > 0 and price >= p.tp) or (p.side < 0 and price <= p.tp)):
                self._realize(p, p.tp, "тейк")

    def account(self):
        self._check_stops()
        floating = sum(self._pnl(p, self._mark(p)) for p in self.pos)
        return SimpleNamespace(login=self.login, server="paper", currency=self.currency,
                               balance=self.balance, equity=self.balance + floating, leverage=0)

    def positions(self, symbol=None):
        self._check_stops()
        out = []
        for p in self.pos:
            if symbol and p.symbol != symbol:
                continue
            out.append(Position(ticket=p.ticket, symbol=p.symbol, side=p.side, volume=p.volume,
                                price_open=p.price_open, sl=p.sl, tp=p.tp,
                                profit=self._pnl(p, self._mark(p))))
        return out

    def open(self, symbol, side, volume, sl, tp, comment=None):
        t = self.m.tick(symbol)
        price = t.ask if side > 0 else t.bid
        spec = self.m.spec(symbol)
        vpp = volume * spec.tick_value / spec.tick_size
        digits = getattr(self.m.symbol_info(symbol), "digits", None) if hasattr(self.m, "symbol_info") else None
        if digits is not None:
            sl, tp = round(sl, digits), round(tp, digits)
        p = PaperPos(self._next, symbol, int(side), float(volume), price, sl, tp, vpp)
        self._next += 1
        self.pos.append(p)
        self._save()
        log.info("PAPER открыта #%s %s %s %.2f по %.5f", p.ticket, symbol, "BUY" if side > 0 else "SELL", volume, price)
        return {"ticket": p.ticket, "price": price}

    def modify_sl(self, pos: Position, sl: float):
        for p in self.pos:
            if p.ticket == pos.ticket:
                p.sl = sl
                self._save()
                return
        raise BrokerError(f"нет позиции #{pos.ticket}")

    def close(self, pos: Position, comment: str = ""):
        for p in self.pos:
            if p.ticket == pos.ticket:
                self._realize(p, self._mark(p), "закрыта ботом")
                return
        raise BrokerError(f"нет позиции #{pos.ticket}")

    def stats(self) -> str:
        n = len(self.closed)
        wins = sum(1 for c in self.closed if c["pnl"] > 0)
        total = sum(c["pnl"] for c in self.closed)
        return (f"Бумажная торговля: {n} закрытых сделок, прибыльных {wins}"
                f"{f' ({wins / n * 100:.0f}%)' if n else ''}, итог {total:+.2f} {self.currency}")
