@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
  set "PYTHON_EXE=python"
)

"%PYTHON_EXE%" scripts\test_terminal.py
if errorlevel 1 (
  echo.
  echo testterminal failed. Press any key to close.
  pause >nul
)
