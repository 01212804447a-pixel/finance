"""Лаборатория стратегий: честная проверка кандидатов на будущих данных.

Подогнать стратегию под историю легко, поэтому единственная честная проверка — данные,
которых не было в момент выбора. Лаборатория запоминает дату своего запуска и с этого дня
каждый день ведёт виртуальный портфель по каждому кандидату на 10 валютных парах
(по ценам закрытия, со спредами Альфа-Форекс). Раз в неделю в Telegram приходит рейтинг.

Ничего не торгует и с терминалом не связана — только данные Yahoo Finance.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import indicators as ind
from .datafeed import YahooFeed

log = logging.getLogger(__name__)

PAIRS = {"EURUSD": 1.4, "GBPUSD": 2.1, "USDJPY": 2.0, "AUDUSD": 2.0, "USDCAD": 2.5,
         "USDCHF": 2.5, "NZDUSD": 2.5, "EURJPY": 2.5, "GBPJPY": 3.5, "EURGBP": 2.0}  # спред, пункты


def _pip(sym: str) -> float:
    return 0.01 if "JPY" in sym else 0.0001


def _machine(c: pd.Series, enter_long, enter_short, exit_long, exit_short) -> pd.Series:
    st, out = 0, np.zeros(len(c))
    el, es, xl, xs = (np.asarray(x.fillna(False), bool) for x in (enter_long, enter_short, exit_long, exit_short))
    for i in range(len(c)):
        if st == 0:
            st = 1 if el[i] else (-1 if es[i] else 0)
        elif st == 1 and xl[i]:
            st = 0
        elif st == -1 and xs[i]:
            st = 0
        out[i] = st
    return pd.Series(out, index=c.index)


def st_momentum(n):
    return lambda c: np.sign(c / c.shift(n) - 1)


def st_blend(c):
    return sum(np.sign(c / c.shift(n) - 1) for n in (21, 63, 126, 252)) / 4


def st_ema(c):
    return np.sign(ind.ema(c, 50) - ind.ema(c, 200))


def st_donchian(c):
    hh, ll = c.rolling(55).max().shift(1), c.rolling(55).min().shift(1)
    xh, xl = c.rolling(20).max().shift(1), c.rolling(20).min().shift(1)
    return _machine(c, c > hh, c < ll, c < xl, c > xh)


def st_rsi2(c):
    tr, r, e = ind.ema(c, 200), ind.rsi(c, 2), ind.ema(c, 5)
    return _machine(c, (c > tr) & (r < 10), (c < tr) & (r > 90), c > e, c < e)


CANDIDATES = {
    "RSI(2) возврат (стратегия бота)": st_rsi2,
    "Импульс 12 мес": st_momentum(252),
    "Импульс 3 мес": st_momentum(63),
    "Импульс-ансамбль 1–12 мес": st_blend,
    "Разворот 3 мес": lambda c: -np.sign(c / c.shift(63) - 1),
    "Стоимость (против 3-летнего изменения)": lambda c: -np.sign(c / c.shift(780) - 1),
    "Дончиан 55/20": st_donchian,
    "EMA 50/200": st_ema,
}


def daily_returns(closes: dict[str, pd.Series], state_fn, risk: float = 0.01) -> pd.Series:
    """Доход портфеля (равный вес пар) в долях капитала по дням.
    Позиция = направление × риск / (3 ATR); торговля по закрытию; издержки = спред × оборот."""
    parts = []
    for sym, c in closes.items():
        atr = c.diff().abs().ewm(alpha=1 / 20, adjust=False).mean()
        st = pd.Series(state_fn(c), index=c.index).fillna(0).clip(-1, 1)
        units = (st * risk / (3 * atr / c)).shift(1).fillna(0)
        cost = units.diff().abs().fillna(0) * (PAIRS.get(sym, 2.5) * _pip(sym) / c)
        parts.append((units * c.pct_change().fillna(0) - cost).rename(sym))
    return pd.concat(parts, axis=1).fillna(0).mean(axis=1)


def summarize(r: pd.Series) -> dict:
    if len(r) < 2:
        return {"days": len(r), "ret": 0.0, "sharpe": 0.0, "dd": 0.0}
    eq = r.cumsum()
    sd = r.std()
    return {"days": int(len(r)), "ret": float(r.sum() * 100),
            "sharpe": float(r.mean() / sd * np.sqrt(260)) if sd > 0 else 0.0,
            "dd": float((eq.cummax() - eq).max() * 100)}


class StrategyLab:
    def __init__(self, state_file: str = "data/lab_state.json", feed: YahooFeed | None = None,
                 pairs: list[str] | None = None, update_hour_utc: int = 6, report_weekday: int = 5):
        self.path = Path(state_file)
        self.feed = feed or YahooFeed(cache_seconds=3600)
        self.pairs = pairs or list(PAIRS)
        self.update_hour = update_hour_utc
        self.report_weekday = report_weekday   # 5 = суббота
        self.state = {"start": None, "last_update": None, "last_report_week": None, "results": {}}
        self._busy = threading.Lock()
        self._last_attempt = 0.0
        if self.path.exists():
            try:
                self.state.update(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception as e:
                log.warning("лаборатория: не прочитать состояние: %s", e)
        if not self.state["start"]:
            self.state["start"] = date.today().isoformat()
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---------- расчёт ----------
    def update(self) -> None:
        closes = {}
        for sym in self.pairs:
            try:
                closes[sym] = self.feed.bars(sym, "D1")["close"]
            except Exception as e:
                log.warning("лаборатория: нет данных %s: %s", sym, e)
        if len(closes) < 3:
            raise RuntimeError("лаборатория: мало данных (Yahoo недоступен?)")
        start = pd.Timestamp(self.state["start"], tz="UTC")
        split = pd.Timestamp("2022-01-01", tz="UTC")
        results = {}
        for name, fn in CANDIDATES.items():
            r = daily_returns(closes, fn)
            results[name] = {"forward": summarize(r[r.index >= start]),
                             "hist_old": summarize(r[r.index < split]),
                             "hist_new": summarize(r[(r.index >= split) & (r.index < start)])}
        self.state["results"] = results
        self.state["last_update"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        self._save()

    def report_text(self) -> str:
        res = self.state.get("results") or {}
        if not res:
            return "🧪 Лаборатория стратегий ещё считает первый результат — загляните позже (/lab)."
        start = self.state["start"]
        days = next(iter(res.values()))["forward"]["days"]
        ranked = sorted(res.items(), key=lambda kv: kv[1]["forward"]["ret"], reverse=True)
        lines = [f"🧪 Лаборатория стратегий — виртуальные портфели на 10 парах с {start} "
                 f"({days} торговых дней, это данные, которых не было при выборе):"]
        for i, (name, r) in enumerate(ranked, 1):
            f, h = r["forward"], r["hist_new"]
            lines.append(f"{i}. {name}: {f['ret']:+.2f}% (просадка {f['dd']:.1f}%) | "
                         f"история 2022–{start[:4]}: Шарп {h['sharpe']:+.2f}")
        if days < 120:
            lines.append(f"⚠ Пока мало данных: надёжные выводы — не раньше чем через ~6 месяцев "
                         f"(осталось ~{max(0, 126 - days)} торговых дней).")
        else:
            best_name, best = ranked[0]
            f = best["forward"]
            verdict = ("выглядит устойчиво" if f["sharpe"] > 0.8 and best["hist_new"]["sharpe"] > 0
                       else "преимущество пока не доказано")
            lines.append(f"Лидер: {best_name}, Шарп на новых данных {f['sharpe']:+.2f} — {verdict}.")
        lines.append("Риск в лаборатории — 1% на позицию, позиции виртуальные.")
        return "\n".join(lines)

    # ---------- работа внутри бота ----------
    def tick(self, now: datetime, notifier) -> None:
        """Вызывается из главного цикла: раз в день пересчёт, раз в неделю отчёт. Не блокирует."""
        today = now.date().isoformat()
        need_update = (now.hour >= self.update_hour and
                       (self.state.get("last_update") or "")[:10] != today)
        week = f"{now.isocalendar()[0]}-{now.isocalendar()[1]}"
        need_report = (now.weekday() == self.report_weekday and now.hour >= 9
                       and self.state.get("last_report_week") != week)
        if not (need_update or need_report) or self._busy.locked():
            return
        if now.timestamp() - self._last_attempt < 1800:   # при сбое данных — не чаще раза в 30 минут
            return
        self._last_attempt = now.timestamp()

        def work():
            with self._busy:
                try:
                    if need_update or not self.state.get("results"):
                        self.update()
                    if need_report:
                        notifier.send(self.report_text())
                        self.state["last_report_week"] = week
                        self._save()
                except Exception as e:
                    log.warning("лаборатория: %s", e)
        threading.Thread(target=work, name="strategy-lab", daemon=True).start()
