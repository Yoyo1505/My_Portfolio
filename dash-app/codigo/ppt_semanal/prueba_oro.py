# -*- coding: utf-8 -*-
"""
prueba_oro.py — Regresión de la presentación automática. Correr tras cualquier cambio en ppt_semanal/.

    python -m ppt_semanal.prueba_oro

Prueba 1 (motor de fórmulas): el evaluador interno debe dar, para TODAS las fórmulas de la plantilla Excel,
          el mismo valor que Excel dejó calculado (semana 39: 14,176 fórmulas).
Prueba 2 (de punta a punta): armar la presentación con las BD de la plantilla Excel debe reproducir la
          plantilla .pptx celda por celda (texto, negrita y color) y párrafo por párrafo.
Las dos usan SOLO los archivos de ppt_semanal/plantilla/, que son la semana 39 aprobada.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "ppt_semanal"

import openpyxl

from . import bases as B
from .generar_ppt import PLANTILLA_PPTX, PLANTILLA_XLSX, _nombres_bd, generar
from .ppt_fill import LAMINAS_QUITAR, Deck
from .validar import estilos_celdas, textos
from .xl_eval import Book, ExcelErr


def prueba_motor() -> tuple[bool, str]:
    wf = openpyxl.load_workbook(PLANTILLA_XLSX, data_only=False)
    wv = openpyxl.load_workbook(PLANTILLA_XLSX, data_only=True)
    nombres = _nombres_bd(PLANTILLA_XLSX)
    bases = B.leer_bases_excel(PLANTILLA_XLSX)
    book = Book(wf, {n: B.a_tabla(bases[n]) for n in nombres})
    total = malas = 0
    ejemplos = []
    for ws in wf.worksheets:
        if ws.title in nombres or ws.title == "Catálogo":
            continue
        for fila in ws.iter_rows():
            for c in fila:
                if not (isinstance(c.value, str) and c.value.startswith("=")):
                    continue
                total += 1
                esp = wv[ws.title][c.coordinate].value
                got = book.get(ws.title, c.coordinate)
                if isinstance(got, ExcelErr):
                    ok = isinstance(esp, str) and esp.startswith("#")
                elif isinstance(esp, (int, float)) and isinstance(got, (int, float)):
                    ok = abs(esp - got) <= 1e-6 * max(1.0, abs(esp))
                else:
                    ok = esp == got or (esp in (None, "") and got in (None, "", 0.0))
                if not ok:
                    malas += 1
                    if len(ejemplos) < 5:
                        ejemplos.append(f"{ws.title}!{c.coordinate}: excel={esp!r} motor={got!r}")
    msg = f"{total} fórmulas, {malas} diferencias"
    return malas == 0, msg + ("" if not ejemplos else " | " + " ; ".join(ejemplos))


def prueba_punta_a_punta() -> tuple[bool, str]:
    deck0 = Deck(PLANTILLA_PPTX)
    import re
    from .ppt_fill import NS, _txt
    sem = int(re.search(r"semana (\d+)", " ".join(_txt(p) for p in deck0.slide(1).iterfind(".//a:p", NS)), re.I).group(1))
    with tempfile.TemporaryDirectory() as tmp:
        generar(sem, fuente="excel", excel_fuente=PLANTILLA_XLSX, salida=Path(tmp), nombre="oro.pptx", log=lambda *_: None,
                ajustar=False)       # la plantilla sin los ajustes de formato (quitar/agregar láminas)
        deck1 = Deck(Path(tmp) / "oro.pptx")
        # se excluyen la portada (el generador le agrega «Gasto semana N y / Acumulado a semana N») y las
        # láminas que el generador quita a propósito (LAMINAS_QUITAR); el índice de lámina no se compara
        ta = [t for i, t in textos(deck0) if i != 1 and i not in LAMINAS_QUITAR]
        tb = [t for i, t in textos(deck1) if i != 1]
        dt = sum(1 for x, y in zip(ta, tb) if x != y) + abs(len(ta) - len(tb))
        ea = [e[1:] for e in estilos_celdas(deck0) if e[0] not in LAMINAS_QUITAR]
        eb = [e[1:] for e in estilos_celdas(deck1)]
        de = sum(1 for x, y in zip(ea, eb) if x != y) + abs(len(ea) - len(eb))
    return dt == 0 and de == 0, f"{len(ta)} párrafos ({dt} distintos), {len(ea)} celdas ({de} distintas)"


def main() -> int:
    ok_all = True
    for nombre, fn in (("1 · motor de fórmulas vs Excel", prueba_motor), ("2 · plantilla reproducida de punta a punta", prueba_punta_a_punta)):
        ok, msg = fn()
        ok_all &= ok
        print(f"[{'OK ' if ok else 'FALLA'}] prueba {nombre}: {msg}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
