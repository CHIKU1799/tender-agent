@echo off
REM === Tender Agent - One-command Windows setup =============================
setlocal enabledelayedexpansion

echo.
echo   Tender Agent - Setup
echo   -----------------------------------------------------------------

REM 1. Python version check
where python >nul 2>&1
if errorlevel 1 (
    echo   [X] Python not found. Please install Python 3.9+ from https://www.python.org/downloads/
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   [OK] Python !PYVER!

REM 2. Create virtualenv if not active
if "%VIRTUAL_ENV%"=="" (
    if not exist ".venv" (
        echo   -^> Creating virtualenv at .venv ...
        python -m venv .venv
        if errorlevel 1 (
            echo   [X] Failed to create virtualenv
            exit /b 1
        )
    )
    call .venv\Scripts\activate.bat
    echo   [OK] Virtualenv activated
)

REM 3. Install Python deps
echo   -^> Installing Python dependencies ...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo   [X] Failed to install dependencies
    exit /b 1
)

REM 4. Install Playwright browsers
echo   -^> Installing Playwright Chromium ...
playwright install chromium
if errorlevel 1 (
    echo   [!] Playwright install had warnings, continuing ...
)

REM 5. Create .env if missing
if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo.
        echo   [!] .env created from .env.example
        echo       Open .env and add your OpenAI API key:
        echo       OPENAI_API_KEY=sk-proj-...
        echo.
    )
) else (
    echo   [OK] .env already exists
)

REM 6. Create runtime dirs
if not exist "output" mkdir output
if not exist "logs" mkdir logs
if not exist "screenshots" mkdir screenshots

echo.
echo   [OK] Setup complete!
echo.
echo   To start the dashboard:
echo     .venv\Scripts\activate.bat
echo     python dashboard.py
echo     -^> http://localhost:5002
echo.
echo   To run all portals from the terminal:
echo     python run_all.py
echo.

endlocal
