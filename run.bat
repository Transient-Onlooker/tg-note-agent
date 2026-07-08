@echo off
setlocal

cd /d "%~dp0"
set "NGROK_DOMAIN="
set "NGROK_ARGS=http http://127.0.0.1:8000"
set "NGROK_EXE="
set "PYTHON_EXE=python"
set "WT_EXE="
set "PROJECT_DIR=%CD%"

if not exist ".env" (
    echo .env file not found.
    echo Create .env from .env.example and fill the required values first.
    pause
    exit /b 1
)

for /f "delims=" %%I in ('where ngrok 2^>nul') do (
    set "NGROK_EXE=%%I"
    goto :ngrok_found
)

if not defined NGROK_EXE (
    set "NGROK_EXE=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.Ngrok_Microsoft.Winget.Source_8wekyb3d8bbwe\ngrok.exe"
)

:ngrok_found
if not exist "%NGROK_EXE%" (
    echo ngrok was not found.
    echo Open a new PowerShell window or install ngrok first.
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
)

if defined NGROK_DOMAIN (
    set "NGROK_ARGS=http --domain=%NGROK_DOMAIN% http://127.0.0.1:8000"
)

for /f "delims=" %%I in ('where wt 2^>nul') do (
    set "WT_EXE=%%I"
    goto :wt_found
)

:wt_found

echo Starting tg-note-agent server and ngrok tunnel...
echo Health check: http://127.0.0.1:8000/healthz
if defined NGROK_DOMAIN (
    echo Webhook URL: https://%NGROK_DOMAIN%/webhook/telegram
) else (
    echo Webhook URL: will be detected from ngrok and registered with Telegram.
)
echo.

if defined WT_EXE (
    echo Starting Windows Terminal tabs...
    "%WT_EXE%" -w 0 new-tab --title "tg-note-agent server" --startingDirectory "%PROJECT_DIR%" cmd /k call "%PYTHON_EXE%" -m uvicorn app.main:app --reload --env-file .env ; new-tab --title "tg-note-agent ngrok" --startingDirectory "%PROJECT_DIR%" cmd /k call "%NGROK_EXE%" %NGROK_ARGS%
) else (
    echo Windows Terminal was not found. Starting separate cmd windows...
    start "tg-note-agent server" /D "%~dp0" cmd /k call "%PYTHON_EXE%" -m uvicorn app.main:app --reload --env-file .env
    timeout /t 2 /nobreak >nul
    start "tg-note-agent ngrok" /D "%~dp0" cmd /k call "%NGROK_EXE%" %NGROK_ARGS%
)
timeout /t 2 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Start-Sleep -Seconds 3; $envPath=Join-Path (Get-Location) '.env'; $token=(Get-Content $envPath | Where-Object { $_ -match '^TELEGRAM_BOT_TOKEN=' } | Select-Object -First 1) -replace '^TELEGRAM_BOT_TOKEN=', ''; if (-not $token) { throw 'TELEGRAM_BOT_TOKEN is empty in .env' }; $publicUrl=$null; for ($i=0; $i -lt 30; $i++) { try { $tunnels=Invoke-RestMethod -UseBasicParsing 'http://127.0.0.1:4040/api/tunnels'; $publicUrl=($tunnels.tunnels | Where-Object { $_.proto -eq 'https' } | Select-Object -First 1 -ExpandProperty public_url); if ($publicUrl) { break } } catch {}; Start-Sleep -Seconds 1 }; if (-not $publicUrl) { throw 'ngrok public URL was not found at http://127.0.0.1:4040/api/tunnels' }; $webhookUrl=$publicUrl.TrimEnd('/') + '/webhook/telegram'; $result=Invoke-RestMethod -Method Post -Uri ('https://api.telegram.org/bot' + $token + '/setWebhook') -Body @{ url = $webhookUrl }; Write-Host ('Registered Telegram webhook: ' + $webhookUrl); $result | ConvertTo-Json -Depth 4"
echo.
if defined WT_EXE (
    echo Server and ngrok are running in Windows Terminal tabs.
) else (
    echo Server and ngrok are running in separate windows.
)
exit /b 0
