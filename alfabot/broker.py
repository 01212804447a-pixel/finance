"""Тонкая обёртка над официальным пакетом MetaTrader5 (работает только на Windows).

Модуль mt5 передаётся в конструктор, поэтому в тестах его можно подменить.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from .risk import SymbolSpec

log = logging.getLogger(__name__)

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]


@dataclass
class Position:
    ticket: int
    symbol: str
    side: int          # 1 buy, -1 sell
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float


class BrokerError(RuntimeError):
    pass


class MT5Broker:
    def __init__(self, mt5_module, magic: int, deviation: int = 20):
        self.mt5 = mt5_module
        self.magic = magic
        self.deviation = deviation
        self._symbol_cache: dict[str, str] = {}

    # ---------- подключение ----------
    def connect(self, login=None, password=None, server=None, path=None) -> None:
        kwargs = {}
        if path:
            kwargs["path"] = path
        if login:
            kwargs.update(login=int(login), password=password or "", server=server or "")
        if not self.mt5.initialize(**kwargs):
            raise BrokerError(f"MT5 initialize failed: {self.mt5.last_error()}")
        info = self.mt5.account_info()
        if info is None:
            raise BrokerError(f"нет данных счёта: {self.mt5.last_error()}")
        log.info("подключено: счёт %s, сервер %s, валюта %s", info.login, info.server, info.currency)

    def is_alive(self) -> bool:
        try:
            ti = self.mt5.terminal_info()
            return bool(ti and ti.connected and self.mt5.account_info() is not None)
        except Exception:
            return False

    def shutdown(self) -> None:
        try:
            self.mt5.shutdown()
        except Exception:
            pass

    def account(self):
        info = self.mt5.account_info()
        if info is None:
            raise BrokerError(f"account_info: {self.mt5.last_error()}")
        return info

    def is_demo(self) -> bool:
        return self.account().trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO

    # ---------- инструменты ----------
    def resolve_symbol(self, name: str) -> str:
        """У брокера символ может называться с суффиксом (EURUSD.x и т.п.)."""
        if name in self._symbol_cache:
            return self._symbol_cache[name]
        resolved = None
        if self.mt5.symbol_info(name) is not None:
            resolved = name
        else:
            cands = [s.name for s in (self.mt5.symbols_get() or [])
                     if s.name.upper().startswith(name.upper())]
            if cands:
                resolved = sorted(cands, key=len)[0]
        if not resolved:
            raise BrokerError(f"символ {name} не найден у брокера")
        if not self.mt5.symbol_select(resolved, True):
            raise BrokerError(f"не удалось добавить {resolved} в обзор рынка")
        self._symbol_cache[name] = resolved
        return resolved

    def spec(self, symbol: str) -> SymbolSpec:
        si = self.mt5.symbol_info(symbol)
        return SymbolSpec(
            tick_size=si.trade_tick_size or si.point,
            tick_value=si.trade_tick_value,
            volume_min=si.volume_min,
            volume_max=si.volume_max,
            volume_step=si.volume_step,
        )

    def symbol_info(self, symbol: str):
        return self.mt5.symbol_info(symbol)

    def tick(self, symbol: str):
        t = self.mt5.symbol_info_tick(symbol)
        if t is None:
            raise BrokerError(f"нет котировки {symbol}")
        return t

    def spread_points(self, symbol: str) -> float:
        si = self.mt5.symbol_info(symbol)
        t = self.tick(symbol)
        return (t.ask - t.bid) / si.point

    def closed_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        tf = getattr(self.mt5, f"TIMEFRAME_{timeframe}")
        # позиция 0 — текущий (незакрытый) бар, берём на один больше и отбрасываем его
        rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, count + 1)
        if rates is None or len(rates) < 2:
            raise BrokerError(f"нет истории {symbol} {timeframe}: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df.iloc[:-1].set_index("time")[["open", "high", "low", "close"]]

    # ---------- позиции ----------
    def positions(self, symbol: str | None = None) -> list[Position]:
        raw = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        out = []
        for p in raw or []:
            if p.magic != self.magic:
                continue  # чужие и ручные сделки бот не трогает
            out.append(Position(
                ticket=p.ticket, symbol=p.symbol,
                side=1 if p.type == self.mt5.POSITION_TYPE_BUY else -1,
                volume=p.volume, price_open=p.price_open, sl=p.sl, tp=p.tp, profit=p.profit,
            ))
        return out

    def _filling(self, symbol: str) -> int:
        fm = self.mt5.symbol_info(symbol).filling_mode
        if fm & 1:
            return self.mt5.ORDER_FILLING_FOK
        if fm & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict):
        res = self.mt5.order_send(request)
        if res is None:
            raise BrokerError(f"order_send вернул None: {self.mt5.last_error()}")
        if res.retcode != self.mt5.TRADE_RETCODE_DONE:
            raise BrokerError(f"отказ {res.retcode}: {getattr(res, 'comment', '')}")
        return res

    def _round(self, symbol: str, price: float) -> float:
        return round(price, self.mt5.symbol_info(symbol).digits)

    def open(self, symbol: str, side: int, volume: float, sl: float, tp: float, comment: str = "alfabot"):
        t = self.tick(symbol)
        price = t.ask if side > 0 else t.bid
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": self.mt5.ORDER_TYPE_BUY if side > 0 else self.mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": self._round(symbol, sl),
            "tp": self._round(symbol, tp),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(symbol),
        }
        return self._send(req)

    def modify_sl(self, pos: Position, sl: float):
        req = {
            "action": self.mt5.TRADE_ACTION_SLTP,
            "symbol": pos.symbol,
            "position": pos.ticket,
            "sl": self._round(pos.symbol, sl),
            "tp": pos.tp,
            "magic": self.magic,
        }
        return self._send(req)

    def close(self, pos: Position, comment: str = "alfabot close"):
        t = self.tick(pos.symbol)
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "position": pos.ticket,
            "volume": pos.volume,
            "type": self.mt5.ORDER_TYPE_SELL if pos.side > 0 else self.mt5.ORDER_TYPE_BUY,
            "price": t.bid if pos.side > 0 else t.ask,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": comment,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(pos.symbol),
        }
        return self._send(req)

    def min_stop_distance(self, symbol: str) -> float:
        si = self.mt5.symbol_info(symbol)
        return (si.trade_stops_level or 0) * si.point
