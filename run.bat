@echo off
REM === Tender Agent - Run launcher (Windows) ================================
REM Usage:
REM   run.bat                 - launch dashboard (default)
REM   run.bat dashboard       - launch dashboard at http://localhost:5002
REM   run.bat all             - run all portals once (run_all.py)
REM   run.bat cli             - interactive CLI (main.py)
REM   run.bat cppp            - scrape CPPP full
REM   run.bat gujarat         - scrape Gujarat portals
REM   run.bat karnataka       - scrape Karnataka portals
REM   run.bat new             - scrape new portals (BSNL/NHPC/AP/TS/WB/Bihar/CG)
REM   run.bat refresh         - full refresh across all portals

setlocal

cd /d "%~dp0"

REM Activate venv if present and not already active
if "%VIRTUAL_ENV%"=="" (
    if exist ".venv\Scripts\activate.bat" (
        call .venv\Scripts\activate.bat
    )
)

set "CMD=%~1"
if "%CMD%"=="" set "CMD=dashboard"

if /i "%CMD%"=="dashboard" goto dashboard
if /i "%CMD%"=="dash"      goto dashboard
if /i "%CMD%"=="ui"        goto dashboard
if /i "%CMD%"=="all"       goto all
if /i "%CMD%"=="cli"       goto cli
if /i "%CMD%"=="main"      goto cli
if /i "%CMD%"=="cppp"      goto cppp
if /i "%CMD%"=="gujarat"   goto gujarat
if /i "%CMD%"=="guj"       goto gujarat
if /i "%CMD%"=="karnataka" goto karnataka
if /i "%CMD%"=="kar"       goto karnataka
if /i "%CMD%"=="kppp"      goto karnataka
if /i "%CMD%"=="new"       goto new
if /i "%CMD%"=="refresh"   goto refresh
if /i "%CMD%"=="full"      goto refresh
if /i "%CMD%"=="help"      goto help
if /i "%CMD%"=="-h"        goto help
if /i "%CMD%"=="--help"    goto help

echo [X] Unknown command: %CMD%
echo Run 'run.bat help' for usage.
exit /b 1

:dashboard
echo -^> Launching dashboard at http://localhost:5002
python dashboard.py
goto end

:all
echo -^> Running all portals (run_all.py)
python run_all.py
goto end

:cli
echo -^> Launching interactive CLI (main.py)
python main.py
goto end

:cppp
echo -^> Scraping CPPP (full)
python scrape_cppp_full.py
goto end

:gujarat
echo -^> Scraping Gujarat portals
python scrape_gujarat.py
goto end

:karnataka
echo -^> Scraping Karnataka portals
python scrape_karnataka.py
goto end

:new
echo -^> Scraping new portals (BSNL/NHPC/AP/TS/WB/Bihar/CG)
python scrape_new_portals.py
goto end

:refresh
echo -^> Full refresh across all portals
python scrape_all.py
goto end

:help
echo.
echo Tender Agent - Run launcher
echo ----------------------------
echo   run.bat                 - launch dashboard (default)
echo   run.bat dashboard       - launch dashboard at http://localhost:5002
echo   run.bat all             - run all portals once (run_all.py)
echo   run.bat cli             - interactive CLI (main.py)
echo   run.bat cppp            - scrape CPPP full
echo   run.bat gujarat         - scrape Gujarat portals
echo   run.bat karnataka       - scrape Karnataka portals
echo   run.bat new             - scrape new portals
echo   run.bat refresh         - full refresh across all portals
echo.
goto end

:end
endlocal
