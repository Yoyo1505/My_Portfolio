# -*- coding: utf-8 -*-
"""
digitales_sql.py — Datos de canales digitales desde SQL (datos/digitales/{r26,p26,r25}_dig.parquet, bajados con
extraer_digitales.py) con la misma forma que digitales_excel.cargar: listas de `Fila` por canal.

Cifras en millones de pesos, gasto NEGATIVO. Grupo de cuenta = «Grupo de Cuentas» del catálogo
(Catálogo grupo cuentas.xlsx, llave Cta Mayor = ID_CONCEPTO_CUENTA_NIV3). Sin detalle por cuenta ni Total Red/Canales
(no están en las tablas digitales).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import digitales_excel as dx

ROOT = Path(__file__).resolve().parents[2]
DATOS = ROOT / "datos"
MONTO = "SUM_MONTO_MNX_CONS_(SUMA)"
CANAL_POR_NIV5 = {"SHOPINBAZ BAZ SUPERAPP": "shop", "BANCA DIGITAL": "baz", "AURORA DIGITAL": "arg",
                  "DIRECCION GENERAL CANALES DIGITALES": "dgc"}

# (etiqueta de fila, grupos del catálogo que la forman, cuenta si es fila de cuenta)
FILAS = [
    ("Gastos de Nomina", ["Gastos de Nómina"], None),
    ("Gastos de Personal", ["Gastos de Nómina", "Gastos de Personal"], None),
    ("Gastos de Publicidad", ["Gastos de Publicidad"], None),
    ("Gastos de Operación", ["Gastos de Operación"], None),
    ("Renta, Luz y Teléfono", ["Renta, Luz y Teléfono"], None),
    ("Flete Neto", ["Flete Neto"], None),
    ("Impuestos y Derechos", ["Impuestos y Derechos"], None),
    ("Otros Gastos (Productos)", ["Otros Gastos (Productos)"], None),
    ("Servicios Administrativos Back Office", ["Servicios Administrativos Boff"], None),
    ("Gastos Extraordinarios", ["Gastos Extraordinarios"], None),
    ("Gastos Despacho", ["Gastos Despacho"], "184 - GASTOS DESPACHO"),
    ("Costo Activo Fijo", ["Costo Activo Fijo"], "60100 - COSTO ACTIVO FIJO"),
    ("Costo Inventario", ["Costo Inventario"], "60200 - COSTO INVENTARIO"),
]
SUMAN = [g for _, gs, _ in FILAS[2:] for g in gs] + ["Gastos de Nómina", "Gastos de Personal", "Fraude Digital"]


_NIV5 = {"Banca Digital": "BANCA DIGITAL", "Direccion General Canales Digitales": "DIRECCION GENERAL CANALES DIGITALES",
         "Aurora Digital": "AURORA DIGITAL", "Shopinbaz Superapp": "SHOPINBAZ BAZ SUPERAPP"}


def _canales_ceco() -> pd.DataFrame:
    cat = pd.read_excel(DATOS / "Catálogo de estructura.xlsx", sheet_name="Estructura", dtype=str,
                        usecols=["CECO_1", "Agrupa Canales", "Agrupa Canales 2"])
    cat["CECO_1"] = cat["CECO_1"].str.strip()
    cat = cat[cat["Agrupa Canales"].str.strip() == "Digitales"].drop_duplicates("CECO_1")
    cat["Ceco_Niv5"] = cat["Agrupa Canales 2"].str.strip().map(_NIV5)
    return cat.rename(columns={"CECO_1": "ID_CENTRO_COSTOS"})[["ID_CENTRO_COSTOS", "Ceco_Niv5"]]


def _tabla(nombre: str, catalogo: pd.DataFrame) -> pd.DataFrame:
    # DASH APP: los parquets de datos/digitales no traen Ceco_Niv5; el canal sale de «Agrupa Canales 2» del catálogo de estructura.
    d = pd.read_parquet(DATOS / "digitales" / f"{nombre}_dig.parquet",
                        columns=["ID_CENTRO_COSTOS", "ID_CONCEPTO_CUENTA_NIV3", "SEMANA_ARG", MONTO])
    d["ID_CENTRO_COSTOS"] = d["ID_CENTRO_COSTOS"].astype(str).str.strip()
    d = d.merge(_canales_ceco(), on="ID_CENTRO_COSTOS", how="left")
    d = d.rename(columns={MONTO: "monto"})
    d["monto"] = pd.to_numeric(d["monto"]).fillna(0.0)
    d["sem"] = pd.to_numeric(d["SEMANA_ARG"], errors="coerce")
    d["canal"] = d["Ceco_Niv5"].map(CANAL_POR_NIV5)
    d = d.merge(catalogo, on="ID_CONCEPTO_CUENTA_NIV3", how="left")
    return d


def _catalogo() -> pd.DataFrame:
    c = pd.read_excel(DATOS / "Catálogo grupo cuentas.xlsx", sheet_name="Catalogo Gpo de Ctas", dtype=str,
                      usecols=["Cta Mayor", "Grupo de Cuentas"])
    c = c.dropna(subset=["Cta Mayor"]).drop_duplicates("Cta Mayor")
    return c.rename(columns={"Cta Mayor": "ID_CONCEPTO_CUENTA_NIV3", "Grupo de Cuentas": "grupo"})


def cargar_sql(sem: int, avisos: list | None = None) -> dict:
    """{'canales': {k: [Fila]}, 'ventana4': {k: [Fila]}} para la semana `sem` (ventana4 = sem-3..sem, solo .y)."""
    avisos = avisos if avisos is not None else []
    cat = _catalogo()
    r26, p26, r25 = (_tabla(n, cat) for n in ("r26", "p26", "r25"))
    sin_canal = sum(t["canal"].isna().sum() for t in (r26, p26, r25))
    if sin_canal:
        avisos.append(f"{sin_canal} renglones SQL sin canal reconocido (Ceco_Niv5) fueron omitidos")
    desconocidos = {g for t in (r26, p26, r25) for g in t["grupo"].dropna().unique()} - set(SUMAN)
    sin_grupo = sum(t["grupo"].isna().sum() for t in (r26, p26, r25))
    if desconocidos or sin_grupo:
        avisos.append(f"grupos sin fila propia (van a 'Otros (diferencia vs. Total)'): {sorted(desconocidos)}; "
                      f"sin grupo en catálogo: {sin_grupo} renglones")

    def suma(t, canal, grupos, ini, fin):
        m = t[(t["canal"] == canal) & t["grupo"].isin(grupos) & (t["sem"] >= ini) & (t["sem"] <= fin)]
        return -m["monto"].sum() / 1e6

    def total(t, canal, ini, fin):
        m = t[(t["canal"] == canal) & (t["sem"] >= ini) & (t["sem"] <= fin)]
        return -m["monto"].sum() / 1e6

    def fila(cuenta, label, f):
        w = {"r25": f(r25, sem, sem), "semant": f(r26, sem - 1, sem - 1), "plan": f(p26, sem, sem), "real": f(r26, sem, sem)}
        y = {"r25": f(r25, 1, sem), "plan": f(p26, 1, sem), "real": f(r26, 1, sem)}
        return dx.Fila(cuenta, label, w, y, {"plan": 0.0, "semant": 0.0, "r25": 0.0}, {"plan": 0.0, "r25": 0.0})

    def fila4(cuenta, label, f):
        y = {"r25": f(r25, sem - 3, sem), "plan": f(p26, sem - 3, sem), "real": f(r26, sem - 3, sem)}
        return dx.Fila(cuenta, label, {k: 0.0 for k in dx.W}, y, {"plan": 0.0, "semant": 0.0, "r25": 0.0},
                       {"plan": 0.0, "r25": 0.0})

    out = {"canales": {}, "ventana4": {}}
    for k, (_, _, nom) in dx.CANALES.items():
        for destino, mk in (("canales", fila), ("ventana4", fila4)):
            filas = [mk(cta, lab, lambda t, a, b, gs=gs, k=k: suma(t, k, gs, a, b)) for lab, gs, cta in FILAS]
            filas.append(mk(None, f"Total Gasto - {nom}", lambda t, a, b, k=k: total(t, k, a, b)))
            out[destino][k] = filas
    return out
