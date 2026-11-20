@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "ROOT=%~dp0"
set "PY=<DEV_ROOT>\python_env_py314\Scripts\python.exe"
set "COD=%ROOT%codigo"
set "AGG=%ROOT%aggs"
set "DAT=%ROOT%datos"

mode con: cols=120 lines=44
color 0B
title Spend Legacy - DASH - Panel de control

if not exist "%PY%" (
    cls
    echo.
    echo   ERROR: no se encontro Python en:
    echo   %PY%
    echo.
    pause
    exit /b 1
)

"%PY%" -c "import tqdm" >nul 2>&1 || (
    echo   Instalando tqdm - barra de progreso - desde PyPI...
    "%PY%" -m pip install tqdm -q >nul 2>&1
)

call :leer_semana

:menu
cls
echo.
echo   +----------------------------------------------------------------------+
echo   ^|                                                                      ^|
echo   ^|        GASTO CANAL FISICO  -  DASH            panel de control       ^|
echo   ^|                                                                      ^|
echo   +----------------------------------------------------------------------+
echo.
echo      Semana de corte vigente : !SEM!
echo      Datos   : %DAT%
echo      Agregados: %AGG%
echo.
echo   +-- TABLERO -----------------------------------------------------------+
echo   ^|  [1]  Iniciar el tablero Dash  (http://localhost:8050)               ^|
echo   +-- ACTUALIZAR LA SEMANA (lo de todos los jueves) ---------------------+
echo   ^|  [A]  Real 2026 del servidor SQL + aggs + dashboard.duckdb (~21 min) ^|
echo   ^|  [R]  Solo Real 2025 del servidor SQL + aggs                         ^|
echo   ^|  [P]  Solo Plan 2026 del servidor SQL + aggs                         ^|
echo   ^|  [M]  Menu avanzado: tablas del servidor, catalogos y etapas a elegir^|
echo   ^|  [F]  Solo Forecast Finanzas v04.11.26 (sin servidor)                ^|
echo   ^|  [S]  Solo FCST PLAN (sin servidor)                                   ^|
echo   +-- ACTUALIZAR AGREGADOS (aggs) SIN IR AL SERVIDOR --------------------+
echo   ^|  [2]  Todos los aggs de la semana !SEM!  (reusa el consolidado)         ^|
echo   ^|  [3]  Solo los aggs que elijas  (escribes los nombres)               ^|
echo   ^|  [4]  Ver la lista de aggs / etapas disponibles                      ^|
echo   +-- RECONSTRUIR -------------------------------------------------------+
echo   ^|  [5]  Reconstruir consolidado + aggs + duckdb de datos\ (~16 min)    ^|
echo   ^|  [6]  Regenerar solo dashboard.duckdb                                ^|
echo   +-- UTILIDADES --------------------------------------------------------+
echo   ^|  [7]  Ver estado del ultimo build                                    ^|
echo   ^|  [8]  Cambiar la semana de corte                                     ^|
echo   ^|  [0]  Salir                                                          ^|
echo   +----------------------------------------------------------------------+
echo.
choice /c 123456780AMFSRP /n /m "   Elige una opcion: "
set "OP=!errorlevel!"
if !OP! equ 15 goto solo_p26
if !OP! equ 14 goto solo_r25
if !OP! equ 13 goto fcst_srv
if !OP! equ 12 goto forecast
if !OP! equ 11 goto avanzado
if !OP! equ 10 goto semana
if !OP! equ 9 goto salir
if !OP! equ 8 goto cambiar_semana
if !OP! equ 7 goto ver_estado
if !OP! equ 6 goto duckdb
if !OP! equ 5 goto rebuild
if !OP! equ 4 goto listar
if !OP! equ 3 goto elegir
if !OP! equ 2 goto todos
if !OP! equ 1 goto iniciar
goto menu

:iniciar
cls
echo.
set "IP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127|169\.254)\.' -and $_.PrefixOrigin -ne 'WellKnown' } | Sort-Object @{e={$_.IPAddress -notlike '10.*'}}, InterfaceMetric | Select-Object -First 1).IPAddress"`) do set "IP=%%i"
echo   Iniciando el tablero Dash...  (cierra esta ventana para detenerlo)
echo.
echo   Local .............. http://localhost:8050
if defined IP (echo   Para compartir ..... http://!IP!:8050) else (echo   Para compartir ..... no se detecto la IP de red)
echo.
start "" http://localhost:8050
pushd "%ROOT%"
"%PY%" dash_app\app.py
popd
pause
goto menu

:semana
cls
echo.
echo   Trae Real 2026 del servidor SQL (solo esa tabla), reconstruye el consolidado y
echo   todos los aggs que dependen de los datos y regenera dashboard.duckdb.
echo   El tablero debe estar apagado. Tarda alrededor de 21 minutos.
echo   Real 2025 y Plan 2026 no se vuelven a extraer.
echo.
choice /c SN /n /m "   Confirmas (S/N)? "
if errorlevel 2 goto menu
cls
echo.
echo   ==== Actualizar semana: Real 2026 del servidor + aggs + dashboard.duckdb ====
echo   Inicio: %time:~0,8%
echo.
call :epoch T0
pushd "%COD%"
echo   [1/2] Extrayendo Real 2026 del servidor SQL...
"%PY%" extraer_sql.py --tablas r26
set "RC=!errorlevel!"
if !RC! geq 2 (
    echo.
    echo   La extraccion fallo ^(codigo !RC!^): no se toca el tablero.
    popd
    goto fin
)
echo.
echo   [2/2] Reconstruyendo consolidado, aggs y dashboard.duckdb...
"%PY%" build_data.py --rebuild --solo semanal
set "RC=!errorlevel!"
if "!RC!"=="0" (
    echo.
    echo   [3/3] Generando presentaciones en salidas\presentacion...
    "%PY%" generar_salidas.py
    if errorlevel 1 echo   AVISO: los datos se actualizaron, pero las presentaciones no se generaron.
)
popd
goto fin

:solo_r25
cls
echo.
echo   Trae Real 2025 del servidor SQL (solo esa tabla), reconstruye el consolidado y
echo   todos los aggs que dependen de los datos y regenera dashboard.duckdb.
echo   El tablero debe estar apagado.
echo.
choice /c SN /n /m "   Confirmas (S/N)? "
if errorlevel 2 goto menu
cls
echo.
echo   ==== Actualizar Real 2025: servidor + aggs + dashboard.duckdb ====
echo   Inicio: %time:~0,8%
echo.
call :epoch T0
pushd "%COD%"
echo   [1/2] Extrayendo Real 2025 del servidor SQL...
"%PY%" extraer_sql.py --tablas r25
set "RC=!errorlevel!"
if !RC! geq 2 (
    echo.
    echo   La extraccion fallo ^(codigo !RC!^): no se toca el tablero.
    popd
    goto fin
)
echo.
echo   [2/2] Reconstruyendo consolidado, aggs y dashboard.duckdb...
"%PY%" build_data.py --rebuild --solo semanal
set "RC=!errorlevel!"
popd
goto fin

:solo_p26
cls
echo.
echo   Trae Plan 2026 del servidor SQL (solo esa tabla), reconstruye el consolidado y
echo   todos los aggs que dependen de los datos y regenera dashboard.duckdb.
echo   El tablero debe estar apagado.
echo.
choice /c SN /n /m "   Confirmas (S/N)? "
if errorlevel 2 goto menu
cls
echo.
echo   ==== Actualizar Plan 2026: servidor + aggs + dashboard.duckdb ====
echo   Inicio: %time:~0,8%
echo.
call :epoch T0
pushd "%COD%"
echo   [1/2] Extrayendo Plan 2026 del servidor SQL...
"%PY%" extraer_sql.py --tablas p26
set "RC=!errorlevel!"
if !RC! geq 2 (
    echo.
    echo   La extraccion fallo ^(codigo !RC!^): no se toca el tablero.
    popd
    goto fin
)
echo.
echo   [2/2] Reconstruyendo consolidado, aggs y dashboard.duckdb...
"%PY%" build_data.py --rebuild --solo semanal
set "RC=!errorlevel!"
popd
goto fin
:avanzado
cls
echo.
pushd "%COD%"
"%PY%" actualizar.py
popd
echo.
pause
call :leer_semana
goto menu

:todos
set "TITULO=Actualizar TODOS los aggs - semana !SEM!"
set "CMD="%PY%" build_data.py --aggs-only --semana !SEM!"
goto ejecutar

:forecast
set "TITULO=Actualizar solo Forecast Finanzas - semana !SEM!"
set "CMD="%PY%" build_data.py --aggs-only --semana !SEM! --solo forecast_cuenta"
goto ejecutar

:fcst_srv
set "TITULO=Actualizar solo FCST PLAN - semana !SEM!"
set "CMD="%PY%" build_data.py --aggs-only --semana !SEM! --solo fcst_srv"
goto ejecutar

:elegir
cls
echo.
echo   Escribe las etapas separadas por coma, sin espacios.
echo   Ejemplo: ceco,contraloria,mes_contraloria
echo   (la opcion 4 del menu muestra todos los nombres validos)
echo.
set "ETAPAS="
set /p "ETAPAS=   Etapas (vacio = volver): "
if "!ETAPAS!"=="" goto menu
set "TITULO=Actualizar aggs elegidos - semana !SEM!"
set "CMD="%PY%" build_data.py --aggs-only --semana !SEM! --solo !ETAPAS!"
goto ejecutar

:listar
set "TITULO=Etapas y dimensiones disponibles"
set "CMD="%PY%" build_data.py --listar-etapas"
goto ejecutar

:rebuild
cls
echo.
echo   Esto reconstruye _consolidado.parquet desde los parquets crudos de la
echo   semana !SEM! en datos\ y luego todos los aggs que dependen de el, y
echo   regenera dashboard.duckdb. Tarda alrededor de 16 minutos.
echo.
choice /c SN /n /m "   Confirmas (S/N)? "
if errorlevel 2 goto menu
set "TITULO=Reconstruir consolidado + aggs - semana !SEM!"
set "CMD="%PY%" build_data.py --rebuild --solo semanal --semana !SEM!"
goto ejecutar

:duckdb
set "TITULO=Regenerar dashboard.duckdb"
set "CMD="%PY%" generate_dashboard_db.py"
goto ejecutar

:ejecutar
cls
echo.
echo   ==== !TITULO! ====
echo   Inicio: %time:~0,8%
echo.
call :epoch T0
pushd "%COD%"
!CMD!
set "RC=!errorlevel!"
popd

:fin
call :epoch T1
set /a DT=T1-T0, MM=DT/60, SS=DT%%60
echo.
echo   ====================================================================
echo   Fin: %time:~0,8%     Duracion: !MM! min !SS! s     Codigo de salida: !RC!
if not "!RC!"=="0" echo   ATENCION: termino con error, revisa el mensaje de arriba.
echo   ====================================================================
call :leer_semana
pause
goto menu

:ver_estado
cls
echo.
echo   ---- estado.json ----
if exist "%DAT%\estado.json" (type "%DAT%\estado.json") else (echo   no existe estado.json)
echo.
echo   ---- ultimas corridas (build_timings.json) ----
if exist "%DAT%\build_timings.json" (
    powershell -NoProfile -Command "try { $j = Get-Content -Raw -Encoding UTF8 '%DAT%\build_timings.json' | ConvertFrom-Json; $j | Select-Object -Last 5 | Format-List | Out-String -Width 80 } catch { 'sin formato legible' }"
) else (echo   no existe build_timings.json)
echo.
pause
goto menu

:cambiar_semana
cls
echo.
echo   Semana actual: !SEM!   (el build usa esta semana para buscar r25/r26/p26_semNN.parquet)
echo.
set "NUEVA="
set /p "NUEVA=   Nueva semana 1-53 (vacio = volver): "
if "!NUEVA!"=="" goto menu
for /f "delims=0123456789" %%a in ("!NUEVA!") do goto menu
set /a CHK=!NUEVA! 2>nul
if !CHK! lss 1 goto menu
if !CHK! gtr 53 goto menu
set "SEM=!CHK!"
goto menu

:leer_semana
set "SEM=39"
rem 2026-11-10: la semana = la mayor entre el corte del tablero (_meta.json) y el ultimo r26_semNN.parquet en datos\
for /f "usebackq" %%s in (`powershell -NoProfile -Command "try { $m=(Get-Content -Raw -Encoding UTF8 '%AGG%\_meta.json' | ConvertFrom-Json).sem_max_real } catch { $m=39 }; $f=(Get-ChildItem '%DAT%' -Filter 'r26_sem*.parquet' | ForEach-Object { [int]($_.BaseName -replace '^r26_sem','') } | Measure-Object -Maximum).Maximum; [Math]::Max([int]$m,[int]$f)"`) do set "SEM=%%s"
exit /b 0

:epoch
for /f "usebackq" %%t in (`powershell -NoProfile -Command "[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()"`) do set "%~1=%%t"
exit /b 0

:salir
endlocal
exit /b 0
