import time
from datetime import datetime, timezone

import pandas as pd

from alfabot.lab import CANDIDATES, StrategyLab, daily_returns, summarize
from tests.helpers import synthetic_bars


class FakeFeed:
    def bars(self, sym, tf):
        df = synthetic_bars(2600, seed=hash(sym) % 100, freq="D")
        df.index = pd.date_range("2016-11-01", periods=len(df), freq="D", tz="UTC")
        return df


def test_lab_update_and_report(tmp_path):
    lab = StrategyLab(str(tmp_path / "lab.json"), feed=FakeFeed(), pairs=["EURUSD", "GBPUSD", "USDJPY"])
    lab.state["start"] = "2023-06-01"
    lab.update()
    res = lab.state["results"]
    assert set(res) == set(CANDIDATES)
    assert all(r["forward"]["days"] > 120 for r in res.values())
    txt = lab.report_text()
    assert "Лаборатория" in txt and "Лидер" in txt
    assert StrategyLab(str(tmp_path / "lab.json")).state["start"] == "2023-06-01"   # дата старта сохраняется


def test_costs_make_random_turnover_lose():
    c = synthetic_bars(1000, freq="D")["close"]
    flip = lambda s: pd.Series([1 if (i // 3) % 2 else -1 for i in range(len(s))], index=s.index)
    r = daily_returns({"EURUSD": c}, flip)
    assert summarize(r)["days"] == 1000


def test_tick_runs_in_background_and_reports_weekly(tmp_path):
    lab = StrategyLab(str(tmp_path / "lab.json"), feed=FakeFeed(), pairs=["EURUSD", "GBPUSD", "USDJPY"])
    sent = []

    class N:
        def send(self, t): sent.append(t)
    saturday = datetime(2026, 11, 7, 10, tzinfo=timezone.utc)
    t0 = time.time()
    lab.tick(saturday, N())
    assert time.time() - t0 < 0.5                      # не блокирует торговый цикл
    for _ in range(100):
        if sent:
            break
        time.sleep(0.05)
    assert sent and "Лаборатория" in sent[0]
    lab.tick(saturday.replace(hour=12), N())           # повторно на этой неделе не шлёт
    time.sleep(0.2)
    assert len(sent) == 1
