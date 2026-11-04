from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from alfabot.broker import Position
from alfabot.datafeed import BarStore, drop_unclosed, parse_yahoo, resample, yahoo_ticker
from alfabot.paper import PaperBroker
from alfabot.risk import SymbolSpec
from alfabot.web.terminal import parse_number, parse_result, parse_summary, rows_to_positions, with_lang

# заголовки и строка — в точности как в веб-терминале MT5 (снято с демо-счёта)
HEADERS = ["Symbol", "Ticket", "Time", "Type", "Volume", "Open Price", "Stop Loss", "Take Profit",
           "Close Price", "Swap", "Profit", "Comment"]
ROW = {"id": "11237443910", "cells": ["EURUSD", "11237443910", "11.04 16:01:21", "buy", "0.01", "1.12599",
                                      "1.12098", "1.13598", "1.12602", "", "0.03", "alfabot-test", None]}


def test_parse_number():
    assert parse_number("100 000.00") == 100000.0
    assert parse_number("99 999.83") == 99999.83
    assert parse_number("-0.33") == -0.33
    assert parse_number("1,5") == 1.5
    assert parse_number("") == 0.0 and parse_number(None) == 0.0


def test_parse_summary():
    s = parse_summary("Balance: 100 000.00 | Equity: 99 999.94 | Margin: 11.26 | "
                      "Free margin: 99 988.68 | Level: 888 098.93%")
    assert s == {"balance": 100000.0, "equity": 99999.94, "margin": 11.26, "margin_free": 99988.68}


def test_parse_result():
    r = parse_result("#11237443910 buy 0.01 EURUSD at 1.12599\n\nDone\nOK")
    assert r == {"ticket": 11237443910, "side": 1, "volume": 0.01, "symbol": "EURUSD", "price": 1.12599}
    assert parse_result("#5 sell 0.05 USDJPY at 158.41 Done")["side"] == -1
    assert parse_result("Market closed") is None


def test_rows_to_positions():
    pos = rows_to_positions(HEADERS, [ROW, {"id": "9", "cells": ["EURUSD", "9", "", "buy limit"]}])
    assert len(pos) == 1
    p = pos[0]
    assert (p.ticket, p.symbol, p.side, p.volume, p.sl, p.tp, p.profit, p.comment) == \
        (11237443910, "EURUSD", 1, 0.01, 1.12098, 1.13598, 0.03, "alfabot-test")


def test_with_lang():
    assert with_lang("https://trade.alfaforex.ru/terminal") == "https://trade.alfaforex.ru/terminal?lang=en"
    assert "lang=ru" in with_lang("https://x/terminal?lang=ru")


def test_yahoo_parsing_and_bars():
    ts = [1_700_000_000 + 3600 * i for i in range(5)]
    js = {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{
        "open": [1, 2, None, 4, 5], "high": [1.5, 2.5, 3, 4.5, 5.5],
        "low": [0.5, 1.5, 2, 3.5, 4.5], "close": [1.2, 2.2, 3, 4.2, 5.2]}]}}]}}
    df = parse_yahoo(js)
    assert len(df) == 4 and df.index.tz is not None
    last_start = df.index[-1]
    assert len(drop_unclosed(df, 60, now=(last_start + pd.Timedelta(minutes=30)).to_pydatetime())) == 3
    assert yahoo_ticker("EURUSD.x") == "EURUSD=X" and yahoo_ticker("XAUUSD") == "GC=F"
    h4 = resample(df, 240)
    assert h4["high"].max() == df["high"].max()


def test_bar_store(tmp_path):
    st = BarStore(str(tmp_path))
    t0 = datetime(2026, 1, 5, 10, 5, tzinfo=timezone.utc)
    for i, px in enumerate([1.0, 1.2, 0.9, 1.1]):
        st.add_tick("EURUSD", "H1", px, t0.replace(minute=5 + i))
    st.add_tick("EURUSD", "H1", 1.3, t0.replace(hour=11))  # новый час закрывает свечу
    df = BarStore(str(tmp_path)).load("EURUSD", "H1")
    assert len(df) == 1
    row = df.iloc[0]
    assert (row.open, row.high, row.low, row.close) == (1.0, 1.2, 0.9, 1.1)


class FakeMarket:
    def __init__(self):
        self.bid, self.ask = 1.1000, 1.1001

    def connect(self, *a, **k): pass
    def account(self): return SimpleNamespace(equity=10000.0, currency="USD", login=1)
    def tick(self, s): return SimpleNamespace(bid=self.bid, ask=self.ask)
    def spec(self, s): return SymbolSpec(0.00001, 1.0, 0.01, 100, 0.01)
    def min_stop_distance(self, s): return 0.0


def test_paper_broker_sl_tp_and_persistence(tmp_path):
    m = FakeMarket()
    f = tmp_path / "paper.json"
    b = PaperBroker(m, str(f))
    b.connect()
    assert b.account().equity == 10000.0 and b.is_demo()
    b.open("EURUSD", 1, 0.1, 1.0950, 1.1100)          # вход по ask 1.1001
    assert len(b.positions()) == 1
    m.bid, m.ask = 1.1051, 1.1052
    assert b.account().equity == pytest.approx(10000 + 0.0050 * 0.1 * 100000)
    b2 = PaperBroker(m, str(f))                          # состояние пережило перезапуск
    assert len(b2.positions()) == 1
    m.bid, m.ask = 1.1100, 1.1101                       # тейк
    assert b2.positions() == []
    assert b2.balance == pytest.approx(10000 + 0.0099 * 0.1 * 100000)
    b2.open("EURUSD", -1, 0.1, 1.1150, 1.1000)
    p = b2.positions()[0]
    b2.modify_sl(p, 1.1120)
    m.bid, m.ask = 1.1119, 1.1121                       # стоп по ask
    assert b2.positions() == [] and b2.closed[-1]["reason"] == "стоп"
    assert "сделок" in b2.stats()
