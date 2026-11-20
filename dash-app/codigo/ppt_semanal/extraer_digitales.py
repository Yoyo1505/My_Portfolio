# -*- coding: utf-8 -*-
"""
extraer_digitales.py — Baja de SQL (R26/P26/R25 DIGITALES) SOLO los CECOs de canales digitales y
sin montos en cero/vacíos. La lista de CECOs sale del Catálogo de estructura (Agrupa Canales = Digitales,
llave CECO_1). Salida: datos/digitales/{r26,p26,r25}_dig.parquet

Uso (desde codigo/):  python -m ppt_semanal.extraer_digitales
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATOS = ROOT / "datos"
SALIDA = DATOS / "digitales"
TABLAS = {"R26 DIGITALES": "r26_dig", "P26 DIGITALES": "p26_dig", "R25 DIGITALES": "r25_dig"}
MONTO = "SUM_MONTO_MNX_CONS (SUMA)"
MONTO_LOCAL = "Monto Local"


def cecos_digitales() -> list[str]:
    cat = pd.read_excel(DATOS / "Catálogo de estructura.xlsx", sheet_name="Estructura", dtype=str,
                        usecols=["CECO_1", "Agrupa Canales"])
    cat["CECO_1"] = cat["CECO_1"].str.strip()
    return sorted(cat.loc[cat["Agrupa Canales"].str.strip() == "Digitales", "CECO_1"].dropna().unique())


NIV5 = {"Banca Digital": "BANCA DIGITAL", "Direccion General Canales Digitales": "DIRECCION GENERAL CANALES DIGITALES",
        "Aurora Digital": "AURORA DIGITAL", "Shopinbaz Superapp": "SHOPINBAZ BAZ SUPERAPP"}


def desde_parquets(carpeta: Path, sem: int) -> None:
    """Las tablas R26/P26/R25 ya traen físicos y digitales juntos (DASH APP/datos/{r26,p26,r25}_semNN.parquet):
    se filtran con el catálogo de estructura (Agrupa Canales = Digitales) y el canal sale de «Agrupa Canales 2»."""
    import duckdb
    cat = pd.read_excel(DATOS / "Catálogo de estructura.xlsx", sheet_name="Estructura", dtype=str,
                        usecols=["CECO_1", "Agrupa Canales", "Agrupa Canales 2", "Entidad"])
    cat["CECO_1"] = cat["CECO_1"].str.strip()
    cat = cat[cat["Agrupa Canales"].str.strip() == "Digitales"].drop_duplicates("CECO_1")
    cat["Ceco_Niv5"] = cat["Agrupa Canales 2"].str.strip().map(NIV5)
    if cat["Ceco_Niv5"].isna().any():
        raise SystemExit(f"CECOs digitales sin canal en 'Agrupa Canales 2': {cat.loc[cat['Ceco_Niv5'].isna(), 'CECO_1'].tolist()}")
    cat = cat.rename(columns={"CECO_1": "ceco", "Entidad": "Entidad_Ceco"})[["ceco", "Ceco_Niv5", "Entidad_Ceco"]]
    SALIDA.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("PRAGMA threads=2; PRAGMA memory_limit='2GB'")      # equipo compartido: poca carga
    con.register("cat", cat)
    for origen, nombre in (("r26", "r26_dig"), ("p26", "p26_dig"), ("r25", "r25_dig")):
        t0 = time.time()
        src = Path(carpeta) / f"{origen}_sem{sem}.parquet"
        out = SALIDA / f"{nombre}.parquet"
        con.execute(f"""COPY (
            select trim(t.ID_CENTRO_COSTOS) as ID_CENTRO_COSTOS, t.ID_CONCEPTO_CUENTA_NIV3, t.SEMANA_ARG,
                   cast(t."SUM_MONTO_MNX_CONS_(SUMA)" as double) as "SUM_MONTO_MNX_CONS_(SUMA)",
                   cast(t.Monto_Local as double) as Monto_Local, cast(t.MES as double) as MES,
                   c.Ceco_Niv5, c.Entidad_Ceco
            from read_parquet('{src.as_posix()}') t join cat c on trim(t.ID_CENTRO_COSTOS) = c.ceco
            where coalesce(t."SUM_MONTO_MNX_CONS_(SUMA)",0) <> 0 or coalesce(t.Monto_Local,0) <> 0
        ) TO '{out.as_posix()}' (FORMAT parquet)""")
        n, m, k = con.execute(f"select count(*), sum(\"SUM_MONTO_MNX_CONS_(SUMA)\")/1e6, count(distinct ID_CENTRO_COSTOS) "
                              f"from read_parquet('{out.as_posix()}')").fetchone()
        print(f"{origen}: {n:,} filas · {k} CECOs · monto {m:,.2f} M · {time.time()-t0:.0f}s")


def main():
    if "--desde" in sys.argv:                    # python -m ppt_semanal.extraer_digitales --desde <carpeta> <sem>
        i = sys.argv.index("--desde")
        return desde_parquets(Path(sys.argv[i + 1]), int(sys.argv[i + 2]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import extraer_sql as e                      # conexión y credenciales del proyecto
    cecos = cecos_digitales()
    print(f"{len(cecos)} CECOs digitales en el catálogo")
    lista = ",".join(f"'{c}'" for c in cecos)
    SALIDA.mkdir(parents=True, exist_ok=True)
    cfg = e.cargar_env()
    for tabla, nombre in TABLAS.items():
        t0 = time.time()
        # monto no nulo y distinto de cero (consolidado o local)
        sql = (f"SELECT * FROM [{tabla}] WHERE LTRIM(RTRIM([ID_CENTRO_COSTOS])) IN ({lista}) "
               f"AND (ISNULL([{MONTO}],0) <> 0 OR ISNULL([{MONTO_LOCAL}],0) <> 0)")
        with e.conectar(cfg, timeout=0) as cn:
            cur = cn.cursor()
            cur.execute(sql)
            cols = [c[0].strip().replace(" ", "_") for c in cur.description]
            df = pd.DataFrame.from_records(cur.fetchall(), columns=cols)
        for c in df.columns:
            if df[c].dtype == object and c not in ("ID_CENTRO_COSTOS", "ID_CONCEPTO_CUENTA_NIV3", "#_SEMANA"):
                df[c] = pd.to_numeric(df[c])
        df["ID_CENTRO_COSTOS"] = df["ID_CENTRO_COSTOS"].str.strip()
        df.to_parquet(SALIDA / f"{nombre}.parquet", index=False)
        print(f"{tabla}: {len(df):,} filas · {df['ID_CENTRO_COSTOS'].nunique()} CECOs · "
              f"monto {df['SUM_MONTO_MNX_CONS_(SUMA)'].sum()/1e6:,.2f} M · {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
