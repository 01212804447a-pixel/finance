"""Проверка: видит ли Python терминал MT5 и какие символы доступны. Ничего не торгует."""
import sys

from alfabot.config import load_config

try:
    import MetaTrader5 as mt5
except ImportError:
    sys.exit("Пакет MetaTrader5 не установлен (нужен Windows): pip install MetaTrader5")

from alfabot.broker import MT5Broker

cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
b = MT5Broker(mt5, cfg.magic)
a = cfg.account
b.connect(a.login, a.password, a.server, a.terminal_path)
info = b.account()
print(f"Счёт {info.login} на {info.server}: {'ДЕМО' if b.is_demo() else 'РЕАЛЬНЫЙ'}")
print(f"Баланс {info.balance} {info.currency}, средства {info.equity}, плечо 1:{info.leverage}")
for name in cfg.symbols:
    try:
        s = b.resolve_symbol(name)
        print(f"  {name} -> {s}: спред {b.spread_points(s):.0f} пп, баров {len(b.closed_bars(s, cfg.timeframe, 500))}")
    except Exception as e:
        print(f"  {name}: ОШИБКА {e}")
b.shutdown()
