"""Мастер настройки AlfaBot: окно, где по порядку вписываются все данные.

Запуск: двойной клик по 2-settings.bat (Windows) или `python setup_wizard.py`.
Сохраняет логин/пароль/Telegram в файл .env, остальные настройки — в config.yaml.
Если графического окна нет (сервер), работает в текстовом режиме.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
CONFIG_FILE = ROOT / "config.yaml"
EXAMPLE_FILE = ROOT / "config.example.yaml"

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


# ---------------------------------------------------------------- данные
def read_env() -> dict:
    out = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_env(values: dict) -> None:
    lines = [
        "# Создано мастером настройки (2-settings.bat). Не публикуйте этот файл — тут пароль.",
        f"ALFA_LOGIN={values.get('ALFA_LOGIN', '')}",
        f"ALFA_PASSWORD={values.get('ALFA_PASSWORD', '')}",
        f"TG_TOKEN={values.get('TG_TOKEN', '')}",
        f"TG_CHAT_ID={values.get('TG_CHAT_ID', '')}",
        f"BOT_MODE={values.get('BOT_MODE', 'paper')}",
        "",
    ]
    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")


def read_config() -> dict:
    src = CONFIG_FILE if CONFIG_FILE.exists() else EXAMPLE_FILE
    return yaml.safe_load(src.read_text(encoding="utf-8")) or {}


def write_config(cfg: dict) -> None:
    header = ("# Создано мастером настройки (2-settings.bat).\n"
              "# Логин, пароль и Telegram хранятся в файле .env.\n"
              "# Все параметры описаны в config.example.yaml.\n")
    CONFIG_FILE.write_text(header + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")


def apply_settings(cfg: dict, *, symbols: str, risk: float, orders: bool, allow_real: bool,
                   show_browser: bool, telegram_on: bool, hourly: bool = True) -> dict:
    """orders — отправлять ордера в терминал (иначе бумажный режим);
    allow_real — разрешить это на НЕ демо-счёте (реальные деньги)."""
    cfg = dict(cfg)
    cfg["symbols"] = [s.strip().upper() for s in symbols.replace(";", ",").split(",") if s.strip()]
    cfg.setdefault("risk", {})["risk_per_trade_pct"] = float(risk)
    cfg.setdefault("account", {})["allow_real"] = bool(allow_real)
    cfg["account"]["login"] = None
    cfg["account"]["password"] = None
    web = cfg.setdefault("web", {})
    web["mode"] = "live" if orders else "paper"
    web["headless"] = not show_browser
    web.setdefault("url", "https://trade.alfaforex.ru/terminal")
    tg = cfg.setdefault("telegram", {})
    tg["enabled"] = bool(telegram_on)
    tg["hourly_report"] = bool(hourly)
    tg["token"] = None
    tg["chat_id"] = None
    return cfg


# ---------------------------------------------------------------- Telegram
def tg_bot_name(token: str) -> str | None:
    r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
    return r["result"]["username"] if r.get("ok") else None


def tg_find_chat(token: str, wait_s: int = 90, on_tick=None) -> tuple[str, str] | None:
    """Ждёт, пока пользователь напишет боту, и возвращает (chat_id, имя)."""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15).json()
        for u in reversed(r.get("result", [])):
            chat = (u.get("message") or {}).get("chat") or {}
            if chat.get("type") == "private":
                return str(chat["id"]), chat.get("first_name") or chat.get("username") or ""
        if on_tick:
            on_tick(int(deadline - time.time()))
        time.sleep(3)
    return None


def tg_send(token: str, chat_id: str, text: str) -> bool:
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=15).json()
    return bool(r.get("ok"))


# ---------------------------------------------------------------- автозапуск Windows
def startup_file() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if sys.platform != "win32" or not appdata:
        return None
    return Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup/AlfaBot.bat"


def set_autostart(enabled: bool) -> None:
    f = startup_file()
    if not f:
        return
    if enabled:
        f.write_text(f'@echo off\r\ncd /d "{ROOT}"\r\nstart "AlfaBot" /min cmd /c "{ROOT / "3-start.bat"}"\r\n',
                     encoding="utf-8")
    elif f.exists():
        f.unlink()


def python_exe() -> str:
    venv = ROOT / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")
    return str(venv) if venv.exists() else sys.executable


# ---------------------------------------------------------------- окно
def run_gui() -> None:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    env = read_env()
    cfg = read_config()
    root = tk.Tk()
    root.title("AlfaBot — настройка")
    root.geometry("760x840")
    pad = {"padx": 10, "pady": 4}

    frm = ttk.Frame(root)
    frm.pack(fill="both", expand=True, padx=8, pady=8)
    frm.columnconfigure(1, weight=1)
    row = 0

    def section(text):
        nonlocal row
        ttk.Label(frm, text=text, font=("Segoe UI", 11, "bold")).grid(row=row, column=0, columnspan=3,
                                                                       sticky="w", padx=10, pady=(12, 2))
        row += 1

    def hint(text):
        nonlocal row
        ttk.Label(frm, text=text, foreground="#666", wraplength=660, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", padx=10)
        row += 1

    def field(label, value="", show=None):
        nonlocal row
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", **pad)
        var = tk.StringVar(value=value)
        ttk.Entry(frm, textvariable=var, show=show or "").grid(row=row, column=1, columnspan=2, sticky="ew", **pad)
        row += 1
        return var

    # 1. Счёт
    section("1. Счёт Альфа-Форекс")
    hint("Те же логин и пароль, которыми вы входите на trade.alfaforex.ru/terminal.")
    v_login = field("Логин (номер счёта):", env.get("ALFA_LOGIN", ""))
    v_pass = field("Пароль:", env.get("ALFA_PASSWORD", ""), show="•")

    # 2. Telegram
    section("2. Telegram")
    hint("Токен бота от @BotFather. Затем нажмите «Найти мой chat_id» и напишите боту любое сообщение.")
    v_token = field("Токен бота:", env.get("TG_TOKEN", ""))
    v_chat = field("Мой chat_id:", env.get("TG_CHAT_ID", ""))
    tg_status = ttk.Label(frm, text="", foreground="#0a6")
    tg_status.grid(row=row, column=1, sticky="w", padx=10)
    btn_find = ttk.Button(frm, text="Найти мой chat_id")
    btn_find.grid(row=row, column=0, sticky="w", **pad)
    row += 1

    def find_chat():
        token = v_token.get().strip()
        if not token:
            messagebox.showwarning("Telegram", "Сначала вставьте токен бота.")
            return
        try:
            name = tg_bot_name(token)
        except Exception as e:
            messagebox.showerror("Telegram", f"Нет связи с Telegram: {e}")
            return
        if not name:
            messagebox.showerror("Telegram", "Токен не подходит. Скопируйте его из @BotFather целиком.")
            return
        webbrowser.open(f"https://t.me/{name}")
        btn_find.state(["disabled"])
        tg_status.config(text=f"Откройте @{name} в Telegram и нажмите «Старт» или напишите любое слово…",
                         foreground="#a60")

        def work():
            res = tg_find_chat(token, 120, lambda s: root.after(0, lambda: tg_status.config(
                text=f"Жду ваше сообщение боту @{name}… ({s} с)")))

            def done():
                btn_find.state(["!disabled"])
                if res:
                    v_chat.set(res[0])
                    tg_send(token, res[0], "✅ AlfaBot подключён. Сюда будут приходить уведомления о сделках.")
                    tg_status.config(text=f"Найдено: {res[1]} (chat_id {res[0]}). Проверьте Telegram!",
                                     foreground="#0a6")
                else:
                    tg_status.config(text="Сообщение не пришло. Напишите боту и нажмите ещё раз.",
                                     foreground="#c00")
            root.after(0, done)
        threading.Thread(target=work, daemon=True).start()

    btn_find.config(command=find_chat)

    # 3. Торговля
    section("3. Торговля")
    v_symbols = field("Валютные пары:", ", ".join(cfg.get("symbols", ["EURUSD", "GBPUSD", "USDJPY"])))
    v_risk = field("Риск на сделку, % от счёта:", str((cfg.get("risk") or {}).get("risk_per_trade_pct", 1.0)))
    acc_cfg = cfg.get("account") or {}
    v_mode = tk.StringVar(value="live" if env.get("BOT_MODE") == "live" else "paper")
    ttk.Label(frm, text="Режим:").grid(row=row, column=0, sticky="w", **pad)
    ttk.Radiobutton(frm, text="Бумажный — сделки виртуальные, в терминал не отправляются",
                    variable=v_mode, value="paper").grid(row=row, column=1, columnspan=2, sticky="w", padx=10)
    row += 1
    ttk.Radiobutton(frm, text="Ордера на счёт — бот открывает сделки в терминале",
                    variable=v_mode, value="live").grid(row=row, column=1, columnspan=2, sticky="w", padx=10)
    row += 1
    v_real = tk.BooleanVar(value=bool(acc_cfg.get("allow_real")))
    ttk.Checkbutton(frm, text="Разрешить реальные деньги (нужно только если счёт НЕ демо)",
                    variable=v_real).grid(row=row, column=1, columnspan=2, sticky="w", padx=10)
    row += 1
    v_hourly = tk.BooleanVar(value=(cfg.get("telegram") or {}).get("hourly_report", True))
    ttk.Checkbutton(frm, text="Каждый час присылать в Telegram, что бот видит на рынке",
                    variable=v_hourly).grid(row=row, column=1, columnspan=2, sticky="w", padx=10)
    row += 1
    v_show = tk.BooleanVar(value=not (cfg.get("web") or {}).get("headless", True))
    ttk.Checkbutton(frm, text="Показывать окно браузера (видно, что делает бот)", variable=v_show).grid(
        row=row, column=1, columnspan=2, sticky="w", padx=10)
    row += 1
    v_auto = tk.BooleanVar(value=bool(startup_file() and startup_file().exists()))
    if sys.platform == "win32":
        ttk.Checkbutton(frm, text="Запускать бота автоматически при включении компьютера",
                        variable=v_auto).grid(row=row, column=1, columnspan=2, sticky="w", padx=10)
        row += 1

    # кнопки
    bar = ttk.Frame(frm)
    bar.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(14, 4), padx=10)
    row += 1
    out = scrolledtext.ScrolledText(frm, height=12, font=("Consolas", 9))
    out.grid(row=row, column=0, columnspan=3, sticky="nsew", padx=10, pady=4)
    frm.rowconfigure(row, weight=1)

    def log(text):
        out.insert("end", text + "\n")
        out.see("end")

    def save(quiet=False) -> bool:
        login = v_login.get().strip()
        if not login.isdigit():
            messagebox.showerror("Ошибка", "Логин — это номер счёта, только цифры.")
            return False
        if not v_pass.get():
            messagebox.showerror("Ошибка", "Впишите пароль от счёта.")
            return False
        try:
            risk = float(v_risk.get().replace(",", "."))
            assert 0 < risk <= 5
        except Exception:
            messagebox.showerror("Ошибка", "Риск — число от 0.1 до 5 (например 1).")
            return False
        orders = v_mode.get() == "live"
        allow_real = v_real.get()
        if allow_real and not bool(read_config().get("account", {}).get("allow_real")):
            if not messagebox.askyesno(
                    "Реальные деньги",
                    "Вы разрешаете боту торговать РЕАЛЬНЫМИ деньгами (если счёт не демо).\n\n"
                    "Бот будет сам открывать и закрывать сделки. Прибыль не гарантирована, возможны убытки.\n"
                    "На истории 2024–2026 текущая стратегия была убыточной.\n\nРазрешить?"):
                v_real.set(False)
                allow_real = False
        token, chat = v_token.get().strip(), v_chat.get().strip()
        write_env({"ALFA_LOGIN": login, "ALFA_PASSWORD": v_pass.get(), "TG_TOKEN": token,
                   "TG_CHAT_ID": chat, "BOT_MODE": "live" if orders else "paper"})
        write_config(apply_settings(read_config(), symbols=v_symbols.get(), risk=risk, orders=orders,
                                    allow_real=allow_real, show_browser=v_show.get(),
                                    telegram_on=bool(token and chat), hourly=v_hourly.get()))
        set_autostart(v_auto.get())
        mode = "бумажный" if not orders else ("ордера на счёт, реальные деньги РАЗРЕШЕНЫ" if allow_real
                                             else "ордера на счёт (только демо-счёт)")
        if not quiet:
            log(f"Сохранено: {mode}; Telegram {'включён' if token and chat else 'не настроен'}.")
        return True

    def run_check():
        if not save(quiet=True):
            return
        log("Проверяю вход в терминал (до 2 минут)…")

        def work():
            p = subprocess.run([python_exe(), "run_web.py", "--check"], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines = [l for l in (p.stdout + p.stderr).splitlines() if l.strip()]
            text = "\n".join(lines[-12:])
            ok = p.returncode == 0 and "Счёт" in p.stdout
            msg = text + ("\n✅ Вход работает. Нажмите «Запустить бота»." if ok
                          else "\n❌ Не получилось — см. текст выше.")
            if ok and "ДЕМО" in p.stdout:
                msg += ("\nℹ Ваш счёт ДЕМО (деньги виртуальные): можно выбрать «Ордера на счёт» — "
                        "сделки бота будут видны прямо в терминале.")
            elif ok and "РЕАЛЬНЫЙ" in p.stdout:
                msg += "\n⚠ Это РЕАЛЬНЫЙ счёт: оставьте бумажный режим, пока не убедитесь в результатах."
            root.after(0, lambda: log(msg))
        threading.Thread(target=work, daemon=True).start()

    def start_bot():
        if not save():
            return
        if sys.platform == "win32":
            subprocess.Popen(f'start "AlfaBot" "{ROOT / "3-start.bat"}"', shell=True, cwd=ROOT)
        else:
            subprocess.Popen([str(ROOT / "3-start.sh")], cwd=ROOT)
        log("Бот запущен в отдельном окне. Не закрывайте его, пока бот должен работать.")

    ttk.Button(bar, text="💾 Сохранить", command=save).pack(side="left", padx=4)
    ttk.Button(bar, text="🔍 Проверить вход", command=run_check).pack(side="left", padx=4)
    ttk.Button(bar, text="▶ Запустить бота", command=start_bot).pack(side="left", padx=4)
    log("Заполните поля сверху вниз, затем «Проверить вход» и «Запустить бота».")
    root.mainloop()


# ---------------------------------------------------------------- текстовый режим
def run_console() -> None:
    import getpass
    env, cfg = read_env(), read_config()

    def ask(q, default=""):
        a = input(f"{q}" + (f" [{default}]" if default else "") + ": ").strip()
        return a or default

    print("=== AlfaBot — настройка ===\n1. Счёт Альфа-Форекс (те же данные, что для trade.alfaforex.ru)")
    login = ask("Логин (номер счёта)", env.get("ALFA_LOGIN", ""))
    pwd = getpass.getpass("Пароль (не отображается): ") or env.get("ALFA_PASSWORD", "")
    print("\n2. Telegram (Enter — пропустить)")
    token = ask("Токен бота от @BotFather", env.get("TG_TOKEN", ""))
    chat = env.get("TG_CHAT_ID", "")
    if token and requests:
        name = tg_bot_name(token)
        if name:
            print(f"Откройте https://t.me/{name} и напишите боту любое слово… (жду до 2 минут)")
            res = tg_find_chat(token, 120)
            if res:
                chat = res[0]
                tg_send(token, chat, "✅ AlfaBot подключён.")
                print(f"chat_id найден: {chat}")
        else:
            print("Токен не подошёл — Telegram пропущен.")
            token = ""
    print("\n3. Торговля")
    symbols = ask("Валютные пары", ", ".join(cfg.get("symbols", ["EURUSD", "GBPUSD", "USDJPY"])))
    risk = float(ask("Риск на сделку, %", "1").replace(",", "."))
    orders = ask("Режим: 1 — бумажный, 2 — ордера на счёт", "1") == "2"
    allow_real = False
    if orders and ask("Разрешить РЕАЛЬНЫЕ деньги (если счёт не демо)? Напишите ДА", "") == "ДА":
        allow_real = True
    write_env({"ALFA_LOGIN": login, "ALFA_PASSWORD": pwd, "TG_TOKEN": token, "TG_CHAT_ID": chat,
               "BOT_MODE": "live" if orders else "paper"})
    write_config(apply_settings(cfg, symbols=symbols, risk=risk, orders=orders, allow_real=allow_real,
                                show_browser=False, telegram_on=bool(token and chat)))
    print(f"\nСохранено в {ENV_FILE.name} и {CONFIG_FILE.name}. Проверка: python run_web.py --check")


if __name__ == "__main__":
    if "--console" in sys.argv:
        run_console()
    else:
        try:
            run_gui()
        except Exception as e:  # нет графики (сервер, нет tkinter)
            print(f"Окно недоступно ({e}), перехожу в текстовый режим.\n")
            run_console()
