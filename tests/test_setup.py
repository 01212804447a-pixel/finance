import os

import yaml

import setup_wizard as sw
from alfabot.config import load_config


def test_wizard_writes_env_and_config(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(sw, "CONFIG_FILE", tmp_path / "config.yaml")
    for k in ("ALFA_LOGIN", "ALFA_PASSWORD", "TG_TOKEN", "TG_CHAT_ID", "BOT_MODE"):
        monkeypatch.delenv(k, raising=False)

    sw.write_env({"ALFA_LOGIN": "123", "ALFA_PASSWORD": "p=ss#1", "TG_TOKEN": "t:k",
                  "TG_CHAT_ID": "42", "BOT_MODE": "paper"})
    assert sw.read_env()["ALFA_PASSWORD"] == "p=ss#1"
    cfg = sw.apply_settings(sw.read_config(), symbols="eurusd; gbpusd", risk=0.5, orders=False,
                            allow_real=False, show_browser=True, telegram_on=True)
    sw.write_config(cfg)

    c = load_config(tmp_path / "config.yaml")     # .env рядом подхватывается автоматически
    assert c.symbols == ["EURUSD", "GBPUSD"]
    assert c.risk.risk_per_trade_pct == 0.5
    assert c.web.mode == "paper" and c.web.headless is False and c.account.allow_real is False
    assert c.account.login == 123 and c.account.password == "p=ss#1"
    assert c.telegram.enabled and c.telegram.token == "t:k" and c.telegram.chat_id == "42"
    for k in ("ALFA_LOGIN", "ALFA_PASSWORD", "TG_TOKEN", "TG_CHAT_ID", "BOT_MODE"):
        os.environ.pop(k, None)


def test_orders_and_real_money_are_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(sw, "CONFIG_FILE", tmp_path / "config.yaml")
    cfg = sw.apply_settings(sw.read_config(), symbols="EURUSD", risk=1, orders=True, allow_real=False,
                            show_browser=False, telegram_on=False)
    assert cfg["web"]["mode"] == "live" and cfg["account"]["allow_real"] is False
    cfg = sw.apply_settings(cfg, symbols="EURUSD", risk=1, orders=True, allow_real=True,
                            show_browser=False, telegram_on=False)
    assert cfg["account"]["allow_real"] is True
    assert cfg["account"]["password"] is None  # пароль только в .env
    yaml.safe_dump(cfg)
