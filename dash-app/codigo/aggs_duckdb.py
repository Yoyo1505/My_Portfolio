# -*- coding: utf-8 -*-
"""
aggs_duckdb.py — Aggs "al vuelo" en DuckDB (lazy), sin generar parquets de aggs.

Para qué: la PPT semanal, el PDF y el correo de canales hoy leen `aggs/pdc.parquet` (1.4 GB, 38 M filas)
y otros aggs que el build tiene que regenerar cada semana. Aquí se calculan los mismos números
directamente sobre `consolidado` (aggs/dashboard.duckdb, solo lectura), con DuckDB, solo las columnas y
semanas que se piden. Así esos reportes no dependen de ningún agg del build.

Fuentes (parámetro `fuente`):
  "duckdb"  (default)  aggs/dashboard.duckdb, tabla `consolidado`, ATTACH en solo lectura.
  "parquet"            aggs/_consolidado.parquet (zstd), lectura perezosa con pushdown de columnas y filtros.

Reglas del proyecto que respeta:
  - `consolidado` es SOLO canal físico (el digital vive en digitales.duckdb).
  - Un cursor por consulta y un lock para la creación de la conexión (los crashes 0xc0000005 vinieron de
    compartir una conexión DuckDB entre hilos).
  - Cada consulta deja su tiempo en datos/timings_aggs_duckdb.jsonl.

Uso:
  python codigo/aggs_duckdb.py --bench --sem 42
  python codigo/aggs_duckdb.py --comparar-pdc "E:/.../Spend Legacy/aggs/pdc.parquet" --sem 42
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
AGGS = ROOT / "aggs"
DATOS = ROOT / "datos"
DB = AGGS / "dashboard.duckdb"
CONS_PARQUET = AGGS / "_consolidado.parquet"
LOG_TIEMPOS = DATOS / "timings_aggs_duckdb.jsonl"

# Serie del consolidado -> nombre de la medida (mismos nombres que los aggs del build)
SERIES = {"R25": "Real_2025", "P26": "Plan_2026", "R26": "Real_2026"}

# columnas por las que se puede agrupar/filtrar (lista blanca: los nombres van dentro del SQL)
COLS_GRUPO = {
    "sem", "MES", "TRIMESTRE", "ECO_PDV", "ID_CENTRO_COSTOS", "ID_CONCEPTO_CUENTA_NIV3",
    "cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "cat_CtaMayor_Nombre", "cat_PosPre",
    "cat_Denom_PosPre", "cat_Prefijo", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
    "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "cat_PDC", "cat_Agrupa1", "cat_Agrupa2",
    "cat_Agrupa3", "cat_Subtipo", "cat_Contraloria", "cat_Contraloria_6",
}

_lock = threading.RLock()
_con: duckdb.DuckDBPyConnection | None = None
_cfg = {"fuente": "duckdb", "hilos": 4, "memoria": "4GB"}


def configurar(fuente: str = "duckdb", hilos: int = 4, memoria: str = "4GB") -> None:
    """Cambia la fuente/recursos. Cierra la conexión actual: la siguiente consulta abre otra."""
    global _con
    if fuente not in ("duckdb", "parquet"):
        raise ValueError("fuente debe ser 'duckdb' o 'parquet'")
    with _lock:
        if _con is not None:
            _con.close()
            _con = None
        _cfg.update(fuente=fuente, hilos=int(hilos), memoria=str(memoria))
        lru_cache_limpiar()


def _conexion() -> duckdb.DuckDBPyConnection:
    global _con
    with _lock:
        if _con is None:
            con = duckdb.connect(":memory:")
            con.execute(f"SET threads={_cfg['hilos']}")
            con.execute(f"SET memory_limit='{_cfg['memoria']}'")
            if _cfg["fuente"] == "duckdb":
                if not DB.exists():
                    raise FileNotFoundError(f"no existe {DB}")
                con.execute(f"ATTACH '{DB.as_posix()}' AS d (READ_ONLY)")
                con.execute("CREATE VIEW consolidado AS SELECT * FROM d.consolidado")
            else:
                con.execute(f"CREATE VIEW consolidado AS SELECT * FROM read_parquet('{CONS_PARQUET.as_posix()}')")
            _con = con
        return _con


def _registrar(nombre: str, seg: float, filas: int, fuente: str) -> None:
    try:
        LOG_TIEMPOS.parent.mkdir(parents=True, exist_ok=True)
        with LOG_TIEMPOS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "consulta": nombre,
                                "seg": round(seg, 3), "filas": filas, "fuente": fuente,
                                "hilos": _cfg["hilos"], "memoria": _cfg["memoria"]}, ensure_ascii=False) + "\n")
    except OSError:
        pass   # el log nunca debe tumbar un reporte


def consulta(sql: str, nombre: str = "consulta", params: list | None = None) -> pd.DataFrame:
    """Ejecuta SQL con un cursor propio, devuelve DataFrame y registra el tiempo."""
    cur = _conexion().cursor()
    t0 = time.perf_counter()
    try:
        df = cur.execute(sql, params or []).df()
    finally:
        cur.close()
    _registrar(nombre, time.perf_counter() - t0, len(df), _cfg["fuente"])
    return df


def _medida(col_monto: str, serie: str, cond_sem: str) -> str:
    return f"SUM({col_monto}) FILTER (WHERE Serie = '{serie}' AND {cond_sem})"


def agg(grupo: list[str], sem_min: int = 1, sem_max: int | None = None, filtros: dict | None = None,
        monto: str = "cons") -> pd.DataFrame:
    """Agg genérico al vuelo: agrupa por `grupo` y devuelve Real_2025, Plan_2026 y Real_2026.
    `filtros`: {columna: [valores]} (IN). `monto`: 'cons' (constante) o 'loc' (local)."""
    malas = [c for c in list(grupo) + list((filtros or {}).keys()) if c not in COLS_GRUPO]
    if malas:
        raise ValueError(f"columna no permitida: {malas}")
    col = {"cons": "monto_cons", "loc": "monto_loc"}[monto]
    cond = "sem BETWEEN ? AND ?" if sem_max is not None else "sem >= ?"
    params: list = [sem_min] + ([sem_max] if sem_max is not None else [])
    where = [cond]
    for c, vals in (filtros or {}).items():
        where.append(f"{c} IN ({', '.join('?' for _ in vals)})")
        params += list(vals)
    sel = ", ".join(grupo)
    medidas = ", ".join(f"COALESCE(SUM({col}) FILTER (WHERE Serie = '{s}'), 0.0) AS {n}" for s, n in SERIES.items())
    sql = (f"SELECT {sel + ', ' if sel else ''}{medidas} FROM consolidado WHERE {' AND '.join(where)}"
           f"{' GROUP BY ' + sel if sel else ''}")
    return consulta(sql, f"agg[{','.join(grupo)}]", params)


_VENTANAS = {"a": "sem <= {s}", "s": "sem > {s} - {n} AND sem <= {s}", "p": "sem > {s} - 2 * {n} AND sem <= {s} - {n}"}
_PREF = {"R25": "r25", "P26": "pl", "R26": "r26"}


@lru_cache(maxsize=8)
def bases_ppt_raw(sem: int, n: int = 1) -> pd.DataFrame:
    """Lo que la PPT sacaba de aggs/pdc.parquet: por Agrupa 1, División, Territorio, Grupo y Cuenta, las
    medidas acumulada (a), de la ventana (s) y de la ventana anterior (p), por serie. Sin homologar."""
    if sem < 2 * n:
        raise ValueError("se necesita al menos la semana 2 (hay semana anterior)")
    exprs = []
    for serie, pref in _PREF.items():
        for suf, w in _VENTANAS.items():
            exprs.append(f"SUM(monto_cons) FILTER (WHERE Serie = '{serie}' AND {w.format(s=sem, n=n)}) AS {pref}_{suf}")
    sql = f"""
    SELECT COALESCE(cat_Agrupa1, '') AS a1, COALESCE(cat_Direccion_Division, '') AS div,
           COALESCE(cat_Subdireccion_Territorio, '') AS terr, COALESCE(cat_Grupo_de_Cuentas, '') AS gpo,
           COALESCE(cat_Cuentas, '') AS cta, {', '.join(exprs)}
    FROM consolidado WHERE sem <= {int(sem)} GROUP BY ALL"""
    return consulta(sql, f"bases_ppt_raw[sem={sem},n={n}]").fillna(0.0)


@lru_cache(maxsize=8)
def fisicos_ppt(sem: int) -> pd.DataFrame:
    """Lo que total_canales.py sacaba de pdc.parquet: Grupo de Cuentas x semana (sin 'No Clasificado')."""
    sql = f"""
    SELECT COALESCE(cat_Grupo_de_Cuentas, '') AS g, sem,
           SUM(monto_cons) FILTER (WHERE Serie = 'R25') AS r25,
           SUM(monto_cons) FILTER (WHERE Serie = 'P26') AS pl,
           SUM(monto_cons) FILTER (WHERE Serie = 'R26') AS r26
    FROM consolidado WHERE sem <= {int(sem)} AND COALESCE(cat_Subdireccion_Territorio, '') <> 'No Clasificado'
    GROUP BY ALL"""
    return consulta(sql, f"fisicos_ppt[sem={sem}]").fillna(0.0)


def lru_cache_limpiar() -> None:
    bases_ppt_raw.cache_clear()
    fisicos_ppt.cache_clear()


def exportar_parquet(origen: str | pd.DataFrame, ruta: Path, compresion: str = "zstd", nivel: int = 3) -> Path:
    """Guarda un resultado comprimido (zstd por defecto). `origen`: SQL o DataFrame."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    cur = _conexion().cursor()
    try:
        if isinstance(origen, pd.DataFrame):
            cur.register("_df_exportar", origen)
            origen = "SELECT * FROM _df_exportar"
        cur.execute(f"COPY ({origen}) TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION {compresion}, "
                    f"COMPRESSION_LEVEL {int(nivel)})")
    finally:
        cur.close()
    tmp.replace(ruta)       # escritura atómica
    return ruta


# ---------------------------------------------------------------- verificación contra la salida original
def _bases_desde_pdc(pdc_parquet: Path, sem: int, n: int) -> pd.DataFrame:
    """Misma consulta que ppt_semanal/bases.py::construir_bases sobre aggs/pdc.parquet (verdad de referencia)."""
    con = duckdb.connect(":memory:")
    con.execute(f"SET threads={_cfg['hilos']}")
    con.execute(f"SET memory_limit='{_cfg['memoria']}'")
    try:
        q = f"""
        select coalesce(cat_Agrupa1,'') as a1, coalesce(cat_Direccion_Division,'') as div,
               coalesce(cat_Subdireccion_Territorio,'') as terr, coalesce(cat_Grupo_de_Cuentas,'') as gpo,
               coalesce(cat_Cuentas,'') as cta,
               sum(Real_2025_cons) filter (where sem <= {sem}) as r25_a,
               sum(Plan_2026_cons) filter (where sem <= {sem}) as pl_a,
               sum(Real_2026_cons) filter (where sem <= {sem}) as r26_a,
               sum(Real_2025_cons) filter (where sem > {sem - n} and sem <= {sem}) as r25_s,
               sum(Plan_2026_cons) filter (where sem > {sem - n} and sem <= {sem}) as pl_s,
               sum(Real_2026_cons) filter (where sem > {sem - n} and sem <= {sem}) as r26_s,
               sum(Real_2025_cons) filter (where sem > {sem - 2 * n} and sem <= {sem - n}) as r25_p,
               sum(Plan_2026_cons) filter (where sem > {sem - 2 * n} and sem <= {sem - n}) as pl_p,
               sum(Real_2026_cons) filter (where sem > {sem - 2 * n} and sem <= {sem - n}) as r26_p
        from read_parquet('{Path(pdc_parquet).as_posix()}') where sem <= {sem} group by all"""
        return con.execute(q).df().fillna(0.0)
    finally:
        con.close()


def comparar_con_pdc(pdc_parquet: Path, sem: int, n: int = 1) -> dict:
    """Corre ambos caminos, mide tiempos y compara fila por fila."""
    t0 = time.perf_counter(); ref = _bases_desde_pdc(pdc_parquet, sem, n); t_ref = time.perf_counter() - t0
    bases_ppt_raw.cache_clear()
    t0 = time.perf_counter(); nuevo = bases_ppt_raw(sem, n); t_new = time.perf_counter() - t0
    llaves = ["a1", "div", "terr", "gpo", "cta"]
    medidas = [c for c in ref.columns if c not in llaves]
    m = ref.merge(nuevo, on=llaves, how="outer", suffixes=("_ref", "_new"), indicator=True).fillna(0.0)
    solo_ref, solo_new = int((m["_merge"] == "left_only").sum()), int((m["_merge"] == "right_only").sum())
    difs = {c: float((m[f"{c}_ref"] - m[f"{c}_new"]).abs().max()) for c in medidas}
    tot_ref = {c: float(ref[c].sum()) for c in medidas}
    tot_new = {c: float(nuevo[c].sum()) for c in medidas}
    return {"seg_pdc_parquet": round(t_ref, 2), "seg_al_vuelo": round(t_new, 2),
            "filas_ref": len(ref), "filas_nuevo": len(nuevo), "solo_en_ref": solo_ref, "solo_en_nuevo": solo_new,
            "dif_max_por_medida": difs, "total_ref": tot_ref, "total_nuevo": tot_new}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sem", type=int, default=None, help="semana de corte (default: sem_max_real de aggs/_meta.json)")
    ap.add_argument("--n", type=int, default=1, help="ancho de la ventana de la PPT (1 o 4 semanas)")
    ap.add_argument("--fuente", choices=["duckdb", "parquet"], default="duckdb")
    ap.add_argument("--hilos", type=int, default=4)
    ap.add_argument("--memoria", default="4GB")
    ap.add_argument("--bench", action="store_true", help="mide bases_ppt_raw, fisicos_ppt y un agg genérico")
    ap.add_argument("--comparar-pdc", metavar="PDC_PARQUET", help="compara contra la salida original (pdc.parquet)")
    a = ap.parse_args()
    configurar(a.fuente, a.hilos, a.memoria)
    sem = a.sem
    if sem is None:
        sem = json.loads((AGGS / "_meta.json").read_text(encoding="utf-8-sig"))["sem_max_real"]
    print(f"fuente={a.fuente} hilos={a.hilos} memoria={a.memoria} sem={sem} n={a.n}")
    if a.bench:
        for nombre, fn in (("bases_ppt_raw", lambda: bases_ppt_raw(sem, a.n)),
                           ("fisicos_ppt", lambda: fisicos_ppt(sem)),
                           ("agg[cat_Grupo_de_Cuentas,cat_Cuentas]", lambda: agg(["cat_Grupo_de_Cuentas", "cat_Cuentas"], 1, sem))):
            for i in (1, 2):
                lru_cache_limpiar()
                t0 = time.perf_counter(); df = fn(); dt = time.perf_counter() - t0
                print(f"  {nombre:42s} corrida {i}: {dt:6.2f} s · {len(df):>9,} filas")
    if a.comparar_pdc:
        r = comparar_con_pdc(Path(a.comparar_pdc), sem, a.n)
        print(json.dumps(r, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
