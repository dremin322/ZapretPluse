@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title Zapret+ - Build

set "PY="
set "PYTHON_FALLBACK_VERSION=3.11.9"
set "PYTHON_FALLBACK_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
set "PYTHON_INSTALLER=%TEMP%\ZapretPlus-python-3.11.9-amd64.exe"

echo.
echo ============================================================
echo   Zapret+ build
echo ============================================================
echo.
echo This file prepares everything automatically.
echo If Python 3.11+ is missing, Zapret+ will install it first.
echo.

call :find_python
if defined PY goto :python_ready

echo Python 3.11+ was not found.
echo.
echo Trying to install Python automatically...
echo.

where winget >nul 2>nul
if errorlevel 1 goto :python_fallback

echo [1/2] Installing Python 3.11 with Windows Package Manager...
winget install --exact --id Python.Python.3.11 --source winget --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo.
    echo WinGet could not install Python. Trying the official Python installer...
    goto :python_fallback
)

call :find_python
if defined PY goto :python_installed

:python_fallback
echo [1/2] Downloading official Python %PYTHON_FALLBACK_VERSION% installer...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing '%PYTHON_FALLBACK_URL%' -OutFile '%PYTHON_INSTALLER%'"
if errorlevel 1 (
    echo.
    echo ERROR: Could not download Python from python.org.
    echo Check your Internet connection and run this file again.
    goto :fail
)

if not exist "%PYTHON_INSTALLER%" (
    echo ERROR: Python installer was not downloaded.
    goto :fail
)

echo Installing Python %PYTHON_FALLBACK_VERSION%...
"%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_pip=1 Include_test=0
if errorlevel 1 (
    echo.
    echo ERROR: Python installation failed.
    goto :fail
)

del /q "%PYTHON_INSTALLER%" >nul 2>nul
call :find_python
if not defined PY (
    echo.
    echo ERROR: Python was installed but Zapret+ could not locate python.exe.
    echo Restart Windows and run build_portable.bat again.
    goto :fail
)

:python_installed
echo.
echo Python installed successfully.

:python_ready
echo.
echo Python:
"%PY%" --version
echo.

echo [2/2] Preparing Zapret+...
echo.

echo [1/5] Checking pip...
"%PY%" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo pip is missing. Installing pip...
    "%PY%" -m ensurepip --default-pip
    if errorlevel 1 (
        echo ERROR: Could not install pip.
        goto :fail
    )
) else (
    echo pip is ready.
)

echo.
echo [2/5] Updating pip...
"%PY%" -m pip install --upgrade pip --disable-pip-version-check --no-input
if errorlevel 1 (
    echo WARNING: pip could not be updated. Continuing with the installed version...
)

echo.
echo [3/5] Installing Zapret+ dependencies...
"%PY%" -m pip install -r requirements.txt --disable-pip-version-check --no-input
if errorlevel 1 (
    echo ERROR: Could not install Zapret+ dependencies.
    goto :fail
)

echo.
echo [4/5] Checking PyInstaller...
"%PY%" -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo PyInstaller is missing. Installing it...
    "%PY%" -m pip install pyinstaller --disable-pip-version-check --no-input
    if errorlevel 1 (
        echo ERROR: Could not install PyInstaller.
        goto :fail
    )
) else (
    for /f "delims=" %%V in ('"%PY%" -m PyInstaller --version 2^>nul') do echo PyInstaller %%V is ready.
)

echo.
echo [5/5] Building Zapret+...
echo.

taskkill /IM ZapretPlus.exe /T /F >nul 2>nul

if exist build rmdir /s /q build
if exist "_portable_build" rmdir /s /q "_portable_build"
if exist "_launcher_build" rmdir /s /q "_launcher_build"
if exist "build_launcher" rmdir /s /q "build_launcher"
if exist "ZapretPlus" rmdir /s /q "ZapretPlus"
if exist "ZapretPlus.exe" del /f /q "ZapretPlus.exe"

echo Building main application. This can take a few minutes...
"%PY%" -m PyInstaller --noconfirm --clean --distpath "_portable_build" --workpath "build" "ZapretPlus.spec"
if errorlevel 1 goto :fail

if not exist "_portable_build\ZapretPlus\ZapretPlus.exe" (
    echo ERROR: Main ZapretPlus.exe was not created.
    goto :fail
)

move "_portable_build\ZapretPlus" "ZapretPlus" >nul
rmdir /s /q "_portable_build" >nul 2>nul

echo Main application built successfully.
echo.
echo Building root launcher...
"%PY%" -m PyInstaller --noconfirm --clean --distpath "_launcher_build" --workpath "build_launcher" "ZapretPlusLauncher.spec"
if errorlevel 1 goto :fail

if not exist "_launcher_build\ZapretPlus.exe" (
    echo ERROR: Root ZapretPlus.exe launcher was not created.
    goto :fail
)

copy /y "_launcher_build\ZapretPlus.exe" "ZapretPlus.exe" >nul
rmdir /s /q "_launcher_build" >nul 2>nul
rmdir /s /q "build_launcher" >nul 2>nul

if not exist "ZapretPlus\assets\icons\home.svg" (
    echo ERROR: UI assets are missing from the build.
    goto :fail
)
if not exist "ZapretPlus\runtime\zapret\bin\winws.exe" (
    echo ERROR: winws.exe is missing from the build.
    goto :fail
)
if not exist "ZapretPlus.exe" (
    echo ERROR: Root launcher is missing.
    goto :fail
)

echo.
echo ============================================================
echo   ZAPRET+ IS READY
echo ============================================================
echo.
echo To start Zapret+, use:
echo.
echo   %CD%\ZapretPlus.exe
echo.
echo Application files are stored here:
echo.
echo   %CD%\ZapretPlus\
echo.
echo You can create a desktop shortcut for the ROOT ZapretPlus.exe.
echo Do not move the inner application files separately.
echo.
echo Opening the finished file...
explorer.exe /select,"%CD%\ZapretPlus.exe" >nul 2>nul
echo.
pause
exit /b 0


:find_python
set "PY="

where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%P in ('py -3.11 -c "import sys; print(sys.executable)" 2^>nul') do (
            if exist "%%P" set "PY=%%P"
        )
    )
)
if defined PY goto :eof

where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do (
            if exist "%%P" set "PY=%%P"
        )
    )
)
if defined PY goto :eof

for %%V in (313 312 311) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
        goto :eof
    )
)

for %%V in (313 312 311) do (
    if exist "%ProgramFiles%\Python%%V\python.exe" (
        set "PY=%ProgramFiles%\Python%%V\python.exe"
        goto :eof
    )
)

goto :eof


:fail
echo.
echo ============================================================
echo   BUILD FAILED
echo ============================================================
echo.
echo Review the error above and run build_portable.bat again.
echo.
pause
exit /b 1
