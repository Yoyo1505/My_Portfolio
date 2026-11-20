# -*- coding: utf-8 -*-
"""
generar_ppt.py — Presentación semanal de Canales Físicos, automática.

    python -m ppt_semanal.generar_ppt                  # semana vigente (aggs/_meta.json -> sem_max_real)
    python -m ppt_semanal.generar_ppt --semana 39      # una semana concreta
    python -m ppt_semanal.generar_ppt --fuente excel --excel-fuente Gasto_semana_39_presentacion_PSA.xlsx
                                                       # prueba de oro: usa las BD de un Excel ya armado

Flujo: bases (aggs/pdc.parquet + catálogo) -> fórmulas de la plantilla Excel (xl_eval)
-> texto de la plantilla .pptx (ppt_fill) -> validaciones -> salidas/presentacion/.
La plantilla .pptx y .xlsx se leen y NUNCA se modifican.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "ppt_semanal"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # codigo/ (para `semana`)

import openpyxl

from . import ajustes
from . import bases as B
from .ppt_fill import Deck, Estilos, llenar_deck, LAMINAS_QUITAR, texto_fechas, texto_fechas_acum, NS, _txt
from .validar import sin_restos, validar_estilos, validar_estructura
from .xl_eval import Book

AQUI = Path(__file__).resolve().parent
PROYECTO = AQUI.parent.parent
AGGS = PROYECTO / "aggs"
PLANTILLA_PPTX = AQUI / "plantilla" / "Gasto_plantilla.pptx"
PLANTILLA_XLSX = AQUI / "plantilla" / "Gasto_plantilla_PSA.xlsx"
CATALOGO = AQUI / "catalogo_territorio.csv"
VENTANA = 4          # semanas del bloque «últimas N semanas acumuladas» (entre la semana y el acumulado)
SALIDA = PROYECTO / "salidas" / "presentacion"


def _sem_vigente() -> int:
    meta = json.loads((AGGS / "_meta.json").read_text(encoding="utf-8-sig"))
    return int(meta["sem_max_real"])


def _sem_plantilla(deck: Deck) -> int:
    t = " ".join(_txt(p) for p in deck.slide(1).iterfind(".//a:p", NS))
    m = re.search(r"semana (\d+)", t, re.I)
    if not m:
        raise ValueError("no se encontró 'semana NN' en la portada de la plantilla")
    return int(m.group(1))


def _nombres_bd(plantilla_xlsx: Path) -> tuple[str, str, str]:
    wb = openpyxl.load_workbook(plantilla_xlsx, read_only=True)
    nombres = list(wb.sheetnames)
    wb.close()
    prev = [n for n in nombres if re.match(r"^BD Semana_\d+$", n)]
    if "BD Acumulado" not in nombres or "BD Semana" not in nombres or len(prev) != 1:
        raise ValueError(f"la plantilla Excel no tiene las 3 hojas BD esperadas: {nombres[:5]}...")
    return "BD Acumulado", "BD Semana", prev[0]


def _fila_total(ws) -> int:
    for r in range(ws.max_row, 0, -1):
        for col in range(2, 17):
            v = ws.cell(r, col).value
            if isinstance(v, str) and v.strip().lower().startswith("total"):
                return r
    raise ValueError(f"{ws.title}: sin fila Total")


def cuadres(book: Book, tablas: dict, nombres_bd: tuple[str, str, str], hojas: dict[int, str]) -> list[str]:
    """Totales de láminas hermanas entre sí y contra las BD (detecta importes que no entran a ninguna lámina)."""
    errores = []
    bd_a, bd_s, bd_p = nombres_bd

    def total(hoja, col):
        ws = book.wb[hoja]
        r = _fila_total(ws)
        v = book.get(hoja, f"{col}{r}")
        return float(v)

    familias = {"semana": (2, 3, 5), "acumulado": (7, 8, 9)}
    esperado = {
        "semana": {"B": ("F", bd_s), "D": ("H", bd_p), "F": ("G", bd_s), "H": ("H", bd_s)},
        "acumulado": {"B": ("F", bd_a), "D": ("G", bd_a), "F": ("H", bd_a)},
    }
    for fam, laminas in familias.items():
        for col, (colbd, bd) in esperado[fam].items():
            bd_total = -float(tablas[bd].col(colbd).sum()) / 1000.0
            for n in laminas:
                if n not in hojas:
                    continue
                t = total(hojas[n], col)
                if abs(t - bd_total) > 1e-6 * max(1.0, abs(bd_total)):
                    errores.append(f"{hojas[n]} col {col}: total {t:,.3f} != BD {bd_total:,.3f} "
                                   f"(dif {t - bd_total:,.3f} M: hay importe que no cae en ninguna fila de la lámina)")
    return errores


def generar(sem: int | None = None, fuente: str = "aggs", excel_fuente: Path | None = None,
            salida: Path | None = None, plantilla_pptx: Path = PLANTILLA_PPTX,
            plantilla_xlsx: Path = PLANTILLA_XLSX, catalogo: Path = CATALOGO,
            nombre: str | None = None, log=print, ajustar: bool = True) -> dict:
    t0 = time.time()
    sem = sem or _sem_vigente()
    salida = Path(salida or SALIDA)
    salida.mkdir(parents=True, exist_ok=True)
    info: dict = {"semana": sem, "fuente": fuente, "inicio": datetime.now().isoformat(timespec="seconds")}

    # 1. bases
    nombres_bd = _nombres_bd(plantilla_xlsx)
    if fuente == "aggs":
        log(f"[ppt] armando bases de la semana {sem} desde aggs/pdc.parquet ...")
        cat = B.cargar_catalogo(catalogo)
        bases = B.construir_bases(AGGS / "pdc.parquet", sem, cat)
        orden = list(bases)
        bases = {nombres_bd[0]: bases[orden[0]], nombres_bd[1]: bases[orden[1]], nombres_bd[2]: bases[orden[2]]}
    elif fuente == "excel":
        bx = B.leer_bases_excel(excel_fuente)
        bases = {n: bx[n] for n in nombres_bd}
    else:
        raise ValueError(f"fuente desconocida: {fuente}")
    tablas = {n: B.a_tabla(df) for n, df in bases.items()}
    info["filas_bd"] = {n: len(d) for n, d in bases.items()}

    # 2. fórmulas del Excel
    book = Book(openpyxl.load_workbook(plantilla_xlsx), tablas)
    hojas = {int(m.group(1)): n for n in book.wb.sheetnames if (m := re.match(r"^L(\d{2})\b", n))}

    # 3. presentación
    deck = Deck(plantilla_pptx)
    tpl_sem = _sem_plantilla(deck)
    from semana import rango_semana  # codigo/semana.py (fuente única del calendario ARG)
    ini, fin = rango_semana(sem)
    fechas = texto_fechas(ini, fin)
    fechas_acum = texto_fechas_acum(rango_semana(1)[0], fin)
    estilos = Estilos(deck)
    rep = llenar_deck(deck, book, tpl_sem, sem, fechas, fechas_acum, estilos)
    info.update(rep)
    info["fechas"] = [fechas, fechas_acum]

    # 4. validaciones (cualquier fallo impide entregar el archivo)
    problemas = []
    problemas += [f"cuadre: {e}" for e in cuadres(book, tablas, nombres_bd, hojas)]
    problemas += [f"resto de plantilla: {e}" for e in sin_restos(deck, sem)]
    revisadas, estilos_malos = validar_estilos(deck, estilos)
    problemas += [f"estilo: {e}" for e in estilos_malos]
    info["celdas_numericas_revisadas"] = revisadas

    out = salida / (nombre or f"Gasto_semana_{sem}.pptx")
    if problemas:
        info["problemas"] = problemas
        bad = out.with_name(out.stem + "_CON_ERRORES.pptx")
        deck.save(bad)
        raise RuntimeError("la presentación NO pasó las validaciones (se guardó como "
                           f"{bad.name} solo para inspección):\n  - " + "\n  - ".join(problemas[:20]))
    deck.titulo_portada(sem)
    if ajustar:
        ventana = None
        if fuente == "aggs" and sem >= 2 * VENTANA:
            b4 = B.construir_bases(AGGS / "pdc.parquet", sem, cat, n=VENTANA)
            o4 = list(b4)
            b4 = {nombres_bd[0]: b4[o4[0]], nombres_bd[1]: b4[o4[1]], nombres_bd[2]: b4[o4[2]]}
            book4 = Book(openpyxl.load_workbook(plantilla_xlsx), {n: B.a_tabla(df) for n, df in b4.items()})
            ventana = {"book": book4, "bases": b4, "sem": sem, "n": VENTANA, "hojas": hojas,
                       "fechas": texto_fechas(rango_semana(sem - VENTANA + 1)[0], fin)}
        info["ajustes"] = ajustes.aplicar(deck, book, bases, nombres_bd, estilos, ventana)
        _, malos = validar_estilos(deck, estilos)
        if malos:
            raise RuntimeError("estilo en láminas por cuenta:\n  - " + "\n  - ".join(malos[:20]))
    else:
        for idx in sorted(LAMINAS_QUITAR, reverse=True):
            deck.quitar_lamina(idx)
    # texto de tablas 14 pt -> 12 pt: evita que las celdas se encimen con las de abajo
    for i in range(1, len(deck) + 1):
        root = deck.slide(i)
        for rp in root.xpath("//a:tbl//a:rPr[@sz='1400']", namespaces=NS):
            rp.set("sz", "1200")
        # títulos largos (p. ej. «… · Últimas 4 semanas») se parten en dos renglones y se enciman con el subtítulo
        sps = root.xpath("//p:sp", namespaces=NS)
        if not sps:
            continue
        titulo = sps[0]
        n_tit = len(_txt(titulo))
        if n_tit > 40:
            for rp in titulo.xpath(".//a:rPr[@sz]", namespaces=NS):
                rp.set("sz", str(max(1800, int(int(rp.get("sz")) * 40 / n_tit))))
    deck.save(out)
    errores_est = validar_estructura(plantilla_pptx, out, getattr(deck, "quitadas", []),
                                     getattr(deck, "agregadas", []), ajustar)
    if errores_est:
        raise RuntimeError("estructura alterada:\n  - " + "\n  - ".join(errores_est))

    # 5. bases a Excel (auditoría)
    xb = salida / f"Bases_semana_{sem}.xlsx"
    import pandas as pd
    with pd.ExcelWriter(xb, engine="openpyxl") as w:
        for n, d in bases.items():
            d.to_excel(w, sheet_name=n, index=False)
    info.update({"pptx": str(out), "bases_xlsx": str(xb), "segundos": round(time.time() - t0, 1)})
    (salida / f"Gasto_semana_{sem}.log.json").write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str),
                                                         encoding="utf-8")
    log(f"[ppt] OK: {out.name} · {rep['tablas']} tablas · {rep['celdas']} celdas · {info['segundos']} s")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--semana", type=int, default=None)
    ap.add_argument("--fuente", choices=["aggs", "excel"], default="aggs")
    ap.add_argument("--excel-fuente", type=Path, default=None)
    ap.add_argument("--salida", type=Path, default=None)
    ap.add_argument("--nombre", default=None)
    a = ap.parse_args(argv)
    if a.fuente == "excel" and not a.excel_fuente:
        ap.error("--fuente excel requiere --excel-fuente")
    sys.path.insert(0, str(AQUI.parent))
    try:
        generar(a.semana, a.fuente, a.excel_fuente, a.salida, nombre=a.nombre)
    except Exception as e:
        print(f"[ppt] ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
