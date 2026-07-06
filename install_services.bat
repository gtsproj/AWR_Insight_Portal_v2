@echo off
:: ============================================================
:: install_services.bat
:: Registers AWR Insight Portal components as Windows Services
:: using NSSM (Non-Sucking Service Manager).
::
:: REQUIREMENTS:
::   1. Run this script as Administrator
::   2. NSSM must be downloaded and placed in tools\nssm.exe
::      Download: https://nssm.cc/download
::   3. Python must be in PATH
::
:: SERVICES REGISTERED:
::   AWRPortal         - FastAPI portal (uvicorn on port 8000)
::   AWRWatcher        - AWR file watcher (monitors awr_reports\)
::   SARWatcher        - SAR file watcher (monitors sar_drop\)
::   AWRQueueProcessor - Queue processor (AWR + SAR, 4 parallel workers)
::
:: USAGE:
::   install_services.bat          (install all services)
::   install_services.bat remove   (remove all services)
:: ============================================================

setlocal EnableDelayedExpansion

:: ── Configuration ────────────────────────────────────────────────────
set PROJECT_DIR=C:\AWR_Insight_Portal_v2
rem Change SERVICE_ACCOUNT if your Windows username is different from Admin
set SERVICE_ACCOUNT=.\Admin
rem Leave SERVICE_PASSWORD blank — set it manually in services.msc after install
rem Right-click service → Properties → Log On → enter your Windows password
set SERVICE_PASSWORD=
set PYTHON=C:\Program Files\Python313\python.exe
set NSSM=%PROJECT_DIR%\tools\nssm.exe
set LOG_DIR=%PROJECT_DIR%\logs\services

:: Verify NSSM exists
if not exist "%NSSM%" (
    echo.
    echo ERROR: NSSM not found at %NSSM%
    echo.
    echo Please download NSSM from https://nssm.cc/download
    echo and place nssm.exe in %PROJECT_DIR%\tools\
    echo.
    pause
    exit /b 1
)

:: Verify Python exists
"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH
    pause
    exit /b 1
)

:: Get Python executable full path

:: Create log directory
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: ── Remove mode ──────────────────────────────────────────────────────
if /i "%1"=="remove" (
    echo Removing AWR Insight Portal services...
    call :remove_service AWRPortal
    call :remove_service AWRWatcher
    call :remove_service SARWatcher
    call :remove_service AWRQueueProcessor
    call :remove_service Grafana
    echo.
    echo All services removed.
    pause
    exit /b 0
)

:: ── Install mode ─────────────────────────────────────────────────────
echo.
echo ============================================================
echo  AWR Insight Portal — Windows Service Installer
echo ============================================================
echo  Project dir : %PROJECT_DIR%
echo  Python      : %PYTHON%
echo  NSSM        : %NSSM%
echo  Log dir     : %LOG_DIR%
echo ============================================================
echo.

:: ── 1. AWR Portal (uvicorn) ──────────────────────────────────────────
echo [1/4] Installing AWRPortal service...
call :remove_service AWRPortal

"%NSSM%" install AWRPortal "%PYTHON%"
"%NSSM%" set AWRPortal AppParameters "-m uvicorn portal.app:app --host 0.0.0.0 --port 8000"
"%NSSM%" set AWRPortal AppDirectory  "%PROJECT_DIR%"
"%NSSM%" set AWRPortal DisplayName   "AWR Insight Portal"
"%NSSM%" set AWRPortal Description   "AWR Insight Portal v2 - Oracle Performance Analysis"
"%NSSM%" set AWRPortal Start         SERVICE_AUTO_START
"%NSSM%" set AWRPortal AppStdout     "%LOG_DIR%\portal_stdout.log"
"%NSSM%" set AWRPortal AppStderr     "%LOG_DIR%\portal_stderr.log"
"%NSSM%" set AWRPortal AppRotateFiles 1
"%NSSM%" set AWRPortal AppRotateBytes 10485760
"%NSSM%" set AWRPortal AppRestartDelay 5000
"%NSSM%" set AWRPortal ObjectName %SERVICE_ACCOUNT% %SERVICE_PASSWORD%
echo    ✓ AWRPortal installed

:: ── 2. AWR Watcher ───────────────────────────────────────────────────
echo [2/4] Installing AWRWatcher service...
call :remove_service AWRWatcher

"%NSSM%" install AWRWatcher "%PYTHON%"
"%NSSM%" set AWRWatcher AppParameters "watcher.py"
"%NSSM%" set AWRWatcher AppDirectory  "%PROJECT_DIR%"
"%NSSM%" set AWRWatcher DisplayName   "AWR File Watcher"
"%NSSM%" set AWRWatcher Description   "Monitors awr_reports folder and queues new AWR files"
"%NSSM%" set AWRWatcher Start         SERVICE_AUTO_START
"%NSSM%" set AWRWatcher AppStdout     "%LOG_DIR%\awr_watcher_stdout.log"
"%NSSM%" set AWRWatcher AppStderr     "%LOG_DIR%\awr_watcher_stderr.log"
"%NSSM%" set AWRWatcher AppRotateFiles 1
"%NSSM%" set AWRWatcher AppRotateBytes 10485760
"%NSSM%" set AWRWatcher AppRestartDelay 5000
"%NSSM%" set AWRWatcher ObjectName %SERVICE_ACCOUNT% %SERVICE_PASSWORD%
echo    ✓ AWRWatcher installed

:: ── 3. SAR Watcher ───────────────────────────────────────────────────
echo [3/4] Installing SARWatcher service...
call :remove_service SARWatcher

"%NSSM%" install SARWatcher "%PYTHON%"
"%NSSM%" set SARWatcher AppParameters "sar_watcher\sar_watcher.py"
"%NSSM%" set SARWatcher AppDirectory  "%PROJECT_DIR%"
"%NSSM%" set SARWatcher DisplayName   "SAR File Watcher"
"%NSSM%" set SARWatcher Description   "Monitors sar_drop folder and queues new SAR files"
"%NSSM%" set SARWatcher Start         SERVICE_AUTO_START
"%NSSM%" set SARWatcher AppStdout     "%LOG_DIR%\sar_watcher_stdout.log"
"%NSSM%" set SARWatcher AppStderr     "%LOG_DIR%\sar_watcher_stderr.log"
"%NSSM%" set SARWatcher AppRotateFiles 1
"%NSSM%" set SARWatcher AppRotateBytes 10485760
"%NSSM%" set SARWatcher AppRestartDelay 5000
"%NSSM%" set SARWatcher ObjectName %SERVICE_ACCOUNT% %SERVICE_PASSWORD%
echo    ✓ SARWatcher installed

:: ── 4. Queue Processor ───────────────────────────────────────────────
echo [4/4] Installing AWRQueueProcessor service...
call :remove_service AWRQueueProcessor

"%NSSM%" install AWRQueueProcessor "%PYTHON%"
"%NSSM%" set AWRQueueProcessor AppParameters "queue_processor.py --daemon --workers 4 --sar-workers 2"
"%NSSM%" set AWRQueueProcessor AppDirectory  "%PROJECT_DIR%"
"%NSSM%" set AWRQueueProcessor DisplayName   "AWR Queue Processor"
"%NSSM%" set AWRQueueProcessor Description   "Processes AWR and SAR report queues in parallel"
"%NSSM%" set AWRQueueProcessor Start         SERVICE_AUTO_START
"%NSSM%" set AWRQueueProcessor AppStdout     "%LOG_DIR%\queue_processor_stdout.log"
"%NSSM%" set AWRQueueProcessor AppStderr     "%LOG_DIR%\queue_processor_stderr.log"
"%NSSM%" set AWRQueueProcessor AppRotateFiles 1
"%NSSM%" set AWRQueueProcessor AppRotateBytes 10485760
"%NSSM%" set AWRQueueProcessor AppRestartDelay 5000
"%NSSM%" set AWRQueueProcessor ObjectName %SERVICE_ACCOUNT% %SERVICE_PASSWORD%
echo    ✓ AWRQueueProcessor installed

:: ── 5. Grafana ───────────────────────────────────────────────────────
echo [5/5] Installing Grafana service...
call :remove_service Grafana

:: Detect Grafana installation path
set GRAFANA_EXE=
if exist "C:\Program Files\GrafanaLabs\grafana\bin\grafana-server.exe" (
    set GRAFANA_EXE=C:\Program Files\GrafanaLabs\grafana\bin\grafana-server.exe
    set GRAFANA_DIR=C:\Program Files\GrafanaLabs\grafana
) else if exist "C:\grafana\bin\grafana-server.exe" (
    set GRAFANA_EXE=C:\grafana\bin\grafana-server.exe
    set GRAFANA_DIR=C:\grafana
)

if "%GRAFANA_EXE%"=="" (
    echo    ⚠ Grafana not found — skipping. Install Grafana and re-run to register as service.
    echo      Expected path: C:\Program Files\GrafanaLabs\grafana\bin\grafana-server.exe
) else (
    "%NSSM%" install Grafana "%GRAFANA_EXE%"
    "%NSSM%" set Grafana AppParameters "server --homepath \"%GRAFANA_DIR%\""
    "%NSSM%" set Grafana AppDirectory  "%GRAFANA_DIR%"
    "%NSSM%" set Grafana DisplayName   "Grafana"
    "%NSSM%" set Grafana Description   "Grafana Dashboard Server - AWR Insight Portal"
    "%NSSM%" set Grafana Start         SERVICE_AUTO_START
    "%NSSM%" set Grafana AppStdout     "%LOG_DIR%\grafana_stdout.log"
    "%NSSM%" set Grafana AppStderr     "%LOG_DIR%\grafana_stderr.log"
    "%NSSM%" set Grafana AppRotateFiles 1
    "%NSSM%" set Grafana AppRotateBytes 10485760
    "%NSSM%" set Grafana AppRestartDelay 5000
    echo    ✓ Grafana installed
)

:: ── Start all services ────────────────────────────────────────────────
echo.
echo Starting all services...
net start AWRQueueProcessor
net start AWRWatcher
net start SARWatcher
net start AWRPortal
if not "%GRAFANA_EXE%"=="" net start Grafana

echo.
echo ============================================================
echo  Installation complete. Services registered:
echo.
echo  AWRPortal         - http://localhost:8000
echo  AWRWatcher        - monitoring awr_reports\
echo  SARWatcher        - monitoring sar_drop\
echo  AWRQueueProcessor - processing AWR + SAR queues (4 workers)
if not "%GRAFANA_EXE%"=="" echo  Grafana           - http://localhost:3000
echo.
echo  IMPORTANT: Set service passwords in services.msc
echo    Right-click each service → Properties → Log On
echo    Select "This account" → enter .\Admin → your Windows password
echo.
echo  Manage via: Services (services.msc)
echo  Logs at   : %LOG_DIR%\
echo.
echo  To remove all services run:
echo    install_services.bat remove
echo ============================================================
pause
exit /b 0

:: ── Helper: remove a service if it exists ───────────────────────────
:remove_service
sc query "%~1" >nul 2>&1
if not errorlevel 1 (
    net stop "%~1" >nul 2>&1
    "%NSSM%" remove "%~1" confirm >nul 2>&1
    echo    Removed existing service: %~1
)
exit /b 0
