@echo off
setlocal

cd /d "%~dp0"
set "NGROK_DOMAIN=epexegetic-unruffed-taisha.ngrok-free.dev"
set "NGROK_EXE="
set "WT_EXE="

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

echo Starting tg-note-agent server and ngrok tunnel...
echo Health check: http://127.0.0.1:8000/healthz
echo Webhook URL: https://%NGROK_DOMAIN%/webhook/telegram
echo.

for /f "delims=" %%I in ('where wt 2^>nul') do (
    set "WT_EXE=%%I"
    goto :wt_found
)

:wt_found
if defined WT_EXE (
    start "" "%WT_EXE%" -w 0 new-tab --title "tg-note-agent server" cmd /k "cd /d ""%~dp0"" && python -m uvicorn app.main:app --reload --env-file .env" ; new-tab --title "tg-note-agent ngrok" cmd /k """%NGROK_EXE%"" http --domain=%NGROK_DOMAIN% 8000"
    echo Started in Windows Terminal tabs.
    echo If ngrok says the endpoint is already online, close the old ngrok tab first.
    exit /b 0
)

start "tg-note-agent server" cmd /k "cd /d ""%~dp0"" && python -m uvicorn app.main:app --reload --env-file .env"
timeout /t 2 /nobreak >nul
start "tg-note-agent ngrok" cmd /k ""%NGROK_EXE%" http --domain=%NGROK_DOMAIN% 8000"

echo Started in two separate windows.
echo Keep both windows open while testing Telegram.
exit /b 0
