
# -*- coding: utf-8 -*-
"""
extraer_sql.py — Trae Real 2025 / Real 2026 / Plan 2026 desde SQL Server
                 y los deja como parquet en la carpeta del proyecto.

Reemplaza a los notebooks "Real 2025.ipynb", "Real 2026.ipynb", "Plan 2026.ipynb"
(solo la parte de extracción; las agrupaciones ya viven en build_data.py).

Credenciales: se leen de .env (nunca del código). Ver .env.ejemplo.

Uso:
    python extraer_sql.py              # solo si SQL tiene más filas que el parquet
    python extraer_sql.py --force      # siempre re-extrae
    python extraer_sql.py --check      # NO extrae; solo dice si hay datos nuevos (exit 0/1)
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
import threading
import time
from pathlib import Path

import semana

BASE = Path(__file__).parent.resolve()   # /codigo
ROOT = BASE.parent                       # raíz del proyecto (tras reorganización 2026-08-14)
DATOS = ROOT / "datos"
ENV = ROOT / "archivos_proyecto" / ".env"

# tabla SQL -> (Origen, Año). El nombre del parquet lo da semana.py: cambia
# cada semana (r26_sem28.parquet), no se sobrescribe el de la semana pasada.
TABLAS = {
    "R25": ("Real", 2025),
    "R26": ("Real", 2026),
    "P26": ("Plan", 2026),
    "FCST": ("Forecast", 2026),   # 2026-10-21: misma tabla FCST del servidor que usa Spend Legacy
}

# tabla -> prefijo de archivo, para ubicar "el parquet existente más reciente"
# con semana.parquet_mas_reciente() (independiente de qué semana sea hoy).
PREFIJO = {"R25": "r25", "R26": "r26", "P26": "p26", "FCST": "fcst",
           "Real 2025": "r25", "Real 2026": "r26", "Plan 2026": "p26"}

# alias cortos para --tablas
ALIAS_TABLA = {
    "r25": "R25", "real2025": "R25", "real 2025": "R25", "Real 2025": "R25",
    "r26": "R26", "real2026": "R26", "real 2026": "R26", "Real 2026": "R26",
    "p26": "P26", "plan2026": "P26", "plan 2026": "P26", "Plan 2026": "P26",
    "fcst": "FCST", "forecast": "FCST",
}


def _resolver_tablas(spec: str | None) -> dict:
    """--tablas 'r26,p26' -> {'R26': (...), 'P26': (...)}.
    None o vacío = las 3 (comportamiento de siempre)."""
    if not spec:
        return dict(TABLAS)
    elegidas = {}
    for tok in spec.split(","):
        tok = tok.strip().casefold()
        if not tok:
            continue
        nombre = ALIAS_TABLA.get(tok)
        if nombre is None:
            sys.exit(f"ERROR: tabla desconocida en --tablas: '{tok}'. "
                     f"Usa r25/r26/p26 o 'R25'/'R26'/'P26'.")
        elegidas[nombre] = TABLAS[nombre]
    return elegidas


# La consola de Windows es cp1252 y el log lleva 'Δ' y '·'. Corriendo bajo
# actualizar.py hay PYTHONIOENCODING=utf-8, pero a mano no: forzamos UTF-8
# aquí también para no morir con UnicodeEncodeError a media extracción.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(m):
    try:
        print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)
    except UnicodeEncodeError:
        print(f"[{time.strftime('%H:%M:%S')}] "
              + m.encode("ascii", "replace").decode("ascii"), flush=True)


def cargar_env() -> dict:
    """Lee .env. Las variables de entorno reales tienen prioridad."""
    cfg = {}
    if ENV.exists():
        for linea in ENV.read_text(encoding="utf-8-sig").splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            k, v = linea.split("=", 1)
            cfg[k.strip()] = v.strip()
    for k in ("SQL_SERVER", "SQL_DATABASE", "SQL_USER", "SQL_PASSWORD", "SQL_DRIVER", "SQL_SERVERS"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    faltan = [k for k in ("SQL_DATABASE", "SQL_PASSWORD") if not cfg.get(k)]
    if faltan:
        sys.exit(f"ERROR: faltan variables obligatorias {faltan} en {ENV} o en las variables de entorno.\n"
                 f"       Configura SQL_PASSWORD y SQL_DATABASE en archivos_proyecto/.env.")
    cfg.setdefault("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
    return cfg


# Orden de preferencia de drivers.
_DRIVERS_FALLBACK = [
    "ODBC Driver 18 for SQL Server",
    "ODBC Driver 17 for SQL Server",
    "SQL Server Native Client 11.0",
    "SQL Server",
]
_CONEXION_OK: dict = {}  # cache de (server, user, password, driver) que funcionó


def _cadena_conexion(server: str, database: str, user: str, password: str, driver: str) -> str:
    extra = "Encrypt=no;" if driver == "ODBC Driver 18 for SQL Server" else ""
    return (f"DRIVER={{{driver}}};SERVER={server};"
            f"DATABASE={database};UID={user};"
            f"PWD={password};TrustServerCertificate=yes;{extra}")


def conectar(cfg: dict, timeout: int = 8):
    """Prueba los servidores configurados y drivers ODBC en orden de prioridad hasta conectar."""
    import pyodbc

    # Si ya sabemos cuál servidor y driver funcionó en esta sesión, intentar primero
    if _CONEXION_OK:
        try:
            return pyodbc.connect(
                _cadena_conexion(
                    _CONEXION_OK["server"],
                    _CONEXION_OK["database"],
                    _CONEXION_OK["user"],
                    _CONEXION_OK["password"],
                    _CONEXION_OK["driver"]
                ),
                autocommit=True,
                timeout=timeout
            )
        except Exception:
            _CONEXION_OK.clear()

    # Construir lista ordenada de servidores a probar según configuración .env
    db_name = cfg.get("SQL_DATABASE", "Spend Database")
    password = cfg.get("SQL_PASSWORD")
    if not password:
        raise ValueError("SQL_PASSWORD no está definida en .env ni en el entorno.")

    servidores_a_probar = []

    # 1. Servidor primario: sql-server.example.local (usuario RGARG por defecto),
    # configurable con SQL_SERVER/SQL_USER en .env.
    env_server = cfg.get("SQL_SERVER", "sql-server.example.local,1433")
    env_user = cfg.get("SQL_USER", "report_user")
    servidores_a_probar.append({
        "server": env_server,
        "user": env_user,
        "password": password,
        "database": db_name,
    })

    # 3. Servidores adicionales en SQL_SERVERS (separados por coma), si los hay
    servidores_extra = cfg.get("SQL_SERVERS", "")
    if servidores_extra:
        ya_listados = {s["server"] for s in servidores_a_probar}
        for s in servidores_extra.split(","):
            s = s.strip()
            if s and s not in ya_listados:
                servidores_a_probar.append({
                    "server": s,
                    "user": env_user,
                    "password": password,
                    "database": db_name,
                })
                ya_listados.add(s)

    instalados = set(pyodbc.drivers())
    drivers_orden = [cfg.get("SQL_DRIVER", "ODBC Driver 17 for SQL Server")] + [
        d for d in _DRIVERS_FALLBACK if d != cfg.get("SQL_DRIVER")
    ]
    drivers_disponibles = [d for d in drivers_orden if d in instalados] or _DRIVERS_FALLBACK

    errores = []
    for srv_info in servidores_a_probar:
        srv = srv_info["server"]
        usr = srv_info["user"]
        pwd = srv_info["password"]
        db = srv_info["database"]
        
        for drv in drivers_disponibles:
            try:
                conn_str = _cadena_conexion(srv, db, usr, pwd, drv)
                cn = pyodbc.connect(conn_str, autocommit=True, timeout=timeout)
                log(f"  Conexión exitosa a SQL Server en '{srv}' (Usuario: {usr}) usando '{drv}'")
                _CONEXION_OK["server"] = srv
                _CONEXION_OK["database"] = db
                _CONEXION_OK["user"] = usr
                _CONEXION_OK["password"] = pwd
                _CONEXION_OK["driver"] = drv
                return cn
            except Exception as e:
                msg_err = str(e).split("] [")[0].replace("\n", " ")[:90]
                errores.append(f"[{srv} - {drv}]: {msg_err}")

    raise RuntimeError("No se pudo conectar a ningún servidor SQL Server configurado.\n  " + "\n  ".join(errores))


# En SQL la columna lleva ESPACIOS; el parquet la guarda con guiones bajos
# (el notebook original hacía c.strip().replace(" ", "_")).
MONTO_SQL = "SUM_MONTO_MNX_CONS (SUMA)"
MONTO_LOCAL_SQL = "Monto Local"
MONTO = "SUM_MONTO_MNX_CONS_(SUMA)"
MONTO_LOCAL = "Monto_Local"

# Tolerancia RELATIVA al comparar sumas.
TOL_REL = 1e-9
TOL_ABS = 1.0      # piso, para tablas con montos pequeños o suma cero


def _diferente(a: float, b: float) -> bool:
    return abs(a - b) > max(TOL_ABS, TOL_REL * max(abs(a), abs(b)))


def firma_sql(cur, tabla: str) -> tuple[int, float, float]:
    """(filas, suma constante, suma local). Detecta tanto altas como correcciones de importe."""
    cur.execute(f'SELECT COUNT(*), COALESCE(SUM(CAST([{MONTO_SQL}] AS FLOAT)), 0), COALESCE(SUM(CAST([{MONTO_LOCAL_SQL}] AS FLOAT)), 0) FROM [{tabla}]')
    n, s, sl = cur.fetchone()
    return int(n), float(s or 0.0), float(sl or 0.0)


def firma_parquet(p: Path) -> tuple[int, float, float]:
    if not p.exists():
        return -1, 0.0, 0.0
    import duckdb
    con = duckdb.connect()
    try:
        cols = [c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p.as_posix()}') LIMIT 1").fetchall()]
        has_loc = MONTO_LOCAL in cols
        loc_expr = f'COALESCE(SUM(CAST("{MONTO_LOCAL}" AS DOUBLE)), 0)' if has_loc else '0.0'
        n, s, sl = con.execute(
            f'''SELECT COUNT(*), COALESCE(SUM(CAST("{MONTO}" AS DOUBLE)), 0), {loc_expr}
                FROM read_parquet('{p.as_posix()}')''').fetchone()
        return int(n), float(s or 0.0), float(sl or 0.0)
    finally:
        con.close()


def hay_novedades(cfg: dict, sem: int | None = None, tablas: dict | None = None) -> tuple[bool, dict]:
    """Compara (filas, suma constante, suma local) SQL vs el parquet ya existente más reciente."""
    tablas = tablas if tablas is not None else TABLAS
    nom = semana.nombres(sem)
    det = {}
    with conectar(cfg) as cn:
        cur = cn.cursor()
        for tabla in tablas:
            n_sql, s_sql, sl_sql = firma_sql(cur, tabla)
            destino = DATOS / nom[tabla]
            n_pq, s_pq, sl_pq = firma_parquet(destino)
            if n_pq >= 0:
                # el archivo de ESTA semana ya existe: compara contra él.
                referencia = destino
            else:
                referencia = semana.parquet_mas_reciente(PREFIJO[tabla])
                if referencia is not None:
                    n_pq, s_pq, sl_pq = firma_parquet(referencia)

            if n_pq < 0:                       # no hay ningún parquet previo de esta tabla
                cambio, motivo, falta_copiar = True, "sin parquet previo, extracción inicial", False
            elif n_sql != n_pq:
                cambio, motivo, falta_copiar = True, "cambió el número de filas", False
            elif _diferente(s_sql, s_pq) or _diferente(sl_sql, sl_pq):
                cambio, motivo, falta_copiar = True, "cambió el monto (constante o local)", False
            elif not destino.exists():
                cambio, motivo, falta_copiar = False, f"sin cambios vs {referencia.name}: se copia", True
            else:
                cambio, motivo, falta_copiar = False, "", False
            det[tabla] = {"sql": n_sql, "parquet": n_pq, "archivo": nom[tabla],
                          "monto_sql": s_sql, "monto_pq": s_pq,
                          "monto_loc_sql": sl_sql, "monto_loc_pq": sl_pq,
                          "cambio": cambio, "motivo": motivo,
                          "falta_copiar": falta_copiar,
                          "referencia": referencia if falta_copiar else None}
    hay = any(d["cambio"] or d["falta_copiar"] for d in det.values())
    return hay, det


# Solo las columnas que lee build_data.py / comparacion.py; Origen, Año y
# Combinada eran constantes por tabla y #_SEMANA duplicaba SEMANA_ARG.
COLS_NECESARIAS = {"ID_CENTRO_COSTOS", "ID_CONCEPTO_CUENTA_NIV3", "SEMANA_ARG",
                   MONTO, MONTO_LOCAL, "MES"}

LOTE = 500_000      # filas por fetchmany; da un latido cada ~10-20 s
_LOG_LOCK = threading.Lock()


def _procesar_lote_polars(filas: list[tuple], cols_limpias: list[str], origen: str, anio: int, schema: dict | None) -> tuple[pl.DataFrame, dict]:
    """Convierte filas SQL en Polars DataFrame usando transposición C-level rápida y schema predeterminado."""
    import polars as pl

    columnas_dict = dict(zip(cols_limpias, zip(*filas)))
    if schema is None:
        df_lote = pl.DataFrame(columnas_dict, infer_schema_length=None)
        schema = df_lote.schema
    else:
        df_lote = pl.DataFrame(columnas_dict, schema=schema)

    if "SEMANA_ARG" in df_lote.columns:
        df_lote = df_lote.with_columns(pl.col("SEMANA_ARG").cast(pl.Int64, strict=False))

    return df_lote, schema


def extraer(cfg: dict, tabla: str, destino: Path, origen: str, anio: int, total: int = 0):
    """Extrae datos de SQL Server con buffering optimizado, transformación por lotes y escritura Parquet."""
    import polars as pl

    log(f"  [{tabla}] iniciando extracción ({total:,} filas estimadas)..." if total else f"  [{tabla}] iniciando extracción...")
    t0 = time.time()
    partes = []
    leidas = 0
    schema_comun = None

    with conectar(cfg, timeout=0) as cn:
        cur = cn.cursor()
        cur.arraysize = 100_000  # optimiza buffer de red del driver ODBC
        cur.execute(f"SELECT TOP 0 * FROM [{tabla}]")
        disponibles = [d[0] for d in cur.description]
        sel = [c for c in disponibles if c.strip().replace(" ", "_") in COLS_NECESARIAS]
        cur.execute("SELECT " + ", ".join(f"[{c}]" for c in sel) + f" FROM [{tabla}]")
        cols_raw = [d[0] for d in cur.description]
        cols_limpias = [c.strip().replace(" ", "_") for c in cols_raw]

        while True:
            filas = cur.fetchmany(LOTE)
            if not filas:
                break
            
            df_lote, schema_comun = _procesar_lote_polars(filas, cols_limpias, origen, anio, schema_comun)
            partes.append(df_lote)
            leidas += len(filas)
            pct = f" ({leidas*100//total}%)" if total else ""
            log(f"    [{tabla}] {leidas:,} filas{pct} · {time.time()-t0:.0f}s")
            del filas

    if partes:
        try:
            df = pl.concat(partes, how="vertical")
        except Exception as e:
            log(f"    [{tabla}] AVISO: reconciliando esquema con coerción relajada ({e})")
            df = pl.concat(partes, how="vertical_relaxed")
    else:
        df = pl.DataFrame({c: [] for c in cols_limpias})
    del partes

    # Escritura atómica a parquet con compresión ZSTD
    tmp = destino.with_suffix(".parquet.tmp")
    df.write_parquet(tmp, compression="zstd", compression_level=3)
    tmp.replace(destino)
    
    tam_mb = destino.stat().st_size / 1e6
    segundos = time.time() - t0
    log(f"  [{tabla}] COMPLETADO: {df.height:,} filas -> {destino.name} ({tam_mb:.1f} MB en {segundos:.0f}s)")
    del df


def procesar_tabla_tarea(cfg: dict, tabla: str, destino: Path, origen: str, anio: int, d: dict, force: bool):
    """Tarea unitaria de procesamiento por tabla para ejecución paralela."""
    import shutil
    if d["cambio"] or force:
        extraer(cfg, tabla, destino, origen, anio, total=d["sql"])
    elif d["falta_copiar"] and not destino.exists():
        tmp = destino.with_suffix(".parquet.tmp")
        shutil.copy2(d["referencia"], tmp)
        tmp.replace(destino)
        log(f"  [{tabla}] sin cambios en SQL: copiado {d['referencia'].name} -> {destino.name}")
    else:
        log(f"  [{tabla}] sin cambios: se conserva {destino.name}")


def main():
    import concurrent.futures
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-extrae aunque los conteos coincidan")
    ap.add_argument("--check", action="store_true", help="solo comprueba; exit 0 si hay novedades, 1 si no")
    ap.add_argument("--semana", type=int, default=None,
                    help="semana ARG para nombrar los parquet (por defecto, última semana cerrada)")
    ap.add_argument("--concurrencia", type=int, default=3, help="hilos paralelos para descarga (por defecto 3)")
    ap.add_argument("--tablas", default=None, metavar="TABLAS",
                    help="revisar/extraer SOLO estas tablas (separadas por coma): "
                         "r25, r26, p26 (o 'Real 2025'/'Real 2026'/'Plan 2026'). "
                         "Por defecto, las 3.")
    args = ap.parse_args()

    cfg = cargar_env()
    tablas = _resolver_tablas(args.tablas)
    sem = args.semana if args.semana is not None else semana.semana_corte()
    ini, fin = semana.rango_semana(sem)
    log(f"Comprobando servidores SQL Server · base {cfg['SQL_DATABASE']}")
    log(f"Semana ARG {sem} ({ini} a {fin}) · tablas: {', '.join(tablas)} · "
        f"destino: {', '.join(semana.nombres(sem)[t] for t in tablas)}")

    try:
        hay, det = hay_novedades(cfg, sem, tablas)
    except Exception as e:
        # exit 2 = error. NO usar 1: en --check el 1 significa "sin cambios"
        # y un fallo de red se leería como "todo al día".
        print(f"ERROR de conexión: {str(e)[:200]}", file=sys.stderr)
        sys.exit(2)

    for t, d in det.items():
        pq = f"{d['parquet']:,}" if d["parquet"] >= 0 else "(no existe)"
        marca = f"  <-- {d['motivo']}" if (d["cambio"] or d["falta_copiar"]) else ""
        log(f"  {t:12s} filas SQL={d['sql']:>12,}  parquet={pq:>14}{marca}")
        if d["parquet"] >= 0:
            dif = d["monto_sql"] - d["monto_pq"]
            log(f"  {'':12s} monto SQL={d['monto_sql']/1e6:>12,.1f}M  "
                f"parquet={d['monto_pq']/1e6:>12,.1f}M  Δ={dif/1e6:+,.1f}M")

    if args.check:
        log("HAY DATOS NUEVOS" if hay else "sin cambios")
        sys.exit(0 if hay else 1)

    if not hay and not args.force:
        log("Sin cambios. Nada que hacer (usa --force para re-extraer).")
        return

    nom = semana.nombres(sem)
    log(f"Extrayendo en paralelo ({min(args.concurrencia, len(tablas))} hilos)...")
    t0 = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrencia) as executor:
        futuros = []
        for tabla, (origen, anio) in tablas.items():
            destino = DATOS / nom[tabla]
            f = executor.submit(
                procesar_tabla_tarea,
                cfg, tabla, destino, origen, anio, det[tabla], args.force
            )
            futuros.append(f)
        
        for fut in concurrent.futures.as_completed(futuros):
            fut.result()

    log(f"[OK] extracción completada en {(time.time()-t0)/60:.2f} min")
    log(f"Siguiente paso: python build_data.py --rebuild --semana {sem}")


if __name__ == "__main__":
    main()
