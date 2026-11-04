@echo off
rem AlfaBot: step 2 - settings window (login, password, Telegram, mode).
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run 1-install.bat first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" setup_wizard.py
