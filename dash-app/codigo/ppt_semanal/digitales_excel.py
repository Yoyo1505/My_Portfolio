# -*- coding: utf-8 -*-
"""
digitales_excel.py — Lee las hojas 8C / Temporalidad de canales digitales del Excel oficial
("Gastos Red GARG (sem NN)...xlsx") y deja los datos listos para la presentación.

Cifras en millones de pesos, gasto NEGATIVO (como el Excel). Las variaciones se recalculan
(Real - comparativo; positivo = favorable) y se contrastan con las que trae el Excel.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl

CANALES = {                       # clave -> (hoja 8C, hoja Temporalidad, nombre)
    "shop": ("8C (Shop)", "Temporalidad (Shop)", "Shopinbaz"),
    "baz": ("8C (BAZ D)", "Temporalidad (BAZ D)", "Baz Digital"),
    "arg": ("8C (ARG D)", "Temporalidad (ARG D)", "Aurora Digital"),
    "dgc": ("8C (DGCDig)", "Temporalidad (DGCDig)", "DG Canales Digitales"),
}
HOJA_CANALES = "8C Canales"
HOJA_TEMP_CANALES = "Temporalidad (Canales)"

W = ("r25", "semant", "plan", "real")      # semana
Y = ("r25", "plan", "real")                # acumulado


def _n(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


class Fila:
    __slots__ = ("cuenta", "label", "w", "y", "xw", "xy", "es_grupo")

    def __init__(self, cuenta, label, w, y, xw, xy):
        self.cuenta, self.label, self.w, self.y, self.xw, self.xy = cuenta, label, w, y, xw, xy
        self.es_grupo = not cuenta


def leer_8c(ws) -> list[Fila]:
    rows = list(ws.iter_rows(min_row=1, max_row=200, max_col=75, values_only=True))
    hdr = next(i for i, r in enumerate(rows[:15]) if any(c == "YTD" for c in r))
    y0 = next(j for j, c in enumerate(rows[hdr]) if c == "YTD")
    b0 = next(j for j, c in enumerate(rows[hdr]) if isinstance(c, str) and c.startswith("SEMANA "))
    ini = next(i for i in range(hdr, hdr + 8) if rows[i][b0] == "$") + 1
    out = []
    for r in rows[ini:]:
        lab = r[b0 + 10]
        if lab is None and r[0] is None:
            continue
        if lab is None:
            continue
        cuenta = r[0] if isinstance(r[0], str) else None
        w = {"r25": _n(r[b0]), "semant": _n(r[b0 + 2]), "plan": _n(r[b0 + 4]), "real": _n(r[b0 + 8])}
        y = {"r25": _n(r[y0]), "plan": _n(r[y0 + 2]), "real": _n(r[y0 + 6])}
        # variaciones del Excel (para contraste): plan, sem ant, año ant
        xw = {"plan": _n(r[b0 + 13]), "semant": _n(r[b0 + 15]), "r25": _n(r[b0 + 17])}
        xy = {"plan": _n(r[y0 + 11]), "r25": _n(r[y0 + 13])}
        out.append(Fila(cuenta, str(lab).strip(), w, y, xw, xy))
    return out


def leer_temp(ws) -> dict[str, list[float]]:
    """Filas 'Semana / YTG / Anual' del primer bloque (Total Gasto), en miles -> devuelve millones."""
    rows = list(ws.iter_rows(min_row=1, max_row=40, max_col=14, values_only=True))
    hdr = next(i for i, r in enumerate(rows) if r[2] == "Periodo")
    res = {}
    for r in rows[hdr + 2: hdr + 5]:
        if r[2] is None:
            continue
        # [Real 2025, Plan, Forecast, Real 2026] en millones, luego var vs plan $, %, var vs 2025 $, %
        res[str(r[2])] = {"r25": _n(r[3]) / 1000, "plan": _n(r[4]) / 1000, "fcst": _n(r[6]) / 1000,
                          "real": _n(r[7]) / 1000, "vp": _n(r[9]) / 1000, "vp_pct": _n(r[10]),
                          "v25": _n(r[11]) / 1000, "v25_pct": _n(r[12])}
    return res


def cargar(xlsx: Path) -> dict:
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    d = {"canales": {}, "temp": {}}
    for k, (h8, ht, nom) in CANALES.items():
        if h8 not in wb.sheetnames:                  # Excel de semanas anteriores sin ese canal
            d["canales"][k], d["temp"][k] = [], {}
            continue
        d["canales"][k] = leer_8c(wb[h8])
        d["temp"][k] = leer_temp(wb[ht])
    d["canales_total"] = leer_8c(wb[HOJA_CANALES])
    d["temp"]["canales"] = leer_temp(wb[HOJA_TEMP_CANALES])
    wb.close()
    return d


if __name__ == "__main__":
    import sys
    d = cargar(Path(sys.argv[1]))
    for k, filas in d["canales"].items():
        print(k, len(filas), [f.label for f in filas if f.es_grupo])
    print(d["temp"]["shop"])
    print(d["temp"]["canales"])
