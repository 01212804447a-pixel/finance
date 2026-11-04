#!/usr/bin/env bash
# Запуск браузерного бота с автоперезапуском (Linux/macOS без Docker).
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a
while true; do
  .venv/bin/python run_web.py --config config.yaml
  echo "Bot stopped, restarting in 30 seconds..."
  sleep 30
done
