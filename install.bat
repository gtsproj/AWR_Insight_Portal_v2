@echo off
REM ============================================================
REM AWR Insight Portal v2 — Installation Bundle
REM Avekshaa Technologies
REM
REM Run as Administrator from the project folder:
REM   C:\AWR_Insight_Portal_v2\install.bat
REM ============================================================

setlocal enabledelayedexpansion
set "INSTALL_DIR=%~dp0"
set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"
set "LOG_FILE=%INSTALL_DIR%\install_log.txt"
set "ERRORS=0"

echo ============================================================
echo  AWR Insight Portal v2 — Installation
echo  Avekshaa Technologies
echo  %DATE% %TIME%
echo ============================================================
echo.

REM Check admin rights
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Please run this script as Administrator.
    pause
    exit /b 1
)

call :log "Installation started: %DATE% %TIME%"
call :log "Install directory: %INSTALL_DIR%"

REM ── Step 1: Check Python ─────────────────────────────────────────
echo [1/8] Checking Python 3.13+...
python --version 2>&1 | findstr /C:"3.13" /C:"3.14" /C:"3.15" >nul
if %errorlevel% neq 0 (
    echo       [WARNING] Python 3.13+ not found. Checking py launcher...
    py -3.13 --version >nul 2>&1
    if !errorlevel! neq 0 (
        echo       [ERROR] Python 3.13+ is required.
        echo       Download from: https://www.python.org/downloads/
        set "ERRORS=1"
    ) else (
        echo       [OK] Python 3.13 found via py launcher
        set "PYTHON=py -3.13"
    )
) else (
    echo       [OK] Python found
    set "PYTHON=python"
)

REM ── Step 2: Install Python packages ─────────────────────────────
echo [2/8] Installing Python packages...
%PYTHON% -m pip install --upgrade pip --quiet
%PYTHON% -m pip install fastapi uvicorn jinja2 psycopg2-binary ^
    python-multipart aiofiles paramiko psutil --quiet
if %errorlevel% neq 0 (
    echo       [ERROR] Failed to install Python packages
    set "ERRORS=1"
) else (
    echo       [OK] Python packages installed
)

REM ── Step 3: Check PostgreSQL ─────────────────────────────────────
echo [3/8] Checking PostgreSQL...
psql --version >nul 2>&1
if %errorlevel% neq 0 (
    echo       [ERROR] PostgreSQL not found in PATH.
    echo       Install PostgreSQL 14+ from: https://www.postgresql.org/download/windows/
    echo       Then add C:\Program Files\PostgreSQL\{version}\bin to PATH
    set "ERRORS=1"
) else (
    for /f "tokens=*" %%v in ('psql --version') do echo       [OK] %%v
)

REM ── Step 4: Create directories ────────────────────────────────────
echo [4/8] Creating required directories...
for %%d in (logs archive sar_archive sar_drop awr_reports queues sar_queues) do (
    if not exist "%INSTALL_DIR%\%%d" (
        mkdir "%INSTALL_DIR%\%%d"
        echo       Created: %%d
    ) else (
        echo       Exists:  %%d
    )
)
echo       [OK] Directories ready

REM ── Step 5: Run database schemas ─────────────────────────────────
echo [5/8] Setting up database schemas...
echo       Connecting to PostgreSQL as postgres...

set "PGPASSWORD=postgres"
set "PSQL=psql -U postgres -d postgres -h localhost"

REM Test connection
%PSQL% -c "SELECT version();" >nul 2>&1
if %errorlevel% neq 0 (
    echo       [WARNING] Cannot connect to PostgreSQL with default credentials.
    echo       Please run schemas manually:
    echo         psql -U postgres -d postgres -f awr_master_schema_v2.sql
    echo         psql -U postgres -d postgres -f portal_settings_schema.sql
    echo         psql -U postgres -d postgres -f awr_object_metadata_schema.sql
    echo         psql -U postgres -d postgres -f awr_license_schema.sql
    echo         psql -U postgres -d postgres -f remote_fetch_schema.sql
    echo         psql -U postgres -d postgres -f awr_db_security_schema.sql
) else (
    echo       Connected. Running schema files...
    for %%s in (
        awr_master_schema_v2.sql
        portal_settings_schema.sql
        awr_object_metadata_schema.sql
        awr_license_schema.sql
        remote_fetch_schema.sql
        awr_db_security_schema.sql
    ) do (
        if exist "%INSTALL_DIR%\%%s" (
            %PSQL% -f "%INSTALL_DIR%\%%s" >nul 2>&1
            if !errorlevel! neq 0 (
                echo       [WARNING] %%s had errors (may already exist — OK)
            ) else (
                echo       [OK] %%s
            )
        ) else (
            echo       [SKIP] %%s not found
        )
    )

    REM Insert default config
    %PSQL% -c "INSERT INTO portal_config (key,value,section) VALUES ('metadata_refresh_days','14','access') ON CONFLICT (key) DO NOTHING;" >nul 2>&1
    echo       [OK] Default config inserted
)

REM ── Step 6: Check Grafana ────────────────────────────────────────
echo [6/8] Checking Grafana...
if exist "%INSTALL_DIR%\grafana-v12.0.2\bin\grafana-server.exe" (
    echo       [OK] Grafana found at grafana-v12.0.2\
) else (
    REM Check for any grafana version
    dir "%INSTALL_DIR%\grafana*" /ad /b >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "tokens=*" %%g in ('dir "%INSTALL_DIR%\grafana*" /ad /b') do (
            if exist "%INSTALL_DIR%\%%g\bin\grafana-server.exe" (
                echo       [OK] Grafana found at %%g
            )
        )
    ) else (
        echo       [WARNING] Grafana not found.
        echo       Download Grafana 12 from: https://grafana.com/grafana/download
        echo       Extract to: %INSTALL_DIR%\grafana-v12.0.2\
    )
)

REM ── Step 7: Check WSL for SAR ─────────────────────────────────────
echo [7/8] Checking WSL for SAR binary conversion...
wsl --status >nul 2>&1
if %errorlevel% neq 0 (
    echo       [WARNING] WSL not available. SAR binary files will not be supported.
    echo       Enable WSL: wsl --install
    echo       Then install sysstat: wsl sudo apt install sysstat -y
) else (
    wsl which sadf >nul 2>&1
    if !errorlevel! neq 0 (
        echo       [WARNING] sadf not found in WSL. Install: wsl sudo apt install sysstat -y
    ) else (
        echo       [OK] WSL + sadf available
    )
)

REM ── Step 8: Install/verify NSSM services ─────────────────────────
echo [8/8] Setting up Windows services...
where nssm >nul 2>&1
if %errorlevel% neq 0 (
    echo       [WARNING] NSSM not found. Services must be installed manually.
    echo       Download NSSM from: https://nssm.cc/download
    echo       Then run install_services.bat
    echo.
    echo       Or start portal manually:
    echo         %PYTHON% -m uvicorn portal.app:app --host 0.0.0.0 --port 8000
) else (
    echo       NSSM found. Installing services...
    call "%INSTALL_DIR%\install_services.bat" /silent
    if !errorlevel! equ 0 (
        echo       [OK] Services installed
    ) else (
        echo       [WARNING] Some services may need manual installation
    )
)

REM ── Summary ──────────────────────────────────────────────────────
echo.
echo ============================================================
if %ERRORS% equ 0 (
    echo  Installation complete!
) else (
    echo  Installation completed with warnings. Check items above.
)
echo.
echo  Next steps:
echo  1. Generate trial license key:
echo     %PYTHON% modules\license_engine.py mac
echo     %PYTHON% modules\license_engine.py generate --tier T30 --mac YOUR_MAC ^
echo       --db 2 --sar 2 --expiry YYYY-MM-DD --customer ID --name "Name"
echo.
echo  2. Start portal:
echo     %PYTHON% -m uvicorn portal.app:app --host 0.0.0.0 --port 8000
echo.
echo  3. Open browser: http://localhost:8000
echo     Default login: admin / Admin@123
echo.
echo  4. Go to Settings -^> License and enter your trial key
echo.
echo  5. Import Grafana dashboards from portal\static\*.json
echo     Grafana URL: http://localhost:3000 (admin/admin)
echo ============================================================
echo.
call :log "Installation completed. Errors: %ERRORS%"
pause
exit /b %ERRORS%

:log
echo %~1 >> "%LOG_FILE%"
exit /b 0
