"""Воспроизводит исследование стратегий из README (раздел «Исследование стратегий»).

    python research/strategy_study.py

10 валютных пар, дневные цены закрытия Yahoo Finance за 10 лет, спреды Альфа-Форекс,
риск 1% на позицию. Выбор — по 2016–2021, проверка — на 2022–сегодня (не участвовали в выборе).
Для сравнения — 30 прогонов случайных направлений с тем же оборотом.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alfabot.datafeed import YahooFeed  # noqa: E402
from alfabot.lab import CANDIDATES, PAIRS, daily_returns, summarize  # noqa: E402

SPLIT = pd.Timestamp("2022-01-01", tz="UTC")


def random_state(seed):
    def f(c):
        rng = np.random.default_rng(seed)
        flips = rng.random(len(c)) < 1 / 40
        return pd.Series(np.where(np.cumsum(flips) % 2 == 0, 1.0, -1.0), index=c.index)
    return f


def main():
    feed = YahooFeed()
    closes = {s: feed.bars(s, "D1")["close"] for s in PAIRS}
    first = min(c.index[0] for c in closes.values()).date()
    print(f"Данные: {len(closes)} пар, с {first}. Выбор: до 2022, проверка: 2022–сегодня.\n")
    rows = []
    for name, fn in CANDIDATES.items():
        r = daily_returns(closes, fn)
        old, new = summarize(r[r.index < SPLIT]), summarize(r[r.index >= SPLIT])
        yrs_old, yrs_new = old["days"] / 260, new["days"] / 260
        rows.append((name, old["ret"] / yrs_old, old["sharpe"], new["ret"] / yrs_new, new["sharpe"], new["dd"]))
    rows.sort(key=lambda x: x[2], reverse=True)
    print(f"{'Стратегия':42s} {'2016–21 %/год':>13s} {'Шарп':>6s} | {'2022–26 %/год':>13s} {'Шарп':>6s} {'Просадка':>8s}")
    for name, a1, s1, a2, s2, dd in rows:
        print(f"{name:42s} {a1:+13.2f} {s1:+6.2f} | {a2:+13.2f} {s2:+6.2f} {dd:7.1f}%")
    rs = [summarize(daily_returns(closes, random_state(k))[lambda x: x.index >= SPLIT])["sharpe"] for k in range(30)]
    print(f"\nСлучайные направления, 2022–26: Шарп в среднем {np.mean(rs):+.2f}, "
          f"90% прогонов в диапазоне {np.quantile(rs, .05):+.2f}…{np.quantile(rs, .95):+.2f}")
    print(f"Лучшая по 2016–21: «{rows[0][0]}» → на новых данных Шарп {rows[0][4]:+.2f}")


if __name__ == "__main__":
    main()
