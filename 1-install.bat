@echo off
rem AlfaBot: step 1 - install (Python packages + browser). Run once.
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python not found. Trying to install it with winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo Please install Python 3.12 from https://www.python.org/downloads/
    echo IMPORTANT: tick "Add python.exe to PATH" in the installer.
    start https://www.python.org/downloads/
    pause
    exit /b 1
  )
  echo.
  echo Python installed. Close this window and run 1-install.bat again.
  pause
  exit /b 0
)
echo [1/3] Creating environment...
%PY% -m venv .venv || goto fail
echo [2/3] Installing packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto fail
echo [3/3] Installing browser (about 150 MB)...
".venv\Scripts\python.exe" -m playwright install chromium || goto fail
echo.
echo Done! Opening settings window...
start "" ".venv\Scripts\pythonw.exe" setup_wizard.py
exit /b 0
:fail
echo.
echo ERROR: installation failed, see the messages above.
pause
exit /b 1
