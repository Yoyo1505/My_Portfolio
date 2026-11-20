@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
title Spend Legacy - DASH (puerto 8050)

cd /d "%~dp0"
set "PYTHON_ENV=<DEV_ROOT>\python_env_py314\Scripts\python.exe"

if not exist "%PYTHON_ENV%" (
    echo ERROR: No se encontro Python en: %PYTHON_ENV%
    pause
    exit /b 1
)

set "IP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127|169\.254)\.' -and $_.PrefixOrigin -ne 'WellKnown' } | Sort-Object @{e={$_.IPAddress -notlike '10.*'}}, InterfaceMetric | Select-Object -First 1).IPAddress"`) do set "IP=%%i"

netstat -ano | findstr /R /C:":8050 .*LISTENING" >nul
if not errorlevel 1 (
    echo.
    echo   El tablero ya esta corriendo en el puerto 8050.
    echo   Local .............. http://localhost:8050
    if defined IP echo   Para compartir ..... http://%IP%:8050
    start "" http://localhost:8050
    timeout /t 8 >nul
    exit /b 0
)

echo.
echo   Iniciando el tablero Dash...  (cierra esta ventana para detenerlo)
echo.
echo   Local .............. http://localhost:8050
if defined IP (echo   Para compartir ..... http://%IP%:8050) else (echo   Para compartir ..... no se detecto la IP de red)
echo.
start "" http://localhost:8050
"%PYTHON_ENV%" "dash_app\app.py"
echo.
pause
