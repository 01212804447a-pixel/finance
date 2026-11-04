@echo off
rem Starts the browser bot and restarts it if it crashes.
cd /d "%~dp0.."
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if not "%%a"=="" set "%%a=%%b"
:loop
".venv\Scripts\python.exe" run_web.py --config config.yaml
echo Bot stopped, restarting in 30 seconds...
timeout /t 30 /nobreak >nul
goto loop
