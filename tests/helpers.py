"""Синтетические данные и поддельный модуль MetaTrader5 для тестов."""
from types import SimpleNamespace

import numpy as np
import pandas as pd


def synthetic_bars(n=3000, seed=1, start=1.10):
    rng = np.random.default_rng(seed)
    # чередующиеся трендовые режимы + шум
    drift = np.repeat(rng.choice([-1, 1], size=n // 250 + 1), 250)[:n] * 0.00012
    ret = drift + rng.normal(0, 0.0009, n)
    close = start * np.exp(np.cumsum(ret))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.0006, n))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_REAL = 2
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    TIMEFRAME_H1 = 16385

    def __init__(self, bars: pd.DataFrame, trade_mode=0, equity=10000.0):
        self.bars = bars
        self.trade_mode = trade_mode
        self.equity = equity
        self.requests = []
        self._positions = []
        self._ticket = 1

    def initialize(self, **kw):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return SimpleNamespace(connected=True)

    def account_info(self):
        return SimpleNamespace(login=123, server="Fake", currency="USD", trade_mode=self.trade_mode,
                               equity=self.equity, balance=self.equity)

    def symbol_info(self, name):
        if not name.startswith("EURUSD"):
            return None
        return SimpleNamespace(name=name, point=0.00001, digits=5, trade_tick_size=0.00001,
                               trade_tick_value=1.0, volume_min=0.01, volume_max=100.0,
                               volume_step=0.01, filling_mode=1, trade_stops_level=0)

    def symbols_get(self):
        return [SimpleNamespace(name="EURUSD")]

    def symbol_select(self, name, enable):
        return True

    def symbol_info_tick(self, name):
        c = float(self.bars["close"].iloc[-1])
        return SimpleNamespace(bid=c, ask=c + 0.0001, time=0)

    def copy_rates_from_pos(self, sym, tf, start, count):
        df = self.bars.tail(count).reset_index()
        df["time"] = df["index" if "index" in df else "time"].astype("int64") // 10**9 \
            if "time" not in df else df["time"].astype("int64") // 10**9
        return df[["time", "open", "high", "low", "close"]].to_records(index=False)

    def positions_get(self, symbol=None):
        return [p for p in self._positions if symbol is None or p.symbol == symbol]

    def order_send(self, req):
        self.requests.append(req)
        if req["action"] == self.TRADE_ACTION_DEAL and "position" not in req:
            self._positions.append(SimpleNamespace(
                ticket=self._ticket, symbol=req["symbol"], magic=req["magic"],
                type=0 if req["type"] == self.ORDER_TYPE_BUY else 1, volume=req["volume"],
                price_open=req["price"], sl=req["sl"], tp=req["tp"], profit=0.0))
            self._ticket += 1
        elif req["action"] == self.TRADE_ACTION_DEAL:
            self._positions = [p for p in self._positions if p.ticket != req["position"]]
        elif req["action"] == self.TRADE_ACTION_SLTP:
            for p in self._positions:
                if p.ticket == req["position"]:
                    p.sl = req["sl"]
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="done")
