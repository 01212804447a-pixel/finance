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
from .strategies import effective_params, get as get_strategy
from .strategy import Signal, stop_levels, trailing_stop

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
        self.market: dict[str, tuple[str, object]] = {}   # символ -> (время свечи, строка индикаторов)
        self._last_errors: dict[str, str] = {}
        self.entry_done: dict[str, str] = {}     # символ -> свеча, по сигналу которой вход уже решён
        self._skip_notified: dict[str, str] = {}
        self.strategy = get_strategy(cfg.strategy.name)
        self.params = effective_params(cfg.strategy)
        self.lab = None                          # лаборатория стратегий (подключается в run_web)
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
            self.entry_done = d.get("entry_done", {})
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
            "entry_done": self.entry_done,
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
                getattr(self.notifier, "flush", lambda t: None)(5)
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

        if self._should_close_for_weekend(now) and not self.strategy.holds_over_weekend:
            if self.broker.positions():
                self.close_all("закрытие перед выходными")
                self.notifier.send("📅 Позиции закрыты перед выходными")

        new_bars = []
        for name, sym in self.symbols.items():
            try:
                if self._process_symbol(sym, now):
                    new_bars.append(sym)
            except BrokerError as e:
                log.warning("%s: %s", sym, e)
                key = f"{sym}: {e}"
                if self._last_errors.get(sym) != key:   # одна и та же ошибка — одно сообщение
                    self._last_errors[sym] = key
                    self.notifier.send(f"⚠️ {key}")
            else:
                self._last_errors.pop(sym, None)
        if new_bars and self.cfg.telegram.hourly_report and self._in_trading_hours(now):
            self.notifier.send(self.market_text(f"🕐 Закрылась свеча {self.cfg.timeframe}, проверил рынок:"))
        self._daily_report(now, acc)
        if self.lab is not None:
            self.lab.tick(now, self.notifier)
        self._save_state()

    def _process_symbol(self, sym: str, now: datetime) -> bool:
        """Возвращает True, если обработана новая закрытая свеча."""
        p = self.params
        bars = self.broker.closed_bars(sym, self.cfg.timeframe, self.strategy.warmup + 300)
        data = self.strategy.compute(bars, p)
        row = data.iloc[-1]
        bar_time = str(data.index[-1])
        new_bar = self.last_bar.get(sym) != bar_time
        atr_value = float(row["atr"])
        self.market[sym] = (bar_time, row)
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

        # 2) выход по сигналу стратегии — один раз на новой свече
        if new_bar:
            self.last_bar[sym] = bar_time
            for pos in positions:
                if (pos.side > 0 and bool(row["exit_long"])) or (pos.side < 0 and bool(row["exit_short"])):
                    self.broker.close(pos, "alfabot exit")
                    self.notifier.send(f"↩️ {sym}: закрыта #{pos.ticket} по сигналу выхода, P/L {pos.profit:.2f}")
            positions = self.broker.positions(sym)

        # 3) вход. Если вход сейчас запрещён (ночь, выходные, спред), сигнал этой свечи ждёт
        #    и исполнится, когда станет можно, — пока не закроется следующая свеча.
        signal = Signal(int(row["signal"]))
        if signal == Signal.NONE or positions or self.entry_done.get(sym) == bar_time:
            return new_bar
        reason = self._entry_blocked(sym, now)
        if reason:
            if self._skip_notified.get(sym) != bar_time + reason:
                self._skip_notified[sym] = bar_time + reason
                log.info("%s: сигнал %s ждёт — %s", sym, signal.name, reason)
                self.notifier.send(f"⏳ {sym}: сигнал {'ПОКУПКА' if signal > 0 else 'ПРОДАЖА'} отложен — {reason}. "
                                   f"Войду, когда станет можно (пока сигнал актуален).")
            return new_bar
        tick = self.broker.tick(sym)
        entry = tick.ask if signal == Signal.BUY else tick.bid
        sl, tp = stop_levels(signal, entry, atr_value, p)
        if abs(entry - sl) < min_dist:
            log.info("%s: стоп ближе минимального уровня брокера", sym)
            self.entry_done[sym] = bar_time
            return new_bar
        equity = self.broker.account().equity
        lots = position_size(equity, self.cfg.risk.risk_per_trade_pct, abs(entry - sl),
                             self.broker.spec(sym), self.cfg.risk.max_lot)
        if lots <= 0:
            log.info("%s: объём слишком мал для заданного риска", sym)
            self.notifier.send(f"⏭ {sym}: сигнал пропущен — минимальный лот больше допустимого риска")
            self.entry_done[sym] = bar_time
            return new_bar
        self.entry_done[sym] = bar_time   # до отправки: при сбое не открыть дубль
        self.broker.open(sym, int(signal), lots, sl, tp)
        digits = 3 if entry > 20 else 5
        self.notifier.send(
            f"{'🟢 BUY' if signal == Signal.BUY else '🔴 SELL'} {sym} {lots} лот по {entry:.{digits}f}, "
            f"SL {sl:.{digits}f}, TP {f'{tp:.{digits}f}' if tp else 'нет (выход по сигналу)'} "
            f"(риск {self.cfg.risk.risk_per_trade_pct}%)"
        )
        return new_bar

    def _in_trading_hours(self, now: datetime) -> bool:
        s = self.cfg.schedule
        return now.weekday() < 5 and s.trade_start_hour_utc <= now.hour < s.trade_end_hour_utc

    def market_text(self, header: str = "📈 Рынок сейчас:") -> str:
        """Что бот видит по каждой паре и чего ждёт для входа."""
        lines = [header, f"Стратегия: {self.strategy.title}"]
        if not self.market:
            return "\n".join(lines) + "\nданных ещё нет — подождите минуту после запуска"
        open_syms = {x.symbol for x in self.broker.positions()}
        for sym, (bar_time, r) in self.market.items():
            text = ("есть открытая сделка, сопровождаю её" if sym in open_syms
                    else self.strategy.describe(r, self.params))
            lines.append(f"• {sym}: {r['close']:.5g} — {text}")
        lines.append(f"Свеча: {str(next(iter(self.market.values()))[0])[:16]} UTC")
        return "\n".join(lines)

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
            elif cmd == "/lab":
                self.notifier.send(self.lab.report_text() if self.lab else "Лаборатория выключена (lab: false)")
            elif cmd == "/market":
                self.notifier.send(self.market_text())
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
                                   "/screenshot — снимок терминала\n/stats — статистика\n/market — что бот видит на рынке\n/lab — рейтинг стратегий на новых данных")

    def _daily_report(self, now: datetime, acc) -> None:
        if now.hour != 21 or self._last_report_day == now.date():
            return
        self._last_report_day = now.date()
        start = self.guard.day_start_equity or acc.equity
        change = acc.equity - start
        self.notifier.send(f"📊 Итог дня: {change:+.2f} {acc.currency} "
                           f"({change / start * 100 if start else 0:+.2f}%).\n" + self.status_text())
