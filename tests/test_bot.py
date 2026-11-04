from datetime import datetime, timezone

import pytest

from alfabot.bot import TradingBot
from alfabot.broker import MT5Broker
from alfabot.config import BotConfig
from alfabot.notifier import Notifier
from alfabot.strategy import compute
from tests.helpers import FakeMT5, synthetic_bars

MONDAY_NOON = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)


def make_bot(tmp_path, bars, trade_mode=0, allow_real=False, clock=MONDAY_NOON):
    cfg = BotConfig(symbols=["EURUSD"], state_file=str(tmp_path / "state.json"))
    cfg.account.allow_real = allow_real
    fake = FakeMT5(bars, trade_mode=trade_mode)
    bot = TradingBot(cfg, MT5Broker(fake, cfg.magic), Notifier(False, None, None), clock=lambda: clock)
    return bot, fake


def bars_ending_with_signal(sig_value):
    df = synthetic_bars(4000)
    data = compute(df, BotConfig().strategy)
    pos = [i for i, s in enumerate(data["signal"]) if s == sig_value and i > 700][0]
    # +1 бар — он будет «текущим незакрытым» и отбросится
    return df.iloc[: pos + 2]


def test_refuses_real_account(tmp_path):
    bot, _ = make_bot(tmp_path, synthetic_bars(800), trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_REAL)
    with pytest.raises(SystemExit):
        bot.start()


def test_real_allowed_explicitly(tmp_path):
    bot, _ = make_bot(tmp_path, synthetic_bars(800), trade_mode=2, allow_real=True)
    bot.start()


@pytest.mark.parametrize("sig", [1, -1])
def test_opens_on_signal_with_sl_tp(tmp_path, sig):
    bot, fake = make_bot(tmp_path, bars_ending_with_signal(sig))
    bot.start()
    bot.step()
    opens = [r for r in fake.requests if r["action"] == fake.TRADE_ACTION_DEAL]
    assert len(opens) == 1
    r = opens[0]
    assert r["type"] == (fake.ORDER_TYPE_BUY if sig == 1 else fake.ORDER_TYPE_SELL)
    assert r["sl"] > 0 and r["tp"] > 0 and r["volume"] > 0
    if sig == 1:
        assert r["sl"] < r["price"] < r["tp"]
    else:
        assert r["tp"] < r["price"] < r["sl"]
    # повторный шаг на том же баре не открывает ещё одну сделку
    bot.step()
    assert len([r for r in fake.requests if r["action"] == fake.TRADE_ACTION_DEAL]) == 1


def test_no_entry_on_weekend(tmp_path):
    sat = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    bot, fake = make_bot(tmp_path, bars_ending_with_signal(1), clock=sat)
    bot.start(); bot.step()
    assert not fake.requests


def test_daily_loss_closes_everything(tmp_path):
    bot, fake = make_bot(tmp_path, bars_ending_with_signal(1))
    bot.start(); bot.step()
    assert fake.positions_get()
    fake.equity = 9600  # -4% за день
    bot.step()
    assert not fake.positions_get()
    assert not bot.guard.can_open


def test_ignores_foreign_positions(tmp_path):
    from types import SimpleNamespace
    bot, fake = make_bot(tmp_path, synthetic_bars(800))
    fake._positions.append(SimpleNamespace(ticket=99, symbol="EURUSD", magic=1, type=0, volume=1,
                                           price_open=1.1, sl=0, tp=0, profit=0))
    bot.start()
    bot.close_all("test")
    assert len(fake.positions_get()) == 1


def test_state_persists(tmp_path):
    bot, _ = make_bot(tmp_path, bars_ending_with_signal(1))
    bot.start(); bot.step()
    bot2, fake2 = make_bot(tmp_path, bars_ending_with_signal(1))
    assert bot2.last_bar == bot.last_bar
    bot2.start(); bot2.step()
    assert not fake2.requests  # тот же бар уже обработан
