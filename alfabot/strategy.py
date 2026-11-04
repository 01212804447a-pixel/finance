"""Трендовая стратегия: пересечение EMA + фильтр тренда EMA200 + ADX + RSI.

Сигнал считается только по ЗАКРЫТЫМ барам, поэтому он не «перерисовывается».
Один и тот же код используется и в живой торговле, и в бэктесте.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import pandas as pd

from . import indicators as ind


class Signal(IntEnum):
    SELL = -1
    NONE = 0
    BUY = 1


@dataclass
class StrategyParams:
    name: str = "rsi2_d1"        # стратегия из alfabot/strategies.py: rsi2_d1 | momentum_12m | ema_cross
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    adx_period: int = 14
    adx_min: float = 20.0
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    atr_period: int = 14
    sl_atr: float = 1.5          # стоп-лосс = 1.5 ATR
    tp_atr: float = 3.0          # тейк-профит = 3 ATR (соотношение 1:2; 0 — без тейка)
    breakeven_atr: float = 1.0   # перенос стопа в безубыток после +1 ATR (0 — выключено)
    trail_atr: float = 1.5       # трейлинг-стоп на расстоянии 1.5 ATR (0 — выключено)
    exit_on_opposite: bool = True

    @property
    def min_bars(self) -> int:
        return max(self.ema_trend, self.ema_slow, self.adx_period * 3, self.atr_period) + 5


def compute(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Добавляет к датафрейму (open/high/low/close) индикаторы и колонку signal."""
    out = df.copy()
    close = out["close"]
    out["ema_fast"] = ind.ema(close, p.ema_fast)
    out["ema_slow"] = ind.ema(close, p.ema_slow)
    out["ema_trend"] = ind.ema(close, p.ema_trend)
    out["adx"] = ind.adx(out, p.adx_period)
    out["rsi"] = ind.rsi(close, p.rsi_period)
    out["atr"] = ind.atr(out, p.atr_period)

    above = out["ema_fast"] > out["ema_slow"]
    prev_above = above.shift(1, fill_value=False)
    cross_up = above & ~prev_above
    cross_down = ~above & prev_above

    strong = out["adx"] >= p.adx_min
    buy = cross_up & (close > out["ema_trend"]) & strong & (out["rsi"] < p.rsi_overbought)
    sell = cross_down & (close < out["ema_trend"]) & strong & (out["rsi"] > p.rsi_oversold)

    sig = pd.Series(Signal.NONE.value, index=out.index)
    sig[buy] = Signal.BUY.value
    sig[sell] = Signal.SELL.value
    # до прогрева индикаторов сигналов нет
    sig.iloc[: p.min_bars] = Signal.NONE.value
    out["signal"] = sig
    out["cross_up"] = cross_up
    out["cross_down"] = cross_down
    out["exit_long"] = cross_down if p.exit_on_opposite else False
    out["exit_short"] = cross_up if p.exit_on_opposite else False
    return out


def last_closed_signal(df: pd.DataFrame, p: StrategyParams) -> tuple[Signal, pd.Series]:
    """df должен содержать только закрытые бары. Возвращает сигнал последнего бара."""
    data = compute(df, p)
    row = data.iloc[-1]
    return Signal(int(row["signal"])), row


def stop_levels(side: Signal, entry: float, atr_value: float, p: StrategyParams) -> tuple[float, float]:
    tp_dist = p.tp_atr * atr_value
    if side == Signal.BUY:
        return entry - p.sl_atr * atr_value, (entry + tp_dist) if p.tp_atr > 0 else 0.0
    if side == Signal.SELL:
        return entry + p.sl_atr * atr_value, (entry - tp_dist) if p.tp_atr > 0 else 0.0
    raise ValueError("нет направления")


def trailing_stop(side: Signal, entry: float, current_sl: float, price: float,
                  atr_value: float, p: StrategyParams) -> float | None:
    """Новый уровень стопа (безубыток/трейлинг) или None, если двигать не нужно.

    Стоп двигается только в сторону прибыли.
    """
    if atr_value <= 0:
        return None
    eps = atr_value * 1e-6  # защита от ошибок округления float
    if side == Signal.BUY:
        profit = price - entry
        candidate = current_sl
        if p.breakeven_atr > 0 and profit + eps >= p.breakeven_atr * atr_value:
            candidate = max(candidate, entry)
        if p.trail_atr > 0 and profit + eps >= p.trail_atr * atr_value:
            candidate = max(candidate, price - p.trail_atr * atr_value)
        return candidate if candidate > current_sl + 1e-12 else None
    if side == Signal.SELL:
        profit = entry - price
        candidate = current_sl
        if p.breakeven_atr > 0 and profit + eps >= p.breakeven_atr * atr_value:
            candidate = min(candidate, entry)
        if p.trail_atr > 0 and profit + eps >= p.trail_atr * atr_value:
            candidate = min(candidate, price + p.trail_atr * atr_value)
        return candidate if candidate < current_sl - 1e-12 else None
    return None
