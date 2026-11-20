# -*- coding: utf-8 -*-
"""
verificar_layout.py — Compara con PowerPoint (COM) la altura real de cada fila de las tablas entre la
plantilla y la presentación generada. Si un texto nuevo es más largo y se parte en dos renglones, la fila
crece y esta verificación lo reporta (lámina, fila, alto original vs nuevo).

    python -m ppt_semanal.verificar_layout <generado.pptx> [plantilla.pptx]

Requiere PowerPoint instalado. Trabaja sobre copias temporales; no modifica ningún archivo.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path


def alturas(ruta: Path) -> dict:
    import win32com.client
    app = win32com.client.Dispatch("PowerPoint.Application")
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        copia = Path(tmp) / "copia.pptx"
        shutil.copyfile(ruta, copia)
        pres = app.Presentations.Open(str(copia), ReadOnly=True, Untitled=False, WithWindow=False)
        try:
            for i in range(1, pres.Slides.Count + 1):
                for sh in pres.Slides(i).Shapes:
                    if sh.HasTable:
                        t = sh.Table
                        res[i] = [round(t.Rows(r).Height, 1) for r in range(1, t.Rows.Count + 1)]
        finally:
            pres.Close()
            app.Quit()
    return res


def comparar(generado: Path, plantilla: Path) -> list[str]:
    a, b = alturas(plantilla), alturas(generado)
    problemas = []
    for i in sorted(a):
        if i not in b:
            problemas.append(f"lámina {i}: tabla ausente en el generado")
            continue
        for r, (x, y) in enumerate(zip(a[i], b[i])):
            if y > x + 0.5:      # solo crecimiento: indica un texto que ahora se parte en más renglones
                problemas.append(f"lámina {i} fila {r}: alto {x} -> {y}")
    return problemas


if __name__ == "__main__":
    gen = Path(sys.argv[1])
    plan = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "plantilla" / "Gasto_plantilla.pptx"
    malos = comparar(gen, plan)
    print(f"{len(malos)} filas con altura distinta")
    for m in malos[:40]:
        print("  ", m)
    sys.exit(1 if malos else 0)
