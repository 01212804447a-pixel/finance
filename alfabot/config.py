"""Загрузка config.yaml в типизированные настройки."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .risk import RiskParams
from .strategy import StrategyParams


@dataclass
class AccountConfig:
    login: int | None = None
    password: str | None = None
    server: str | None = None          # например "AlfaForex-MT5" — смотрите в окне входа MT5
    terminal_path: str | None = None   # путь к terminal64.exe, если не стандартный
    allow_real: bool = False           # пока False, бот торгует ТОЛЬКО на демо-счёте


@dataclass
class ScheduleConfig:
    # часы указываются по UTC; торговля открывается только в этом окне
    trade_start_hour_utc: int = 6
    trade_end_hour_utc: int = 20
    no_new_trades_friday_after_utc: int = 18
    close_all_friday_utc: int | None = 20   # закрыть всё перед выходными (None — не закрывать)


@dataclass
class TelegramConfig:
    enabled: bool = False
    token: str | None = None
    chat_id: str | None = None
    hourly_report: bool = True     # раз в час (в торговые часы) писать, что бот видит на рынке


@dataclass
class WebConfig:
    url: str = "https://trade.alfaforex.ru/terminal"
    mode: str = "paper"                 # paper — виртуальные сделки, live — настоящие ордера
    headless: bool = True               # false — видно окно браузера
    profile_dir: str = "browser_profile"
    comment_tag: str = "alfabot"        # метка сделок бота в поле «Комментарий»
    data_source: str = "yahoo"          # yahoo | local (свечи из котировок терминала)
    contract_size: float = 100000.0     # размер лота для валютных пар
    contract_sizes: dict = field(default_factory=lambda: {"XAUUSD": 100, "XAGUSD": 5000})
    volume_min: float = 0.01
    volume_step: float = 0.01
    paper_balance: float | None = None  # None — стартовый баланс = средства реального счёта


@dataclass
class BotConfig:
    symbols: list[str] = field(default_factory=lambda: ["EURUSD", "GBPUSD", "USDJPY"])
    timeframe: str = "H1"
    magic: int = 770077
    poll_seconds: int = 10
    deviation_points: int = 20
    state_file: str = "state.json"
    log_file: str = "logs/bot.log"
    lab: bool = True                 # лаборатория стратегий: еженедельный рейтинг в Telegram
    account: AccountConfig = field(default_factory=AccountConfig)
    strategy: StrategyParams = field(default_factory=StrategyParams)
    risk: RiskParams = field(default_factory=RiskParams)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    web: WebConfig = field(default_factory=WebConfig)


def _build(cls, data: dict | None):
    data = data or {}
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{cls.__name__}: неизвестные параметры {sorted(unknown)}")
    return cls(**data)


def load_dotenv(path: str | os.PathLike) -> None:
    """Читает файл .env (КЛЮЧ=значение) в переменные окружения, не перезаписывая уже заданные."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def load_config(path: str | os.PathLike) -> BotConfig:
    load_dotenv(Path(path).resolve().parent / ".env")
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    nested = {
        "account": AccountConfig,
        "strategy": StrategyParams,
        "risk": RiskParams,
        "schedule": ScheduleConfig,
        "telegram": TelegramConfig,
        "web": WebConfig,
    }
    kwargs = {k: v for k, v in raw.items() if k not in nested}
    for key, cls in nested.items():
        kwargs[key] = _build(cls, raw.get(key))
    cfg = _build(BotConfig, kwargs)
    from .strategies import get as get_strategy
    info = get_strategy(cfg.strategy.name)          # проверка имени стратегии
    if info.name != "ema_cross":
        cfg.timeframe = info.timeframe              # дневные стратегии всегда на D1

    # секреты можно держать в переменных окружения, а не в файле
    acc = cfg.account
    acc.login = int(os.environ["ALFA_LOGIN"]) if os.environ.get("ALFA_LOGIN") else acc.login
    acc.password = os.environ.get("ALFA_PASSWORD", acc.password)
    acc.server = os.environ.get("ALFA_SERVER", acc.server)
    if os.environ.get("BOT_MODE"):
        cfg.web.mode = os.environ["BOT_MODE"]
    if cfg.web.mode not in ("paper", "live"):
        raise ValueError("web.mode должен быть paper или live")
    tg = cfg.telegram
    tg.token = os.environ.get("TG_TOKEN", tg.token)
    tg.chat_id = os.environ.get("TG_CHAT_ID", tg.chat_id)
    return cfg
