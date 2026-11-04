"""Простой побарный бэктест той же стратегии, что торгует вживую.

Консервативные допущения: вход по открытию следующего бара, спред учитывается,
если внутри бара задеты и стоп, и тейк — считаем, что сработал стоп.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .risk import SymbolSpec, position_size
from .strategy import Signal, StrategyParams, compute, stop_levels, trailing_stop


@dataclass
class Trade:
    side: int
    entry_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    lots: float
    exit_time: pd.Timestamp | None = None
    exit: float | None = None
    pnl: float = 0.0
    reason: str = ""


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series | None = None
    start_equity: float = 0.0

    @property
    def final_equity(self) -> float:
        return float(self.equity_curve.iloc[-1]) if self.equity_curve is not None and len(self.equity_curve) else self.start_equity

    def stats(self) -> dict:
        pnl = [t.pnl for t in self.trades]
        wins = [x for x in pnl if x > 0]
        losses = [x for x in pnl if x <= 0]
        eq = self.equity_curve if self.equity_curve is not None else pd.Series([self.start_equity])
        dd = ((eq.cummax() - eq) / eq.cummax() * 100).max() if len(eq) else 0.0
        return {
            "сделок": len(pnl),
            "прибыльных_%": round(len(wins) / len(pnl) * 100, 1) if pnl else 0.0,
            "профит_фактор": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else float("inf") if wins else 0.0,
            "итог": round(self.final_equity - self.start_equity, 2),
            "доходность_%": round((self.final_equity / self.start_equity - 1) * 100, 2) if self.start_equity else 0.0,
            "макс_просадка_%": round(float(dd), 2),
        }


def run_backtest(df: pd.DataFrame, p: StrategyParams, spec: SymbolSpec, *,
                 start_equity: float = 10_000.0, risk_pct: float = 1.0,
                 spread: float = 0.0, max_lot: float = 100.0, compute_fn=None) -> BacktestResult:
    data = (compute_fn or compute)(df, p)
    equity = start_equity
    curve = []
    trades: list[Trade] = []
    pos: Trade | None = None
    pending: Signal = Signal.NONE
    pending_exit = False
    value_per_price = spec.tick_value / spec.tick_size  # деньги на 1 лот за 1.0 цены

    def close(t: Trade, price: float, when, reason: str):
        nonlocal equity
        t.exit, t.exit_time, t.reason = price, when, reason
        t.pnl = (price - t.entry) * t.side * t.lots * value_per_price
        equity += t.pnl
        trades.append(t)

    idx = data.index
    for i in range(len(data)):
        bar = data.iloc[i]
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

        # исполнение решений, принятых на закрытии прошлого бара
        if pos is not None and pending_exit:
            close(pos, o if pos.side > 0 else o + spread, idx[i], "обратный сигнал")
            pos = None
        pending_exit = False
        if pos is None and pending != Signal.NONE:
            atr_prev = data["atr"].iloc[i - 1]
            entry = o + spread if pending == Signal.BUY else o
            sl, tp = stop_levels(pending, entry, atr_prev, p)
            lots = position_size(equity, risk_pct, abs(entry - sl), spec, max_lot)
            if lots > 0:
                pos = Trade(int(pending), idx[i], entry, sl, tp, lots)
        pending = Signal.NONE

        # стоп / тейк внутри бара (цены bid; для sell учитываем спред на ask)
        if pos is not None:
            if pos.side > 0:
                if l <= pos.sl:
                    close(pos, min(pos.sl, o), idx[i], "стоп"); pos = None
                elif pos.tp and h >= pos.tp:
                    close(pos, pos.tp, idx[i], "тейк"); pos = None
            else:
                if h + spread >= pos.sl:
                    close(pos, max(pos.sl, o + spread), idx[i], "стоп"); pos = None
                elif pos.tp and l + spread <= pos.tp:
                    close(pos, pos.tp, idx[i], "тейк"); pos = None

        # решения на закрытии бара
        if pos is not None:
            side = Signal(pos.side)
            price = c if pos.side > 0 else c + spread
            new_sl = trailing_stop(side, pos.entry, pos.sl, price, bar["atr"], p)
            if new_sl is not None:
                pos.sl = new_sl
            if (pos.side > 0 and bar["exit_long"]) or (pos.side < 0 and bar["exit_short"]):
                pending_exit = True
        sig = Signal(int(bar["signal"]))
        if sig != Signal.NONE and (pos is None or pending_exit):
            pending = sig

        open_pnl = 0.0
        if pos is not None:
            mark = c if pos.side > 0 else c + spread
            open_pnl = (mark - pos.entry) * pos.side * pos.lots * value_per_price
        curve.append(equity + open_pnl)

    if pos is not None:
        last = data.iloc[-1]["close"]
        close(pos, last if pos.side > 0 else last + spread, idx[-1], "конец данных")
        curve[-1] = equity
    return BacktestResult(trades, pd.Series(curve, index=idx), start_equity)


def load_csv(path: str | Path) -> pd.DataFrame:
    """CSV из MT5 (экспорт истории: <DATE> <TIME> <OPEN> ...) или обычный time,open,high,low,close."""
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip("<>").lower() for c in df.columns]
    if "date" in df.columns and "time" in df.columns:
        df["time"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
    elif "date" in df.columns:
        df["time"] = pd.to_datetime(df["date"])
    else:
        df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time")[["open", "high", "low", "close"]].astype(float).sort_index()
