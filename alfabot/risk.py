"""Риск-менеджмент: размер позиции и защитные лимиты."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class RiskParams:
    risk_per_trade_pct: float = 1.0     # % от средств на одну сделку
    max_daily_loss_pct: float = 3.0     # при просадке за день -> закрыть всё и стоп до завтра
    max_total_drawdown_pct: float = 15.0  # от пика средств -> полная остановка бота
    max_open_positions: int = 3
    max_spread_points: int = 30
    max_lot: float = 1.0                # жёсткий потолок объёма одной сделки


@dataclass
class SymbolSpec:
    tick_size: float
    tick_value: float      # стоимость одного тика для 1 лота в валюте счёта
    volume_min: float
    volume_max: float
    volume_step: float


def position_size(equity: float, risk_pct: float, sl_distance: float,
                  spec: SymbolSpec, max_lot: float) -> float:
    """Лот, при котором срабатывание стопа = risk_pct % средств. 0 -> сделку не открывать."""
    if equity <= 0 or sl_distance <= 0 or spec.tick_size <= 0 or spec.tick_value <= 0:
        return 0.0
    risk_money = equity * risk_pct / 100.0
    loss_per_lot = sl_distance / spec.tick_size * spec.tick_value
    raw = risk_money / loss_per_lot
    step = spec.volume_step if spec.volume_step > 0 else 0.01
    lots = math.floor(raw / step + 1e-9) * step
    lots = min(lots, spec.volume_max, max_lot)
    if lots < spec.volume_min:
        # даже минимальный лот превысил бы допустимый риск -> пропускаем
        return 0.0
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(lots, decimals)


@dataclass
class RiskGuard:
    """Хранит состояние дня и пика средств, решает, можно ли торговать."""
    params: RiskParams
    day: date | None = None
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    halted_today: bool = False
    halted_forever: bool = False
    reasons: list[str] = field(default_factory=list)

    def update(self, now: datetime, equity: float) -> None:
        today = now.date()
        if self.day != today:
            self.day = today
            self.day_start_equity = equity
            self.halted_today = False
        self.peak_equity = max(self.peak_equity, equity)

    def check(self, equity: float) -> str | None:
        """Возвращает причину остановки в момент нарушения лимита (один раз)."""
        if self.halted_forever:
            return None
        if self.peak_equity > 0:
            dd = (self.peak_equity - equity) / self.peak_equity * 100
            if dd >= self.params.max_total_drawdown_pct:
                self.halted_forever = True
                reason = f"просадка от пика {dd:.2f}% >= {self.params.max_total_drawdown_pct}%"
                self.reasons.append(reason)
                return reason
        if self.day_start_equity > 0:
            day_dd = (self.day_start_equity - equity) / self.day_start_equity * 100
            if day_dd >= self.params.max_daily_loss_pct:
                if not self.halted_today:
                    self.halted_today = True
                    reason = f"дневной убыток {day_dd:.2f}% >= {self.params.max_daily_loss_pct}%"
                    self.reasons.append(reason)
                    return reason
        return None

    @property
    def can_open(self) -> bool:
        return not (self.halted_today or self.halted_forever)

    def to_dict(self) -> dict:
        return {
            "day": self.day.isoformat() if self.day else None,
            "day_start_equity": self.day_start_equity,
            "peak_equity": self.peak_equity,
            "halted_today": self.halted_today,
            "halted_forever": self.halted_forever,
        }

    def load_dict(self, d: dict) -> None:
        self.day = date.fromisoformat(d["day"]) if d.get("day") else None
        self.day_start_equity = float(d.get("day_start_equity", 0.0))
        self.peak_equity = float(d.get("peak_equity", 0.0))
        self.halted_today = bool(d.get("halted_today", False))
        self.halted_forever = bool(d.get("halted_forever", False))
