#!/usr/bin/env bash
# Установка браузерного бота на Linux/macOS (без Docker).
set -e
cd "$(dirname "$0")
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
if [ "$(uname)" = "Linux" ]; then
  .venv/bin/python -m playwright install --with-deps chromium
else
  .venv/bin/python -m playwright install chromium
fi
[ -f config.yaml ] || cp config.example.yaml config.yaml
echo "Готово. Открываю настройки..."
.venv/bin/python setup_wizard.py
