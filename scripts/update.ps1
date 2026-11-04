# Обновление AlfaBot до последней версии. Настройки (.env, config.yaml) и данные (data, logs) сохраняются.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$url  = "https://github.com/01212804447a-pixel/finance/archive/refs/heads/claude/zen-bohr-17bdcp.zip"
$tmp  = Join-Path $env:TEMP "alfabot_update"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory -Path $tmp | Out-Null
$zip = Join-Path $tmp "bot.zip"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Write-Host "Скачиваю новую версию..."
Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
Expand-Archive -Path $zip -DestinationPath $tmp
$src = Get-ChildItem -Path $tmp -Directory | Select-Object -First 1
robocopy $src.FullName $root /E /XF .env config.yaml /XD data logs .venv /NFL /NDL /NJH /NJS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Не удалось скопировать файлы (код $LASTEXITCODE)" }
Write-Host "Обновляю пакеты..."
& (Join-Path $root ".venv\Scripts\python.exe") -m pip install -r (Join-Path $root "requirements.txt") --quiet
Remove-Item $tmp -Recurse -Force
Write-Host "Готово! Запустите бота снова: 3-start.bat" -ForegroundColor Green
