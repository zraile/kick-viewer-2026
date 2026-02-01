@echo off
title Kick Viewer Bot - Auto Launcher
setlocal

:: ANSI Color Codes
set "GREEN=[92m"
set "RED=[31m"
set "YELLOW=[33m"
set "BLUE=[94m"
set "RESET=[0m"

echo %BLUE%====================================================%RESET%
echo        KICK VIEWER BOT 
echo %BLUE%====================================================%RESET%

:: 1. Check Python
echo [*] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo %RED%[!] ERROR: Python not found!%RESET%
    echo Please install Python and make sure 'Add to PATH' is checked.
    pause
    exit /b
)

:: 2. Install/Update Requirements
echo [*] Checking and updating dependencies...
python -m pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo %RED%[!] ERROR: Failed to install dependencies!%RESET%
    echo Please check your internet connection.
    pause
    exit /b
)

:: 3. Check Docker
echo [*] Checking Docker installation...
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo %RED%[!] ERROR: Docker not found!%RESET%
    echo Please install Docker Desktop to use this bot.
    pause
    exit /b
)

:: 4. Final Launch
echo %GREEN%[+] Everything is ready! Starting the bot...%RESET%
echo.
python kick-multi6.py

if %errorlevel% neq 0 (
    echo.
    echo %RED%[!] Bot stopped due to an error.%RESET%
    pause
)

endlocal
