# -*- coding: utf-8 -*-
"""
bases.py — Arma las tres bases del Excel de la presentación (acumulado, semana, semana anterior).

Columnas de salida (idénticas a las del Excel, incluido el punto de "Real 2025."):
    Agrupa 1 | División | Subdirección / Territorio | Grupo de Cuentas | Cuentas |
    Real 2025. | Plan 2026 | Real 2026 | Grupo

Origen: aggs/pdc.parquet (lo regenera build_data.py). Cifras en pesos constantes
(`*_cons`), en MILES y con el signo del Excel (gasto = negativo): valor = -monto/1000.
Así las fórmulas `-SUMIFS(...)/1000` de la plantilla dan millones sin tocarlas.

Dos homologaciones que el Excel traía a mano y que se validaron fila por fila
contra la presentación de la semana 39:
  1. Territorio "Sin agrupar" (filas de Staff de cada División) -> el nombre de la División.
  2. Grupo de cuentas "Renta, Luz y Teléfono" -> se abre en Renta / Luz / Teléfono (la cuenta).

La columna `Grupo` sale de cruzar `Subdirección / Territorio` con el catálogo
(equivale al VLOOKUP del Excel). Un territorio sin catálogo con importe distinto
de cero detiene el proceso: el VLOOKUP lo dejaba en "" y el importe desaparecía
de todas las láminas sin avisar.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNAS = ["Agrupa 1", "División", "Subdirección / Territorio", "Grupo de Cuentas",
            "Cuentas", "Real 2025.", "Plan 2026", "Real 2026", "Grupo"]
LETRAS = dict(zip(COLUMNAS, "ABCDEFGHI"))

SIN_AGRUPAR = "Sin agrupar"
GRUPO_QUE_SE_ABRE = {"Renta, Luz y Teléfono"}      # se reemplaza por el nombre de la cuenta
EPS_IMPORTE = 0.5                                   # miles de pesos: por debajo se considera cero


def cargar_catalogo(ruta: Path) -> dict[str, str]:
    """{territorio en minúsculas: grupo}. Acepta CSV/TSV con encabezado Territorio,Grupo,Nombre."""
    ruta = Path(ruta)
    txt = ruta.read_text(encoding="utf-8-sig")
    dialecto = csv.excel_tab if "\t" in txt.splitlines()[0] else csv.excel
    filas = list(csv.DictReader(txt.splitlines(), dialect=dialecto))
    cat: dict[str, str] = {}
    for f in filas:
        t, g = (f.get("Territorio") or "").strip(), (f.get("Grupo") or "").strip()
        if not t or not g:
            raise ValueError(f"catálogo con fila incompleta: {f}")
        if t.lower() in cat:
            raise ValueError(f"catálogo con territorio duplicado: {t}")
        cat[t.lower()] = g
    if not cat:
        raise ValueError(f"catálogo vacío: {ruta}")
    return cat


def _homologar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    sin = df["Subdirección / Territorio"] == SIN_AGRUPAR
    df.loc[sin, "Subdirección / Territorio"] = df.loc[sin, "División"]
    abre = df["Grupo de Cuentas"].isin(GRUPO_QUE_SE_ABRE)
    df.loc[abre, "Grupo de Cuentas"] = df.loc[abre, "Cuentas"]
    llaves = COLUMNAS[:5]
    return df.groupby(llaves, as_index=False, sort=True)[["Real 2025.", "Plan 2026", "Real 2026"]].sum()


def _cruzar_catalogo(df: pd.DataFrame, cat: dict[str, str], nombre: str) -> pd.DataFrame:
    df = df.copy()
    df["Grupo"] = df["Subdirección / Territorio"].str.lower().map(cat).fillna("")
    huerfanas = df[(df["Grupo"] == "")]
    peso = huerfanas[["Real 2025.", "Plan 2026", "Real 2026"]].abs().sum(axis=1)
    graves = huerfanas[peso > EPS_IMPORTE]
    if len(graves):
        top = (graves.groupby("Subdirección / Territorio")[["Real 2025.", "Plan 2026", "Real 2026"]]
               .sum().round(1).to_string())
        raise ValueError(
            f"[{nombre}] territorios sin catálogo con importe (se perderían de las láminas):\n{top}\n"
            "Agrégalos a ppt_semanal/catalogo_territorio.csv")
    return df[df["Grupo"] != ""].reset_index(drop=True) if len(huerfanas) else df


def construir_bases(pdc_parquet: Path, sem: int, catalogo: dict[str, str], n: int = 1) -> dict[str, pd.DataFrame]:
    """Devuelve {'BD Acumulado', 'BD Semana', 'BD Semana_<n-1>'} -> DataFrame con COLUMNAS.\n    n = semanas de la ventana «Semana» (1 = la semana; 4 = últimas 4 semanas acumuladas). La ventana «anterior»\n    son las n semanas inmediatas previas."""
    import duckdb

    if sem < 2 * n:
        raise ValueError("se necesita al menos la semana 2 (hay semana anterior)")
    from .fuente import PDC, conectar
    con = conectar()   # DASH APP: tabla consolidado de aggs/dashboard.duckdb (el parámetro pdc_parquet ya no se usa)
    try:
        # una sola lectura de la fuente para las tres vistas
        q = f"""
        select coalesce(cat_Agrupa1,'')                 as a1,
               coalesce(cat_Direccion_Division,'')       as div,
               coalesce(cat_Subdireccion_Territorio,'')  as terr,
               coalesce(cat_Grupo_de_Cuentas,'')         as gpo,
               coalesce(cat_Cuentas,'')                  as cta,
               sum(Real_2025_cons) filter (where sem <= {sem})      as r25_a,
               sum(Plan_2026_cons) filter (where sem <= {sem})      as pl_a,
               sum(Real_2026_cons) filter (where sem <= {sem})      as r26_a,
               sum(Real_2025_cons) filter (where sem > {sem - n} and sem <= {sem})      as r25_s,
               sum(Plan_2026_cons) filter (where sem > {sem - n} and sem <= {sem})      as pl_s,
               sum(Real_2026_cons) filter (where sem > {sem - n} and sem <= {sem})      as r26_s,
               sum(Real_2025_cons) filter (where sem > {sem - 2 * n} and sem <= {sem - n})  as r25_p,
               sum(Plan_2026_cons) filter (where sem > {sem - 2 * n} and sem <= {sem - n})  as pl_p,
               sum(Real_2026_cons) filter (where sem > {sem - 2 * n} and sem <= {sem - n})  as r26_p
        from {PDC}
        where sem <= {sem}
        group by all
        """
        base = con.execute(q).df().fillna(0.0)
    finally:
        con.close()

    out: dict[str, pd.DataFrame] = {}
    vistas = [("BD Acumulado", "a"), ("BD Semana", "s"), (f"BD Semana_{sem - 1}", "p")]
    for nombre, suf in vistas:
        d = pd.DataFrame({
            "Agrupa 1": base["a1"], "División": base["div"], "Subdirección / Territorio": base["terr"],
            "Grupo de Cuentas": base["gpo"], "Cuentas": base["cta"],
            "Real 2025.": -base[f"r25_{suf}"] / 1000.0,
            "Plan 2026": -base[f"pl_{suf}"] / 1000.0,
            "Real 2026": -base[f"r26_{suf}"] / 1000.0,
        })
        d = _homologar(d)
        d = _cruzar_catalogo(d, catalogo, nombre)
        out[nombre] = d[COLUMNAS]
    return out


def a_tabla(df: pd.DataFrame):
    """DataFrame -> Table de xl_eval (columnas por letra A..I)."""
    from .xl_eval import Table
    cols = {}
    for nombre, letra in LETRAS.items():
        if nombre in ("Real 2025.", "Plan 2026", "Real 2026"):
            cols[letra] = df[nombre].to_numpy(dtype=float)
        else:
            cols[letra] = df[nombre].astype(str).to_numpy(dtype=object)
    return Table(cols)


def leer_bases_excel(xlsx: Path) -> dict[str, pd.DataFrame]:
    """Lee las BD de un Excel PSA ya armado (para pruebas de oro). Usa los valores en caché."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, data_only=True, read_only=True)
    res = {}
    for nombre in wb.sheetnames:
        if not nombre.startswith("BD "):
            continue
        filas = []
        for r in wb[nombre].iter_rows(min_row=2, max_col=9, values_only=True):
            if r[0] is None and r[1] is None and r[2] is None:
                break
            filas.append(r)
        d = pd.DataFrame(filas, columns=COLUMNAS)
        for c in ("Real 2025.", "Plan 2026", "Real 2026"):
            d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0.0)
        for c in ("Agrupa 1", "División", "Subdirección / Territorio", "Grupo de Cuentas", "Cuentas", "Grupo"):
            d[c] = d[c].fillna("").astype(str)
        res[nombre] = d
    wb.close()
    return res
