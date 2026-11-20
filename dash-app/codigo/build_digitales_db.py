# -*- coding: utf-8 -*-
"""
build_digitales_db.py — Canal Digital completo para el tablero (2026-10-21).

Escribe aggs/digitales.duckdb (el tablero lo ATTACH-ea en solo lectura junto a dashboard.duckdb):
  consolidado_dig : datos/digitales/{r25,p26,r26}_dig.parquet + catálogos de cuentas/estructura, con el mismo esquema
                    que `consolidado` (CECO, PDV, División, Contraloría...). Agrupa1/Subtipo = 'Digitales', igual que
                    los dig_* de build_digitales.py.
  dig_<nombre>    : los agregados que el canal digital no tenía (division, agrupa2/3, agrupadores_reales,
                    contraloria, cierres_arbol, cierres_expansion). Se derivan de consolidado_dig con las MISMAS
                    columnas que su agg_<nombre> del dashboard.duckdb.
Los datos digitales son chicos (<1 MB de parquet): corre en segundos y no toca dashboard.duckdb, así que se puede
ejecutar con el tablero abierto (después reinicia el tablero para que lo vea).

Uso (desde codigo/):  python build_digitales_db.py
Deja tiempos por etapa en codigo/_tiempos_digitales.log.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb

BASE = Path(__file__).parent.resolve()
ROOT = BASE.parent
sys.path.insert(0, str(BASE))

AGGS = ROOT / "aggs"
SALIDA = ROOT / "datos" / "digitales"
DB_DIG = AGGS / "digitales.duckdb"
LOG = BASE / "_tiempos_digitales.log"
FALTANTES = ["division", "agrupa2", "agrupa3", "agrupadores_reales", "contraloria", "cierres_arbol"]
T0 = time.time()
_t = [time.time()]


def paso(msg: str):
    ahora = time.time()
    linea = f"[{time.strftime('%H:%M:%S')}] +{ahora - _t[0]:6.1f}s (total {ahora - T0:6.1f}s) {msg}"
    _t[0] = ahora
    print(linea, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def main():
    import build_data as bd
    from catalogos import obtener_cat_cuentas, obtener_cat_estructura
    import polars as pl

    paso("inicio build_digitales_db")
    cta = obtener_cat_cuentas()
    est = obtener_cat_estructura()
    paso(f"catálogos leídos (cuentas {cta.height:,} · estructura {est.height:,})")

    tmp = DB_DIG.with_suffix(".nuevo")
    for p in (tmp, tmp.with_suffix(".nuevo.wal")):
        p.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute("SET threads=4; SET memory_limit='3GB'")
    con.register("cat_c", cta.to_arrow())
    con.register("cat_e", est.to_arrow())

    sel = ",\n            ".join(
        [f'COALESCE(c."{a}", \'No Clasificado\') AS "{a}"' for _, a in bd.COLS_CUENTAS]
        + [f'COALESCE(e."{a}", \'No Clasificado\') AS "{a}"' for _, a in bd.COLS_ESTRUCT])
    partes = " UNION ALL ".join(
        f"""SELECT TRIM(CAST(ID_CENTRO_COSTOS AS VARCHAR)) AS ID_CENTRO_COSTOS,
                   TRIM(CAST(ID_CONCEPTO_CUENTA_NIV3 AS VARCHAR)) AS ID_CONCEPTO_CUENTA_NIV3,
                   CAST(SEMANA_ARG AS INTEGER) AS sem, '{serie}' AS Serie,
                   CAST("SUM_MONTO_MNX_CONS_(SUMA)" AS DOUBLE) AS monto_cons,
                   CAST(COALESCE(TRY_CAST("Monto_Local" AS DOUBLE), "SUM_MONTO_MNX_CONS_(SUMA)") AS DOUBLE) AS monto_loc,
                   TRY_CAST(MES AS INTEGER) AS MES
            FROM read_parquet('{(SALIDA / (n + '.parquet')).as_posix()}')"""
        for n, serie in (("r25_dig", "R25"), ("p26_dig", "P26"), ("r26_dig", "R26")))
    con.execute(f"""
        CREATE TABLE consolidado_dig AS
        SELECT d.sem, d.Serie, d.monto_cons, d.monto_loc, d.ID_CENTRO_COSTOS, d.ID_CONCEPTO_CUENTA_NIV3,
               COALESCE(e."ECO_PDV", 0) AS ECO_PDV, d.MES,
               {sel},
               CASE WHEN d.sem<=13 THEN 'Q1' WHEN d.sem<=26 THEN 'Q2' WHEN d.sem<=39 THEN 'Q3' ELSE 'Q4' END AS TRIMESTRE
        FROM ({partes}) d
        LEFT JOIN cat_c c ON d.ID_CONCEPTO_CUENTA_NIV3 = c.ID_CONCEPTO_CUENTA_NIV3
        LEFT JOIN cat_e e ON d.ID_CENTRO_COSTOS = e.ID_CENTRO_COSTOS
    """)
    # mismo criterio que build_digitales.construir(): el canal digital se muestra como un solo bloque
    con.execute("UPDATE consolidado_dig SET cat_Agrupa1 = 'Digitales', cat_Subtipo = 'Digitales'")
    n = con.execute("SELECT COUNT(*) FROM consolidado_dig").fetchone()[0]
    paso(f"consolidado_dig: {n:,} filas")

    # columnas de cada agg físico (para derivar el digital con las mismas llaves)
    ref = duckdb.connect(str(AGGS / "dashboard.duckdb"), read_only=True)
    cols_dig = {r[0] for r in con.execute("DESCRIBE consolidado_dig").fetchall()}
    for nombre in FALTANTES:
        cols = [r[0] for r in ref.execute(f"DESCRIBE agg_{nombre}").fetchall()]
        llaves = [c for c in cols if not c.endswith(("_cons", "_loc"))]
        faltan = [c for c in llaves if c not in cols_dig]
        if faltan:
            paso(f"  dig_{nombre}: OMITIDO (columnas que consolidado_dig no tiene: {faltan})")
            continue
        g = ", ".join(llaves)
        meds = ", ".join(
            f"SUM(CASE WHEN Serie = '{s}' THEN monto_{suf} ELSE 0 END) AS {nom}_{suf}"
            for suf in ("cons", "loc") for s, nom in (("R25", "Real_2025"), ("P26", "Plan_2026"), ("R26", "Real_2026")))
        orden = ", ".join(f"{nom}_{suf}" for suf in ("cons", "loc") for nom in ("Real_2025", "Plan_2026", "Real_2026"))
        con.execute(f"CREATE TABLE dig_{nombre} AS SELECT {g}, {meds} FROM consolidado_dig GROUP BY {g}")
        filas = con.execute(f"SELECT COUNT(*) FROM dig_{nombre}").fetchone()[0]
        paso(f"  dig_{nombre}: {filas:,} filas")
    ref.close()

    # Cierres_expansion trae 'Categoria' (Master Cierres por PDC): no aplica a digitales -> no se genera.
    r = con.execute("""SELECT Serie, ROUND(SUM(monto_cons)/1e6, 2) FROM consolidado_dig GROUP BY 1 ORDER BY 1""").fetchall()
    paso("totales consolidado_dig (M): " + ", ".join(f"{s}={m:,.2f}" for s, m in r))
    con.execute("CHECKPOINT")
    con.close()
    DB_DIG.unlink(missing_ok=True)
    tmp.replace(DB_DIG)
    paso(f"[OK] {DB_DIG.name} listo ({DB_DIG.stat().st_size / 1e6:,.1f} MB)")


if __name__ == "__main__":
    main()
