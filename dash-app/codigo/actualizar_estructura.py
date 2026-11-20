# -*- coding: utf-8 -*-
"""
actualizar_estructura.py — actualiza datos/Catálogo de estructura.xlsx desde la exportación SAP "Estructura_RGARG_IE.xlsx"
(SharedDrive: 99. Catálogos\\1. Estructura CeCos).

El archivo de SAP trae la hoja "Arbol": título en las filas 1-7, ENCABEZADO en la fila 8 y datos desde la 9; las columnas 0-19
son la sangría del árbol y de la 20 en adelante están los 62 atributos, IGUALES (mismo orden) a los de la hoja "Estructura" que
lee el build. El mapeo es por POSICIÓN y se valida contra el encabezado actual del catálogo (ignorando solo el sufijo numérico
que Excel/pandas agrega a nombres repetidos). Se conservan los valores tal cual (incluidos los "0"); solo se descartan filas
completamente vacías y CECOs repetidos (se queda el primero; en la exportación son nodos raíz duplicados al final).

Uso:  python actualizar_estructura.py [ruta_origen.xlsx]
Deja una copia del catálogo anterior en datos/_catalogo_estructura_anterior.xlsx.
"""
from __future__ import annotations

import re
import shutil
import sys
import time
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DESTINO = ROOT / "datos" / "Catálogo de estructura.xlsx"
ORIGEN = Path(r"<SHARED_DRIVE>"
              r"\99. Catálogos\1. Estructura CeCos\Estructura_RGARG_IE.xlsx")
HOJA_ORIGEN, FILA_ENCABEZADO, COL_ATRIBUTOS, N_ATRIBUTOS, COL_CLAVE = "Arbol", 8, 20, 62, 1


def _base(nombre) -> str:
    """Nombre sin el sufijo numérico de repetidos: 'CECO_1' -> 'CECO', 'Negocio_3' -> 'Negocio', 'ID_2' -> 'ID'."""
    return re.sub(r"[_\s]*\d+$", "", str(nombre).strip()) if re.search(r"[_]\d+$", str(nombre).strip()) else str(nombre).strip()


def main():
    origen = Path(sys.argv[1]) if len(sys.argv) > 1 else ORIGEN
    t0 = time.time()
    # encabezado actual (la referencia del mapeo)
    wb_o = openpyxl.load_workbook(DESTINO, read_only=True, data_only=True)
    cab_actual = [c for c in next(wb_o["Estructura"].iter_rows(min_row=1, max_row=1, values_only=True))]
    wb_o.close()
    cab_actual = cab_actual[:N_ATRIBUTOS]

    wb = openpyxl.load_workbook(origen, read_only=True, data_only=True)
    ws = wb[HOJA_ORIGEN]
    filas = ws.iter_rows(values_only=True)
    encabezado = None
    datos, vistos, repetidos, vacias = [], set(), 0, 0
    for i, f in enumerate(filas, 1):
        if i == FILA_ENCABEZADO:
            encabezado = list(f[COL_ATRIBUTOS:COL_ATRIBUTOS + N_ATRIBUTOS])
            continue
        if i < FILA_ENCABEZADO:
            continue
        attrs = list(f[COL_ATRIBUTOS:COL_ATRIBUTOS + N_ATRIBUTOS])
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in attrs):
            vacias += 1
            continue
        clave = str(attrs[COL_CLAVE]).strip()
        if clave in vistos:
            repetidos += 1
            continue
        vistos.add(clave)
        datos.append(attrs)
    wb.close()

    # validar el mapeo por posición
    malos = [(j, a, b) for j, (a, b) in enumerate(zip(cab_actual, encabezado)) if _base(a) != _base(b)]
    print("Mapeo por posición (origen SAP -> catálogo del build):")
    for j, (a, b) in enumerate(zip(cab_actual, encabezado)):
        marca = "" if _base(a) == _base(b) else "   <-- NO COINCIDE"
        print(f"  {j:2d}  {str(b).strip()[:44]:44s} -> {str(a).strip()[:44]}{marca}")
    if malos:
        sys.exit(f"\nABORTADO: {len(malos)} columnas no coinciden por posición; el formato del origen cambió. No se modificó nada.")

    shutil.copy2(DESTINO, DESTINO.with_name("_catalogo_estructura_anterior.xlsx"))
    wo = openpyxl.Workbook(write_only=True)
    hoja = wo.create_sheet("Estructura")
    hoja.append(cab_actual)
    for r in datos:
        hoja.append(r)
    tmp = DESTINO.with_suffix(".nuevo.xlsx")
    wo.save(tmp)
    tmp.replace(DESTINO)
    print(f"\nListo: {len(datos):,} CECOs escritos en {DESTINO.name} · repetidos descartados {repetidos} · filas vacías {vacias} "
          f"· {time.time() - t0:.0f}s\nCatálogo anterior: {DESTINO.with_name('_catalogo_estructura_anterior.xlsx').name}")


if __name__ == "__main__":
    main()
