"""Проверка торговли через браузер на ДЕМО-счёте: открыть -> найти -> перенести стоп -> закрыть.

  ALFA_LOGIN=... ALFA_PASSWORD=... python scripts/web_smoke_test.py --config config.yaml [--symbol EURUSD]

На реальном счёте скрипт откажется работать.
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alfabot.config import load_config  # noqa: E402
from alfabot.datafeed import BarStore, DataFeed  # noqa: E402
from alfabot.risk import position_size  # noqa: E402
from alfabot.web.broker import WebBroker  # noqa: E402
from alfabot.web.terminal import WebTerminal  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--symbol", action="append")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = load_config(args.config)
    w = cfg.web
    t = WebTerminal(w.url, cfg.account.login, cfg.account.password, headless=w.headless,
                    profile_dir=w.profile_dir)
    b = WebBroker(t, DataFeed("yahoo", BarStore("/tmp/alfabot_bars")), comment_tag=w.comment_tag)
    b.connect()
    try:
        if not b.is_demo():
            sys.exit("Это не демо-счёт — тест отменён.")
        for name in args.symbol or ["EURUSD"]:
            sym = b.resolve_symbol(name)
            tk = b.tick(sym)
            pt = b.symbol_info(sym).point
            spec = b.spec(sym)
            dist = 300 * pt
            lots = position_size(b.account().equity, 0.01, dist, spec, 0.05) or 0.01
            for side in (1, -1):
                entry = tk.ask if side > 0 else tk.bid
                sl, tp = entry - side * dist, entry + side * 2 * dist
                res = b.open(sym, side, lots, sl, tp)
                print(f"OK открыта {res}")
                pos = [p for p in b.positions(sym) if p.ticket == res["ticket"]]
                assert pos, "позиция не найдена в таблице"
                p = pos[0]
                print(f"OK в таблице: {p}")
                assert abs(p.sl - sl) < 2 * pt and abs(p.tp - tp) < 2 * pt, "SL/TP не совпадают"
                new_sl = entry - side * dist / 2
                b.modify_sl(p, new_sl)
                p2 = [x for x in b.positions(sym) if x.ticket == p.ticket][0]
                assert abs(p2.sl - new_sl) < 2 * pt, f"стоп не перенесён: {p2.sl} vs {new_sl}"
                print(f"OK стоп перенесён на {p2.sl}")
                b.close(p2)
                assert not [x for x in b.positions(sym) if x.ticket == p.ticket], "позиция не закрылась"
                print(f"OK закрыта #{p.ticket}")
        print("ВСЁ РАБОТАЕТ:", b.account())
    finally:
        b.shutdown()


if __name__ == "__main__":
    main()
