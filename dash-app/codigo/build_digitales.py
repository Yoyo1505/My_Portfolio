# -*- coding: utf-8 -*-
"""
build_digitales.py — Datos de la pestaña "Canales Digitales" del Dash.

1) Extrae de SQL (R26/P26/R25 DIGITALES) SOLO los CECOs con Agrupa Canales = "Digitales"
   del Catálogo de estructura (llave CECO_1), sin montos en cero -> datos/digitales/{r26,p26,r25}_dig.parquet
2) Arma aggs_digitales/ con el mismo esquema que aggs/ (global, grupo_cuentas, cuentas,
   forecast_cuenta vacío + catálogos JSON/_meta copiados) para que DigitalDataService
   reutilice los mismos cálculos que Resumen.

Uso (desde codigo/):
    python build_digitales.py              # extrae + construye
    python build_digitales.py --solo-build # reusa los parquet de datos/digitales
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import pandas as pd
import polars as pl

BASE = Path(__file__).parent.resolve()
ROOT = BASE.parent
DATOS = ROOT / "datos"
SALIDA = DATOS / "digitales"
AGGS = ROOT / "aggs"
AGGS_DIG = ROOT / "aggs_digitales"

TABLAS = {"R26 DIGITALES": "r26_dig", "P26 DIGITALES": "p26_dig", "R25 DIGITALES": "r25_dig"}
SERIE = {"r26_dig": "R26", "p26_dig": "P26", "r25_dig": "R25"}
MONTO = "SUM_MONTO_MNX_CONS (SUMA)"
MONTO_LOCAL = "Monto Local"
COLS_SQL = ["ID_CENTRO_COSTOS", "ID_CONCEPTO_CUENTA_NIV3", "SEMANA_ARG", MONTO, MONTO_LOCAL, "MES"]
JSONS = ["orden_gpo.json", "responsables_gpo.json", "responsables_cta.json", "ceco_nombres.json",
         "pdv_nombres.json", "riesgos.json", "_meta.json"]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def cecos_digitales() -> list[str]:
    cat = pd.read_excel(DATOS / "Catálogo de estructura.xlsx", sheet_name="Estructura", dtype=str,
                        usecols=["CECO_1", "Agrupa Canales"])
    cat["CECO_1"] = cat["CECO_1"].str.strip()
    return sorted(cat.loc[cat["Agrupa Canales"].str.strip() == "Digitales", "CECO_1"].dropna().unique())


def extraer():
    sys.path.insert(0, str(BASE))
    import extraer_sql as e
    cecos = cecos_digitales()
    log(f"{len(cecos)} CECOs digitales en el catálogo")
    lista = ",".join(f"'{c}'" for c in cecos)
    SALIDA.mkdir(parents=True, exist_ok=True)
    cfg = e.cargar_env()
    cols_sel = ", ".join(f"[{c}]" for c in COLS_SQL)
    for tabla, nombre in TABLAS.items():
        t0 = time.time()
        sql = (f"SELECT {cols_sel} FROM [{tabla}] WHERE LTRIM(RTRIM([ID_CENTRO_COSTOS])) IN ({lista}) "
               f"AND (ISNULL([{MONTO}],0) <> 0 OR ISNULL([{MONTO_LOCAL}],0) <> 0)")
        with e.conectar(cfg, timeout=0) as cn:
            cur = cn.cursor()
            cur.execute(sql)
            cols = [c[0].strip().replace(" ", "_") for c in cur.description]
            df = pd.DataFrame.from_records(cur.fetchall(), columns=cols)
        for c in ("SEMANA_ARG", "SUM_MONTO_MNX_CONS_(SUMA)", "Monto_Local", "MES"):
            df[c] = pd.to_numeric(df[c])
        df["ID_CENTRO_COSTOS"] = df["ID_CENTRO_COSTOS"].astype(str).str.strip()
        df["ID_CONCEPTO_CUENTA_NIV3"] = df["ID_CONCEPTO_CUENTA_NIV3"].astype(str).str.strip()
        df.to_parquet(SALIDA / f"{nombre}.parquet", index=False)
        log(f"{tabla}: {len(df):,} filas · {df['ID_CENTRO_COSTOS'].nunique()} CECOs · "
            f"monto {df['SUM_MONTO_MNX_CONS_(SUMA)'].sum()/1e6:,.2f} M · {time.time()-t0:.0f}s")


def construir():
    AGGS_DIG.mkdir(exist_ok=True)
    # catálogo de cuentas completo (datos/cat_cuentas.parquet, el mismo que usa build_data); una fila
    # por cuenta (keep first) para que el total cuadre con SQL aunque la cuenta tenga split de PosPre
    cat = (pl.scan_parquet(DATOS / "cat_cuentas.parquet")
           .select(["ID_CONCEPTO_CUENTA_NIV3", "cat_Grupo_de_Cuentas", "cat_Cuentas",
                    "cat_CtaMayor_Nombre", "cat_PosPre", "cat_Denom_PosPre"])
           .unique(subset="ID_CONCEPTO_CUENTA_NIV3", keep="first", maintain_order=True).collect())

    partes = []
    for nombre, serie in SERIE.items():
        d = pl.read_parquet(SALIDA / f"{nombre}.parquet")
        partes.append(d.select(
            pl.col("ID_CONCEPTO_CUENTA_NIV3").cast(pl.Utf8),
            pl.col("SEMANA_ARG").cast(pl.Int64).alias("sem"),
            pl.col("MES").cast(pl.Int64).cast(pl.Int32),
            pl.lit(serie).alias("Serie"),
            pl.col("SUM_MONTO_MNX_CONS_(SUMA)").cast(pl.Float64).alias("cons"),
            pl.coalesce(pl.col("Monto_Local").cast(pl.Float64),
                        pl.col("SUM_MONTO_MNX_CONS_(SUMA)").cast(pl.Float64)).alias("loc"),
        ))
    u = pl.concat(partes).join(cat, on="ID_CONCEPTO_CUENTA_NIV3", how="left")
    sin_cat = u.filter(pl.col("cat_Cuentas").is_null())
    if sin_cat.height:
        log(f"  [AVISO] {sin_cat['ID_CONCEPTO_CUENTA_NIV3'].n_unique()} cuentas sin catálogo "
            f"({sin_cat['cons'].sum()/1e6:,.2f} M) -> '(Sin dato)'")
    u = u.with_columns([pl.col(c).fill_null("(Sin dato)") for c in
                        ("cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_CtaMayor_Nombre", "cat_PosPre", "cat_Denom_PosPre")])
    u = u.with_columns(pl.lit("Digitales").alias("cat_Agrupa1"), pl.lit("Digitales").alias("cat_Subtipo"))

    medidas = []
    for suf in ("cons", "loc"):
        for serie, nom in (("R25", "Real_2025"), ("P26", "Plan_2026"), ("R26", "Real_2026")):
            col = "cons" if suf == "cons" else "loc"
            medidas.append(pl.when(pl.col("Serie") == serie).then(pl.col(col)).otherwise(0.0).sum()
                           .alias(f"{nom}_{suf}"))
    orden_med = [f"{n}_{s}" for s in ("cons", "loc") for n in ("Real_2025", "Plan_2026", "Real_2026")]
    orden_med = ["Real_2025_cons", "Plan_2026_cons", "Real_2026_cons", "Real_2025_loc", "Plan_2026_loc", "Real_2026_loc"]

    def agg(claves, destino):
        out = u.group_by(claves).agg(medidas).select(claves + orden_med)
        out.write_parquet(AGGS_DIG / destino, compression="zstd")
        log(f"  {destino}: {out.height:,} filas")
        return out

    k_gpo = ["cat_Grupo_de_Cuentas", "cat_Agrupa1", "MES", "cat_Subtipo", "ID_CONCEPTO_CUENTA_NIV3",
             "cat_CtaMayor_Nombre", "sem"]
    k_cta = ["cat_Cuentas", "cat_Agrupa1", "cat_Grupo_de_Cuentas", "MES", "cat_Subtipo", "ID_CONCEPTO_CUENTA_NIV3",
             "cat_CtaMayor_Nombre", "cat_PosPre", "cat_Denom_PosPre", "sem"]
    agg(k_gpo, "grupo_cuentas.parquet")
    agg(k_gpo, "global.parquet")
    agg(k_cta, "cuentas.parquet")

    # Forecast Finanzas digital: lo deja build_data.build_forecast_cuenta() en aggs/forecast_cuenta_dig.parquet
    # (Excel "Forecast Finanzas 2026"); si aún no existe, parquet vacío con el esquema esperado
    if (AGGS / "forecast_cuenta_dig.parquet").exists():
        shutil.copy2(AGGS / "forecast_cuenta_dig.parquet", AGGS_DIG / "forecast_cuenta.parquet")
    else:
        pl.DataFrame(schema={"ID_CONCEPTO_CUENTA_NIV3": pl.Utf8, "sem": pl.Int64, "Forecast": pl.Float64,
                             "cat_Grupo_de_Cuentas": pl.Utf8, "cat_Cuentas": pl.Utf8,
                             "cat_Agrupador_Reales": pl.Utf8, "cat_CtaMayor_Nombre": pl.Utf8}
                     ).write_parquet(AGGS_DIG / "forecast_cuenta.parquet")
    for j in JSONS:
        if (AGGS / j).exists():
            shutil.copy2(AGGS / j, AGGS_DIG / j)

    tot = u.group_by("Serie").agg(pl.col("cons").sum() / 1e6)
    log("  totales (M): " + ", ".join(f"{r['Serie']}={r['cons']:,.1f}" for r in tot.sort("Serie").to_dicts()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-build", action="store_true", help="no extrae de SQL; usa datos/digitales")
    args = ap.parse_args()
    if not args.solo_build:
        extraer()
    construir()
    log("[OK] aggs_digitales listo")


if __name__ == "__main__":
    main()
