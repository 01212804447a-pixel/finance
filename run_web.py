"""Бот через браузер — MetaTrader 5 устанавливать не нужно.

  python run_web.py                      # режим из config.yaml (по умолчанию paper — без реальных ордеров)
  python run_web.py --check              # только войти, показать счёт, позиции и котировки, сделать скриншот

Логин/пароль: переменные окружения ALFA_LOGIN и ALFA_PASSWORD (или account.login/password в config.yaml).
"""
import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from alfabot.bot import TradingBot
from alfabot.config import load_config
from alfabot.datafeed import BarStore, DataFeed
from alfabot.notifier import Notifier
from alfabot.paper import PaperBroker
from alfabot.web.broker import WebBroker
from alfabot.web.terminal import WebTerminal


def build(cfg):
    w = cfg.web
    terminal = WebTerminal(w.url, cfg.account.login, cfg.account.password,
                           headless=w.headless, profile_dir=w.profile_dir)
    feed = DataFeed(w.data_source, BarStore(str(Path(cfg.state_file).parent / "bars")))
    broker = WebBroker(terminal, feed, comment_tag=w.comment_tag, timeframe=cfg.timeframe,
                       contract_size=w.contract_size, contract_sizes=w.contract_sizes,
                       volume_min=w.volume_min, volume_step=w.volume_step, volume_max=cfg.risk.max_lot)
    if w.mode == "paper":
        paper_file = str(Path(cfg.state_file).with_name("paper_state.json"))
        return PaperBroker(broker, paper_file, w.paper_balance), terminal
    return broker, terminal


def check(cfg) -> None:
    broker, terminal = build(cfg)
    market = broker.m if isinstance(broker, PaperBroker) else broker
    market.connect()
    try:
        acc = market.account()
        print(f"Счёт {acc.login} ({acc.server}), {'ДЕМО' if market.is_demo() else 'РЕАЛЬНЫЙ'}: "
              f"баланс {acc.balance} {acc.currency}, средства {acc.equity}")
        for p in terminal.positions():
            print(f"  позиция #{p.ticket} {p.symbol} {'BUY' if p.side > 0 else 'SELL'} {p.volume} "
                  f"SL {p.sl} TP {p.tp} P/L {p.profit} [{p.comment}]")
        for name in cfg.symbols:
            try:
                s = market.resolve_symbol(name)
                t = market.tick(s)
                bars = market.closed_bars(s, cfg.timeframe, 400)
                sp = market.spec(s)
                pip_value = sp.tick_value * 10   # 1 пункт = 10 минимальных шагов цены
                print(f"  {name} -> {s}: bid {t.bid} ask {t.ask}, свечей {cfg.timeframe}: {len(bars)}, "
                      f"последняя {bars.index[-1]}; 1 пункт при 1 лоте = {pip_value:.2f} {acc.currency}")
            except Exception as e:
                print(f"  {name}: ОШИБКА {e}")
        Path("logs").mkdir(exist_ok=True)
        Path("logs/terminal.png").write_bytes(terminal.screenshot())
        print("Скриншот: logs/terminal.png")
    finally:
        market.shutdown()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # эмодзи и кириллица в консоли Windows
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="AlfaBot через веб-терминал")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--check", action="store_true", help="проверить вход и данные, ничего не торговать")
    args = ap.parse_args()

    cfg = load_config(args.config)
    Path(cfg.log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.handlers.RotatingFileHandler(cfg.log_file, maxBytes=5_000_000,
                                                       backupCount=5, encoding="utf-8")],
    )
    if args.check:
        check(cfg)
        return
    broker, _ = build(cfg)
    notifier = Notifier(cfg.telegram.enabled, cfg.telegram.token, cfg.telegram.chat_id)
    TradingBot(cfg, broker, notifier).run_forever()


if __name__ == "__main__":
    main()
