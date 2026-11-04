"""Бэктест стратегии.

  python backtest.py --csv EURUSD_H1.csv                 # по CSV, экспортированному из MT5
  python backtest.py --mt5 EURUSD --bars 20000           # история прямо из терминала (Windows)
"""
import argparse
import sys

from alfabot.backtest import load_csv, run_backtest
from alfabot.config import load_config
from alfabot.risk import SymbolSpec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv")
    src.add_argument("--mt5", metavar="SYMBOL")
    ap.add_argument("--bars", type=int, default=20000)
    ap.add_argument("--equity", type=float, default=10000)
    ap.add_argument("--spread", type=float, default=None, help="спред в цене, напр. 0.00012")
    ap.add_argument("--tick-size", type=float, default=0.00001)
    ap.add_argument("--tick-value", type=float, default=1.0, help="стоимость тика на 1 лот в валюте счёта")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        cfg = load_config("config.example.yaml")
    spec = SymbolSpec(args.tick_size, args.tick_value, 0.01, 100.0, 0.01)
    spread = args.spread if args.spread is not None else 10 * args.tick_size

    if args.csv:
        df = load_csv(args.csv)
    else:
        try:
            import MetaTrader5 as mt5
        except ImportError:
            sys.exit("--mt5 работает только на Windows с установленным MetaTrader5")
        from alfabot.broker import MT5Broker
        b = MT5Broker(mt5, cfg.magic)
        a = cfg.account
        b.connect(a.login, a.password, a.server, a.terminal_path)
        sym = b.resolve_symbol(args.mt5)
        df = b.closed_bars(sym, cfg.timeframe, args.bars)
        s = b.spec(sym)
        spec = SymbolSpec(s.tick_size, s.tick_value, s.volume_min, s.volume_max, s.volume_step)
        if args.spread is None:
            spread = b.spread_points(sym) * b.symbol_info(sym).point
        b.shutdown()

    from alfabot.strategies import effective_params, get as get_strategy
    info = get_strategy(cfg.strategy.name)
    print(f"Стратегия: {info.title}")
    res = run_backtest(df, effective_params(cfg.strategy), spec, start_equity=args.equity,
                       risk_pct=cfg.risk.risk_per_trade_pct, spread=spread, max_lot=cfg.risk.max_lot,
                       compute_fn=info.compute)
    print(f"Баров: {len(df)}  период: {df.index[0]} — {df.index[-1]}")
    for k, v in res.stats().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
