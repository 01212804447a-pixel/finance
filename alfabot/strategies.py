"""Реестр стратегий. Выбор — параметр strategy.name в config.yaml (или в окне настроек).

Каждая стратегия получает свечи (open/high/low/close) и возвращает их же с колонками:
signal (1 покупка / -1 продажа / 0), exit_long, exit_short (выход из позиции), atr (для стопа и объёма).

Результаты проверки на 10 валютных парах (2016–2026, спреды Альфа-Форекс) — в README, раздел
«Исследование стратегий». Ни одна стратегия не показала устойчивого преимущества; дневные теряют
заметно меньше часовой из-за меньших издержек.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
import pandas as pd

from . import indicators as ind
from .strategy import StrategyParams, compute as ema_cross_compute


def close_atr(close: pd.Series, n: int = 20) -> pd.Series:
    """ATR по ценам закрытия: дневные high/low в бесплатных данных бывают с выбросами."""
    return close.diff().abs().ewm(alpha=1 / n, adjust=False).mean()


def _finish(out: pd.DataFrame, buy, sell, exit_long, exit_short, warmup: int) -> pd.DataFrame:
    sig = pd.Series(0, index=out.index)
    sig[pd.Series(buy, index=out.index).fillna(False).astype(bool)] = 1
    sig[pd.Series(sell, index=out.index).fillna(False).astype(bool)] = -1
    sig.iloc[:warmup] = 0
    out["signal"] = sig
    out["exit_long"] = pd.Series(exit_long, index=out.index).fillna(False).astype(bool)
    out["exit_short"] = pd.Series(exit_short, index=out.index).fillna(False).astype(bool)
    return out


def rsi2_compute(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """RSI(2) Коннорса на дневках: покупка короткой просадки по направлению долгого тренда."""
    out = df.copy()
    c = out["close"]
    out["atr"] = close_atr(c)
    out["ema_trend"] = ind.ema(c, 200)
    out["rsi2"] = ind.rsi(c, 2)
    out["ema_exit"] = ind.ema(c, 5)
    up = c > out["ema_trend"]
    return _finish(out, up & (out["rsi2"] < 10), ~up & (out["rsi2"] > 90),
                   c > out["ema_exit"], c < out["ema_exit"], 210)


def momentum_compute(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    """Импульс за 12 месяцев: всегда в рынке по направлению годового изменения цены."""
    out = df.copy()
    c = out["close"]
    out["atr"] = close_atr(c)
    out["ret_12m"] = c / c.shift(252) - 1
    state = np.sign(out["ret_12m"])
    return _finish(out, state > 0, state < 0, state < 0, state > 0, 260)


def _describe_ema(row, p) -> str:
    up, above = row["close"] > row["ema_trend"], row["ema_fast"] > row["ema_slow"]
    if up and not above:
        wait = f"тренд вверх → жду пересечения EMA{p.ema_fast} вверх для ПОКУПКИ"
    elif not up and above:
        wait = f"тренд вниз → жду пересечения EMA{p.ema_fast} вниз для ПРОДАЖИ"
    elif up:
        wait = "тренд вверх, пересечение уже было → жду отката и нового пересечения"
    else:
        wait = "тренд вниз, пересечение уже было → жду отскока и нового пересечения"
    if row["adx"] < p.adx_min:
        wait += f"; тренд слабый (ADX {row['adx']:.1f} < {p.adx_min:g})"
    return f"тренд {'↑' if up else '↓'}, ADX {row['adx']:.0f}, RSI {row['rsi']:.0f} — {wait}"


def _describe_rsi2(row, p) -> str:
    up = row["close"] > row["ema_trend"]
    want = "RSI(2) < 10 для ПОКУПКИ" if up else "RSI(2) > 90 для ПРОДАЖИ"
    return f"тренд {'↑' if up else '↓'} (EMA200), RSI(2) = {row['rsi2']:.0f} — жду {want}"


def _describe_momentum(row, p) -> str:
    r = row["ret_12m"]
    return f"за 12 мес {r * 100:+.1f}% → направление {'ПОКУПКА' if r > 0 else 'ПРОДАЖА'}"


@dataclass(frozen=True)
class StrategyInfo:
    name: str
    title: str
    timeframe: str
    compute: Callable[[pd.DataFrame, StrategyParams], pd.DataFrame]
    describe: Callable
    warmup: int
    sl_atr: float | None = None       # None — брать из config.yaml (только для ema_cross)
    tp_atr: float | None = None       # 0 — без тейк-профита
    breakeven_atr: float | None = None  # 0 — без безубытка
    trail_atr: float | None = None    # 0 — без трейлинга
    holds_over_weekend: bool = False


STRATEGIES: dict[str, StrategyInfo] = {
    "rsi2_d1": StrategyInfo(
        "rsi2_d1", "RSI(2) возврат к среднему, дневки", "D1", rsi2_compute, _describe_rsi2, 210,
        sl_atr=2.0, tp_atr=0.0, breakeven_atr=0.0, trail_atr=0.0, holds_over_weekend=True),
    "momentum_12m": StrategyInfo(
        "momentum_12m", "Импульс 12 месяцев, дневки", "D1", momentum_compute, _describe_momentum, 260,
        sl_atr=3.0, tp_atr=0.0, breakeven_atr=0.0, trail_atr=0.0, holds_over_weekend=True),
    "ema_cross": StrategyInfo(
        "ema_cross", "Пересечение EMA 20/50 + тренд, часовки", "H1", ema_cross_compute, _describe_ema, 210),
}
DEFAULT = "rsi2_d1"


def get(name: str | None) -> StrategyInfo:
    key = (name or DEFAULT).strip().lower()
    if key not in STRATEGIES:
        raise ValueError(f"неизвестная стратегия {name!r}; есть: {', '.join(STRATEGIES)}")
    return STRATEGIES[key]


def effective_params(p: StrategyParams) -> StrategyParams:
    """Параметры стопов с учётом пресета выбранной стратегии."""
    info = get(p.name)
    changes = {k: getattr(info, k) for k in ("sl_atr", "tp_atr", "breakeven_atr", "trail_atr")
               if getattr(info, k) is not None}
    return replace(p, **changes)
