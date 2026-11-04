#!/usr/bin/env bash
# AlfaBot: запуск бота (macOS/Linux). Не закрывайте окно, пока бот должен работать.
cd "$(dirname "$0")"
while true; do
  .venv/bin/python run_web.py --config config.yaml
  echo "Бот остановился, перезапуск через 30 секунд (Ctrl+C — выйти)"
  sleep 30
done
