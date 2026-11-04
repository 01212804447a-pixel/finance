@echo off
rem AlfaBot: update to the latest version (settings and data are kept).
rem Close the bot window (3-start.bat) before updating.
cd /d "%~dp0"
PowerShell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update.ps1"
pause
