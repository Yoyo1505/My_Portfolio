# -*- coding: utf-8 -*-
"""
codigo/extraer_arbol_ceco.py — Árbol real de Centros de Costos -> datos/cat_arbol.parquet

Lee Estructura_RGARG_IE.xlsx (hoja 1): la jerarquía está en la sangría (columnas B..T, una por nivel).
Por cada CECO guarda su rama padre inmediata (la rama más chica que lo contiene) y la ruta
desde el nivel 6 (Canales) hacia abajo. Las señales de sobregasto usan esa rama para no
quedarse en nodos grandes como "Concentrador".

Uso: python codigo/extraer_arbol_ceco.py [ruta_excel]
"""
import sys
from pathlib import Path

import openpyxl
import pandas as pd

BASE_DIR = Path(__file__).parent.parent.resolve()
EXCEL = (r"<SHARED_DRIVE>"
         r"\99. Catálogos\1. Estructura CeCos\Estructura_RGARG_IE.xlsx")
COL_CECO = 21          # columna "CECO" (numérico)
NIVEL_RUTA = 6         # la ruta guardada empieza en "Canales" (se omite Group > Company > Country > Sector)
FILA_ENCABEZADO = 7


def main(ruta):
    wb = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    pila, filas = {}, []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i <= FILA_ENCABEZADO:
            continue
        d = next((j for j in range(1, 20) if r[j] not in (None, "")), None)
        if d is None or r[COL_CECO] in (None, ""):
            continue
        pila[d] = str(r[d]).strip()
        for k in [k for k in pila if k > d]:
            del pila[k]
        ruta_n = [pila[k] for k in sorted(pila)]
        if len(ruta_n) < 2:
            continue
        filas.append({
            "ID_CENTRO_COSTOS": str(int(r[COL_CECO])) if isinstance(r[COL_CECO], (int, float)) else str(r[COL_CECO]).strip(),
            "cat_Rama": ruta_n[-2],
            "cat_Rama_Padre": ruta_n[-3] if len(ruta_n) >= 3 else "",
            "cat_Rama_Ruta": " > ".join(ruta_n[NIVEL_RUTA - 1:-1]),
            "nivel": d,
        })
    df = pd.DataFrame(filas).drop_duplicates("ID_CENTRO_COSTOS", keep="last")
    destino = BASE_DIR / "datos" / "cat_arbol.parquet"
    df.to_parquet(destino, index=False)
    print(f"{len(df):,} CECOs, {df['cat_Rama'].nunique():,} ramas -> {destino}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else EXCEL)
