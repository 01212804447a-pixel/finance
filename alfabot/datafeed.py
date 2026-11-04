"""Источник свечей для стратегии в браузерном режиме.

1) Yahoo Finance: бесплатная история, ключ не нужен. Для мажорных валютных пар часовые свечи
   практически совпадают с брокерскими.
2) Свои свечи из котировок терминала (BarStore). Копятся всё время работы бота и используются,
   если Yahoo недоступен.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
_YAHOO_INTERVAL = {"M1": ("1m", "7d"), "M5": ("5m", "60d"), "M15": ("15m", "60d"),
                   "M30": ("30m", "60d"), "H1": ("60m", "730d"), "H4": ("60m", "730d"),
                   "D1": ("1d", "10y")}
YAHOO_SPECIAL = {"XAUUSD": "GC=F", "XAGUSD": "SI=F"}


class DataError(RuntimeError):
    pass


def yahoo_ticker(symbol: str) -> str:
    base = symbol.upper()[:6]
    return YAHOO_SPECIAL.get(base, f"{base}=X")


def parse_yahoo(js: dict) -> pd.DataFrame:
    res = (js.get("chart") or {}).get("result") or []
    if not res:
        raise DataError(f"Yahoo: пустой ответ {str(js)[:200]}")
    r = res[0]
    ts = r.get("timestamp") or []
    q = r["indicators"]["quote"][0]
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"]},
                      index=pd.to_datetime(ts, unit="s", utc=True))
    df = df.dropna()
    df = df[(df["high"] >= df["low"]) & (df["close"] > 0)]
    return df[~df.index.duplicated(keep="last")].sort_index()


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    rule = f"{minutes}min"
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"})
    return out.dropna()


def drop_unclosed(df: pd.DataFrame, minutes: int, now: datetime | None = None) -> pd.DataFrame:
    now = now or datetime.now(timezone.utc)
    end = df.index + pd.Timedelta(minutes=minutes)
    return df[end <= pd.Timestamp(now)]


class YahooFeed:
    URL = "https://query1.finance.yahoo.com/v8/finance/chart/{t}"

    def __init__(self, cache_seconds: int = 60):
        self.cache_seconds = cache_seconds
        self._cache: dict[tuple, tuple[float, pd.DataFrame]] = {}

    def bars(self, symbol: str, timeframe: str) -> pd.DataFrame:
        key = (symbol, timeframe)
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_seconds:
            return hit[1]
        if requests is None:
            raise DataError("нет пакета requests")
        interval, rng = _YAHOO_INTERVAL[timeframe]
        r = requests.get(self.URL.format(t=yahoo_ticker(symbol)),
                         params={"interval": interval, "range": rng},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        if r.status_code != 200:
            raise DataError(f"Yahoo HTTP {r.status_code}")
        df = parse_yahoo(r.json())
        minutes = TF_MINUTES[timeframe]
        if timeframe == "H4":
            df = resample(df, 240)
        df = drop_unclosed(df, minutes)
        self._cache[key] = (time.time(), df)
        return df


class BarStore:
    """Собирает свечи из котировок (bid) и хранит их в CSV."""

    def __init__(self, directory: str = "bars"):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cur: dict[tuple, dict] = {}
        self._data: dict[tuple, pd.DataFrame] = {}

    def _path(self, symbol, tf):
        return self.dir / f"{symbol}_{tf}.csv"

    def load(self, symbol: str, tf: str) -> pd.DataFrame:
        key = (symbol, tf)
        if key not in self._data:
            p = self._path(symbol, tf)
            if p.exists():
                df = pd.read_csv(p, index_col=0, parse_dates=True)
                df.index = pd.to_datetime(df.index, utc=True)
            else:
                df = pd.DataFrame(columns=["open", "high", "low", "close"], dtype=float)
            self._data[key] = df
        return self._data[key]

    def add_tick(self, symbol: str, tf: str, price: float, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        minutes = TF_MINUTES[tf]
        start = pd.Timestamp(now).floor(f"{minutes}min")
        key = (symbol, tf)
        cur = self._cur.get(key)
        if cur and cur["time"] != start:
            df = self.load(symbol, tf)
            row = pd.DataFrame([{k: cur[k] for k in ("open", "high", "low", "close")}], index=[cur["time"]])
            df = pd.concat([df, row]) if len(df) else row
            df = df[~df.index.duplicated(keep="last")].tail(5000)
            self._data[key] = df
            df.to_csv(self._path(symbol, tf))
            cur = None
        if cur is None:
            self._cur[key] = {"time": start, "open": price, "high": price, "low": price, "close": price}
        else:
            cur["high"] = max(cur["high"], price)
            cur["low"] = min(cur["low"], price)
            cur["close"] = price


class DataFeed:
    def __init__(self, source: str = "yahoo", store: BarStore | None = None):
        self.source = source
        self.yahoo = YahooFeed()
        self.store = store or BarStore()

    def closed_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        if self.source == "yahoo":
            try:
                df = self.yahoo.bars(symbol, timeframe)
                if len(df) >= count // 2:
                    return df.tail(count)
            except Exception as e:
                log.warning("Yahoo недоступен для %s: %s — пробую свои свечи", symbol, e)
        df = self.store.load(symbol, timeframe)
        if len(df) < 60:
            raise DataError(f"мало истории {symbol} {timeframe}: {len(df)} свечей")
        return df.tail(count)
