@echo off
rem Starts the bot and restarts it if it crashes.
cd /d "%~dp0.."
:loop
".venv\Scripts\python.exe" run_bot.py --config config.yaml
echo Bot stopped, restarting in 30 seconds...
timeout /t 30 /nobreak >nul
goto loop
