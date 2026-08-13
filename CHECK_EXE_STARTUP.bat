@echo off
setlocal
cd /d "%~dp0"
title Zapret+ - Startup Diagnostics

echo.
echo Starting portable Zapret+...
echo.

if not exist "ZapretPlus\ZapretPlus.exe" (
  echo ERROR: ZapretPlus\ZapretPlus.exe was not found.
  pause
  exit /b 1
)

start "" "ZapretPlus\ZapretPlus.exe"
timeout /t 4 /nobreak >nul

echo.
echo Startup log:
echo ------------------------------------------------------------
if exist "%APPDATA%\ZapretPlus\startup.log" (
  type "%APPDATA%\ZapretPlus\startup.log"
) else (
  echo No startup.log found.
)
echo ------------------------------------------------------------
echo.
pause
