"""Главный цикл автономного бота."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .broker import BrokerError, MT5Broker
from .config import BotConfig
from .notifier import Notifier
from .risk import RiskGuard, position_size
from .strategy import Signal, compute, stop_levels, trailing_stop

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TradingBot:
    def __init__(self, cfg: BotConfig, broker: MT5Broker, notifier: Notifier,
                 clock: Callable[[], datetime] = utcnow):
        self.cfg = cfg
        self.broker = broker
        self.notifier = notifier
        self.clock = clock
        self.guard = RiskGuard(cfg.risk)
        self.last_bar: dict[str, str] = {}
        self.paused = False
        self.symbols: dict[str, str] = {}   # имя из конфига -> имя у брокера
        self._last_report_day = None
        self._load_state()

    # ---------- состояние ----------
    def _load_state(self) -> None:
        p = Path(self.cfg.state_file)
        if not p.exists():
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            self.guard.load_dict(d.get("guard", {}))
            self.last_bar = d.get("last_bar", {})
            self.paused = d.get("paused", False)
        except Exception as e:
            log.warning("не удалось прочитать состояние: %s", e)

    def _save_state(self) -> None:
        p = Path(self.cfg.state_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "guard": self.guard.to_dict(),
            "last_bar": self.last_bar,
            "paused": self.paused,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    # ---------- запуск ----------
    def start(self) -> None:
        acc = self.cfg.account
        self.broker.connect(acc.login, acc.password, acc.server, acc.terminal_path)
        demo = self.broker.is_demo()
        if not demo and not acc.allow_real:
            self.broker.shutdown()
            raise SystemExit(
                "Это РЕАЛЬНЫЙ счёт, а в config.yaml стоит allow_real: false. "
                "Сначала проверьте бота на демо-счёте. Чтобы торговать реальными деньгами, "
                "осознанно поставьте allow_real: true."
            )
        for name in self.cfg.symbols:
            self.symbols[name] = self.broker.resolve_symbol(name)
        info = self.broker.account()
        kind = getattr(self.broker, "label", None) or ("ДЕМО" if demo else "РЕАЛЬНЫЙ")
        self.notifier.send(
            f"🤖 Бот запущен ({kind} счёт {info.login}). "
            f"Средства: {info.equity:.2f} {info.currency}. "
            f"Инструменты: {', '.join(self.symbols.values())}, ТФ {self.cfg.timeframe}."
        )

    def run_forever(self) -> None:
        self.start()
        backoff = 5
        while True:
            try:
                if not self.broker.is_alive():
                    raise BrokerError("потеряно соединение с терминалом")
                self.step()
                backoff = 5
                time.sleep(self.cfg.poll_seconds)
            except KeyboardInterrupt:
                self.notifier.send("⏹ Бот остановлен вручную")
                break
            except Exception as e:
                log.exception("ошибка цикла: %s", e)
                self.notifier.send(f"⚠️ Ошибка: {e}. Переподключение через {backoff} c")
                shot = getattr(self.broker, "screenshot", lambda: None)()
                if shot:
                    self.notifier.send_photo(shot, "Экран терминала в момент ошибки")
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
                try:
                    self.broker.shutdown()
                    acc = self.cfg.account
                    self.broker.connect(acc.login, acc.password, acc.server, acc.terminal_path)
                except Exception as e2:
                    log.error("переподключение не удалось: %s", e2)
        self.broker.shutdown()

    # ---------- один шаг ----------
    def step(self) -> None:
        now = self.clock()
        self._handle_commands()
        acc = self.broker.account()
        self.guard.update(now, acc.equity)
        breach = self.guard.check(acc.equity)
        if breach:
            self.close_all(f"лимит риска: {breach}")
            self.notifier.send(f"🛑 {breach}. Все позиции закрыты, новые сделки "
                               f"{'остановлены до ручного сброса (удалите state.json)' if self.guard.halted_forever else 'до завтра запрещены'}.")

        if self._should_close_for_weekend(now):
            if self.broker.positions():
                self.close_all("закрытие перед выходными")
                self.notifier.send("📅 Позиции закрыты перед выходными")

        for name, sym in self.symbols.items():
            try:
                self._process_symbol(sym, now)
            except BrokerError as e:
                log.warning("%s: %s", sym, e)
        self._daily_report(now, acc)
        self._save_state()

    def _process_symbol(self, sym: str, now: datetime) -> None:
        p = self.cfg.strategy
        bars = self.broker.closed_bars(sym, self.cfg.timeframe, p.min_bars + 300)
        data = compute(bars, p)
        row = data.iloc[-1]
        bar_time = str(data.index[-1])
        new_bar = self.last_bar.get(sym) != bar_time
        atr_value = float(row["atr"])
        positions = self.broker.positions(sym)

        # 1) сопровождение открытых позиций: безубыток и трейлинг
        tick = self.broker.tick(sym)
        min_dist = self.broker.min_stop_distance(sym)
        for pos in positions:
            side = Signal.BUY if pos.side > 0 else Signal.SELL
            price = tick.bid if pos.side > 0 else tick.ask
            new_sl = trailing_stop(side, pos.price_open, pos.sl or (
                pos.price_open - 1e9 if pos.side > 0 else pos.price_open + 1e9), price, atr_value, p)
            # двигаем стоп шагами не меньше 0.1 ATR, чтобы не дёргать брокера каждую секунду
            big_enough = not pos.sl or abs(new_sl - pos.sl) >= 0.1 * atr_value if new_sl is not None else False
            if new_sl is not None and big_enough and abs(price - new_sl) >= min_dist:
                self.broker.modify_sl(pos, new_sl)
                log.info("%s #%s стоп -> %.5f", sym, pos.ticket, new_sl)

        if not new_bar:
            return
        self.last_bar[sym] = bar_time
        signal = Signal(int(row["signal"]))

        # 2) выход по противоположному пересечению
        if p.exit_on_opposite:
            for pos in positions:
                opposite = (pos.side > 0 and bool(row["cross_down"])) or (pos.side < 0 and bool(row["cross_up"]))
                if opposite:
                    self.broker.close(pos, "alfabot exit")
                    self.notifier.send(f"↩️ {sym}: закрыта #{pos.ticket} по обратному сигналу, P/L {pos.profit:.2f}")
            positions = self.broker.positions(sym)

        # 3) вход
        if signal == Signal.NONE or positions:
            return
        reason = self._entry_blocked(sym, now)
        if reason:
            log.info("%s: сигнал %s пропущен — %s", sym, signal.name, reason)
            return
        tick = self.broker.tick(sym)
        entry = tick.ask if signal == Signal.BUY else tick.bid
        sl, tp = stop_levels(signal, entry, atr_value, p)
        if abs(entry - sl) < min_dist:
            log.info("%s: стоп ближе минимального уровня брокера", sym)
            return
        equity = self.broker.account().equity
        lots = position_size(equity, self.cfg.risk.risk_per_trade_pct, abs(entry - sl),
                             self.broker.spec(sym), self.cfg.risk.max_lot)
        if lots <= 0:
            log.info("%s: объём слишком мал для заданного риска", sym)
            return
        self.broker.open(sym, int(signal), lots, sl, tp)
        self.notifier.send(
            f"{'🟢 BUY' if signal == Signal.BUY else '🔴 SELL'} {sym} {lots} лот по {entry:.5f}, "
            f"SL {sl:.5f}, TP {tp:.5f} (риск {self.cfg.risk.risk_per_trade_pct}%)"
        )

    def _entry_blocked(self, sym: str, now: datetime) -> str | None:
        if self.paused:
            return "бот на паузе"
        if not self.guard.can_open:
            return "сработал лимит риска"
        s = self.cfg.schedule
        wd = now.weekday()
        if wd >= 5:
            return "выходные"
        if not (s.trade_start_hour_utc <= now.hour < s.trade_end_hour_utc):
            return "вне торгового окна"
        if wd == 4 and now.hour >= s.no_new_trades_friday_after_utc:
            return "вечер пятницы"
        if len(self.broker.positions()) >= self.cfg.risk.max_open_positions:
            return "достигнут лимит открытых позиций"
        spread = self.broker.spread_points(sym)
        if spread > self.cfg.risk.max_spread_points:
            return f"широкий спред {spread:.0f} пп"
        return None

    def _should_close_for_weekend(self, now: datetime) -> bool:
        h = self.cfg.schedule.close_all_friday_utc
        return h is not None and now.weekday() == 4 and now.hour >= h

    def close_all(self, why: str) -> None:
        for pos in self.broker.positions():
            try:
                self.broker.close(pos, "alfabot " + why[:20])
                log.info("закрыта #%s (%s)", pos.ticket, why)
            except BrokerError as e:
                log.error("не удалось закрыть #%s: %s", pos.ticket, e)
                self.notifier.send(f"❗ Не удалось закрыть #{pos.ticket} {pos.symbol}: {e}")

    def status_text(self) -> str:
        acc = self.broker.account()
        lines = [f"Баланс {acc.balance:.2f}, средства {acc.equity:.2f} {acc.currency}",
                 f"Пауза: {'да' if self.paused else 'нет'}; лимит дня: "
                 f"{'сработал' if self.guard.halted_today else 'ок'}"]
        for pos in self.broker.positions():
            lines.append(f"{pos.symbol} {'BUY' if pos.side > 0 else 'SELL'} {pos.volume} "
                         f"@{pos.price_open} SL {pos.sl} TP {pos.tp} P/L {pos.profit:.2f}")
        return "\n".join(lines)

    def _handle_commands(self) -> None:
        for cmd in self.notifier.poll_commands():
            if cmd == "/status":
                self.notifier.send(self.status_text())
            elif cmd == "/pause":
                self.paused = True
                self.notifier.send("⏸ Пауза: новые сделки не открываются, открытые сопровождаются")
            elif cmd == "/resume":
                self.paused = False
                self.notifier.send("▶️ Торговля возобновлена")
            elif cmd == "/screenshot":
                shot = getattr(self.broker, "screenshot", lambda: None)()
                if shot:
                    self.notifier.send_photo(shot, "Терминал сейчас")
                else:
                    self.notifier.send("Скриншот доступен только в браузерном режиме")
            elif cmd == "/stats":
                stats = getattr(self.broker, "stats", None)
                self.notifier.send(stats() if stats else self.status_text())
            elif cmd == "/closeall":
                self.paused = True
                self.close_all("команда /closeall")
                self.notifier.send("Все позиции бота закрыты, бот на паузе (/resume — продолжить)")
            else:
                self.notifier.send("Команды:\n/status — баланс и позиции\n/pause — не открывать новые сделки\n"
                                   "/resume — продолжить\n/closeall — закрыть всё и пауза\n"
                                   "/screenshot — снимок терминала\n/stats — статистика")

    def _daily_report(self, now: datetime, acc) -> None:
        if now.hour != 21 or self._last_report_day == now.date():
            return
        self._last_report_day = now.date()
        start = self.guard.day_start_equity or acc.equity
        change = acc.equity - start
        self.notifier.send(f"📊 Итог дня: {change:+.2f} {acc.currency} "
                           f"({change / start * 100 if start else 0:+.2f}%).\n" + self.status_text())
