# Регистрирует задачу Планировщика: бот стартует при входе в Windows
# и автоматически перезапускается при сбое.
# Запуск от администратора: PowerShell -ExecutionPolicy Bypass -File scripts\register_autostart.ps1
param([switch]$Mt5)   # -Mt5: запускать бота для программы MT5 вместо браузерного
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bat  = Join-Path $root ($(if ($Mt5) { "scripts\start_bot.bat" } else { "3-start.bat" }))

$action   = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -ExecutionTimeLimit (New-TimeSpan -Days 3650) -StartWhenAvailable
Register-ScheduledTask -TaskName "AlfaBot" -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "Задача 'AlfaBot' создана. Удалить: Unregister-ScheduledTask -TaskName AlfaBot" -ForegroundColor Green
