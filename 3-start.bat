@echo off
rem AlfaBot: step 3 - start the bot. Keep this window open while the bot works.
chcp 65001 >nul
title AlfaBot
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist ".env" (
  echo Settings not found. Run 2-settings.bat first.
  pause
  exit /b 1
)
:loop
".venv\Scripts\python.exe" run_web.py --config config.yaml
echo Bot stopped, restarting in 30 seconds... (close this window to stop)
timeout /t 30 /nobreak >nul
goto loop
