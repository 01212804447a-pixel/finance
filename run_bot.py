"""Запуск бота: python run_bot.py [--config config.yaml]"""
import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from alfabot.bot import TradingBot
from alfabot.broker import MT5Broker
from alfabot.config import load_config
from alfabot.notifier import Notifier


def main() -> None:
    for stream in (sys.stdout, sys.stderr):  # эмодзи и кириллица в консоли Windows
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="AlfaBot для MetaTrader 5")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = load_config(args.config)
    Path(cfg.log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.handlers.RotatingFileHandler(cfg.log_file, maxBytes=5_000_000,
                                                 backupCount=5, encoding="utf-8"),
        ],
    )
    try:
        import MetaTrader5 as mt5
    except ImportError:
        sys.exit("Нужен пакет MetaTrader5 (только Windows): pip install MetaTrader5")

    broker = MT5Broker(mt5, magic=cfg.magic, deviation=cfg.deviation_points)
    notifier = Notifier(cfg.telegram.enabled, cfg.telegram.token, cfg.telegram.chat_id,
                        outbox_dir=str(Path(cfg.state_file).parent / "outbox"))
    bot = TradingBot(cfg, broker, notifier)
    if cfg.lab:
        from alfabot.lab import StrategyLab
        bot.lab = StrategyLab(str(Path(cfg.state_file).parent / "lab_state.json"))
    bot.run_forever()


if __name__ == "__main__":
    main()
