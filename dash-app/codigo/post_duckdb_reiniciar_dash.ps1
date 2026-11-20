# Espera a que termine la regeneracion de aggs\dashboard.duckdb (generate_dashboard_db.py),
# verifica que existan consolidado y los rollups, y levanta el Dash (puerto 8050) si no esta corriendo.
$root = "<PROJECT_ROOT>"
$py   = "<DEV_ROOT>\python_env_py314\Scripts\python.exe"
$fin  = "$root\archivos_proyecto\duckdb_regen_fin.txt"
$log  = "$root\archivos_proyecto\post_duckdb.log"
function Log($m) { "[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $m | Out-File $log -Append -Encoding utf8 }

Log "esperando fin de regeneracion duckdb"
$limite = (Get-Date).AddHours(4)
while (-not (Test-Path $fin) -and (Get-Date) -lt $limite) { Start-Sleep 30 }
if (-not (Test-Path $fin)) { Log "TIMEOUT: no termino la regeneracion; no se reinicia el Dash"; exit 1 }

$chk = @'
import duckdb, sys
con = duckdb.connect(r"<PROJECT_ROOT>", read_only=True)
t = {r[0] for r in con.execute("select table_name from duckdb_tables()").fetchall()}
need = {"consolidado","rollup_ceco_general","rollup_ceco_general_gpo","rollup_mes_contraloria","rollup_pdv_general","rollup_pdv_general_gpo","rollup_pdv_general_prefijo","rollup_pdv_porcuenta"}
falta = sorted(need - t)
print("FALTAN:" + ",".join(falta) if falta else "OK")
sys.exit(1 if falta else 0)
'@
$chk | Out-File "$env:TEMP\chk_duckdb.py" -Encoding utf8
$res = & $py -I "$env:TEMP\chk_duckdb.py" 2>&1
Log "verificacion duckdb: $res"
if ($LASTEXITCODE -ne 0) { Log "duckdb incompleto; no se reinicia el Dash"; exit 1 }

if (Get-NetTCPConnection -LocalPort 8050 -State Listen -ErrorAction SilentlyContinue) {
    Log "el Dash ya corre en 8050; reinicialo a mano para tomar los datos nuevos"
} else {
    Start-Process -FilePath $py -ArgumentList "dash_app\app.py" -WorkingDirectory $root -WindowStyle Minimized
    Log "Dash iniciado en 8050"
}
