import math

import pandas as pd
import pytest

from alfabot import indicators as ind
from alfabot.backtest import run_backtest
from alfabot.risk import RiskGuard, RiskParams, SymbolSpec, position_size
from alfabot.strategy import Signal, StrategyParams, compute, stop_levels, trailing_stop
from tests.helpers import synthetic_bars

EURUSD = SymbolSpec(tick_size=0.00001, tick_value=1.0, volume_min=0.01, volume_max=100, volume_step=0.01)


def test_indicators_ranges():
    df = synthetic_bars(1000)
    r = ind.rsi(df["close"]).dropna()
    a = ind.adx(df).iloc[50:]
    assert ((r >= 0) & (r <= 100)).all()
    assert ((a >= 0) & (a <= 100)).all()
    assert (ind.atr(df) > 0).all()


def test_rsi_all_gains_is_100():
    s = pd.Series(range(1, 50), dtype=float)
    assert ind.rsi(s).iloc[-1] == 100.0


def test_signals_only_after_warmup_and_both_sides():
    p = StrategyParams()
    data = compute(synthetic_bars(4000), p)
    assert (data["signal"].iloc[: p.min_bars] == 0).all()
    assert (data["signal"] == 1).any() and (data["signal"] == -1).any()
    buys = data[data["signal"] == 1]
    assert (buys["close"] > buys["ema_trend"]).all()
    assert (buys["ema_fast"] > buys["ema_slow"]).all()


def test_signal_not_repainting():
    """Сигнал на баре i не меняется от добавления будущих баров."""
    p = StrategyParams()
    df = synthetic_bars(2000)
    full = compute(df, p)["signal"]
    part = compute(df.iloc[:1500], p)["signal"]
    assert (full.iloc[:1500].values == part.values).all()


def test_position_size_risk():
    # 10000$ * 1% = 100$, стоп 50 пунктов (0.0050) => 1 лот теряет 500$ => 0.2 лота
    assert position_size(10000, 1.0, 0.0050, EURUSD, 10) == pytest.approx(0.2)
    assert position_size(10000, 1.0, 0.0050, EURUSD, 0.1) == pytest.approx(0.1)  # потолок
    assert position_size(50, 1.0, 0.0050, EURUSD, 10) == 0.0  # меньше мин. лота -> не торгуем
    assert position_size(10000, 1.0, 0.0, EURUSD, 10) == 0.0


def test_stop_levels_and_trailing():
    p = StrategyParams()
    sl, tp = stop_levels(Signal.BUY, 1.1000, 0.0010, p)
    assert sl == pytest.approx(1.0985) and tp == pytest.approx(1.1030)
    assert trailing_stop(Signal.BUY, 1.1000, sl, 1.1005, 0.0010, p) is None
    assert trailing_stop(Signal.BUY, 1.1000, sl, 1.1010, 0.0010, p) == pytest.approx(1.1000)
    assert trailing_stop(Signal.BUY, 1.1000, 1.1000, 1.1020, 0.0010, p) == pytest.approx(1.1005)
    sl, tp = stop_levels(Signal.SELL, 1.1000, 0.0010, p)
    assert trailing_stop(Signal.SELL, 1.1000, sl, 1.0990, 0.0010, p) == pytest.approx(1.1000)
    # стоп не отодвигается назад
    assert trailing_stop(Signal.SELL, 1.1000, 1.0990, 1.0995, 0.0010, p) is None


def test_risk_guard_daily_and_total():
    from datetime import datetime, timezone
    g = RiskGuard(RiskParams(max_daily_loss_pct=3, max_total_drawdown_pct=10))
    d1 = datetime(2026, 1, 5, 10, tzinfo=timezone.utc)
    g.update(d1, 10000)
    assert g.check(9800) is None and g.can_open
    assert g.check(9690) is not None and not g.can_open
    assert g.check(9600) is None  # сообщает один раз
    g.update(d1.replace(day=6), 9600)
    assert g.can_open
    assert g.check(8990) is not None and g.halted_forever and not g.can_open
    g2 = RiskGuard(RiskParams()); g2.load_dict(g.to_dict())
    assert g2.halted_forever


def test_backtest_runs_and_consistent():
    res = run_backtest(synthetic_bars(5000), StrategyParams(), EURUSD, spread=0.0001)
    st = res.stats()
    assert st["сделок"] > 5
    assert math.isclose(res.final_equity, 10000 + sum(t.pnl for t in res.trades), rel_tol=1e-9)
    # убыток по стопу не превышает ~риск 1% (+спред/гэп)
    worst = min(t.pnl for t in res.trades)
    assert worst > -10000 * 0.02
