# Установка AlfaBot на Windows (ПК или Windows-VPS).
# Запуск: PowerShell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python не найден. Установите Python 3.11+ с python.org (галочка 'Add to PATH')." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements-mt5.txt
& .\.venv\Scripts\python.exe -m playwright install chromium
if (-not (Test-Path ".env")) { Copy-Item .env.example .env }

if (-not (Test-Path "config.yaml")) {
    Copy-Item config.example.yaml config.yaml
    Write-Host "Создан config.yaml — откройте и проверьте настройки." -ForegroundColor Yellow
}
Write-Host "Готово. Впишите логин/пароль в .env и проверьте: .\.venv\Scripts\python.exe run_web.py --check" -ForegroundColor Green
