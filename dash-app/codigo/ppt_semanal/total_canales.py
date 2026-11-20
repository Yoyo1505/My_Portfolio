# -*- coding: utf-8 -*-
"""
total_canales.py — Filas de CANALES FÍSICOS (aggs/pdc.parquet) con la misma forma que digitales_sql.cargar_sql,
para sumarlas con digitales (digitales_ppt.sumar) y armar la pestaña «Físicos + Digitales» de la consolidada.

Cifras en millones de pesos constantes, gasto NEGATIVO. Grupos = «Grupo de Cuentas» del catálogo (los mismos
nombres que usa digitales_sql.FILAS). Excluye el territorio «No Clasificado» igual que la tarjeta KPI de físicos.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from . import digitales_excel as dx
from .digitales_sql import FILAS

ROOT = Path(__file__).resolve().parents[2]
from .fuente import PDC, conectar   # DASH APP: consolidado de aggs/dashboard.duckdb en lugar de pdc.parquet
SIN_FILA = "No Clasificado"
NOMBRE = "Canales Físicos"
CERO = {"plan": 0.0, "semant": 0.0, "r25": 0.0}


def cargar_fisicos(sem: int) -> tuple[list[dx.Fila], list[dx.Fila]]:
    """(filas semana/acumulado, filas ventana de 4 semanas) de físicos; mismo layout que digitales_sql."""
    q = f"""
    select coalesce(cat_Grupo_de_Cuentas,'') g, sem,
           sum(Real_2025_cons) r25, sum(Plan_2026_cons) pl, sum(Real_2026_cons) r26
    from {PDC}
    where sem <= {sem} and coalesce(cat_Subdireccion_Territorio,'') <> '{SIN_FILA}'
    group by all
    """
    con = conectar()
    try:
        d = con.execute(q).df().fillna(0.0)
    finally:
        con.close()

    def suma(col, grupos, ini, fin):
        m = d[(d["sem"] >= ini) & (d["sem"] <= fin)]
        if grupos is not None:
            m = m[m["g"].isin(grupos)]
        return -float(m[col].sum()) / 1e6

    def fila(cuenta, label, grupos):
        w = {"r25": suma("r25", grupos, sem, sem), "semant": suma("r26", grupos, sem - 1, sem - 1),
             "plan": suma("pl", grupos, sem, sem), "real": suma("r26", grupos, sem, sem)}
        y = {"r25": suma("r25", grupos, 1, sem), "plan": suma("pl", grupos, 1, sem), "real": suma("r26", grupos, 1, sem)}
        return dx.Fila(cuenta, label, w, y, dict(CERO), {"plan": 0.0, "r25": 0.0})

    def fila4(cuenta, label, grupos):
        y = {"r25": suma("r25", grupos, sem - 3, sem), "plan": suma("pl", grupos, sem - 3, sem),
             "real": suma("r26", grupos, sem - 3, sem)}
        return dx.Fila(cuenta, label, {k: 0.0 for k in dx.W}, y, dict(CERO), {"plan": 0.0, "r25": 0.0})

    out = []
    for mk in (fila, fila4):
        filas = [mk(cta, lab, gs) for lab, gs, cta in FILAS]
        filas.append(mk(None, f"Total Gasto - {NOMBRE}", None))
        out.append(filas)
    return out[0], out[1]
