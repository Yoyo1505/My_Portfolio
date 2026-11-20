# -*- coding: utf-8 -*-
"""
validar.py — Comprobaciones sobre la presentación generada.

* estructura: nada cambia salvo el texto (mismas partes del zip, mismas formas, mismas
  tablas, mismos hipervínculos y mismos rels).
* estilos: cada celda numérica usa un (negrita, color) que existe en la plantilla para
  esa columna, relleno y signo.
* cuadre: los totales deben coincidir entre láminas que muestran lo mismo por otro corte.
* sin restos: no queda ninguna referencia a la semana de la plantilla.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from lxml import etree

from .ppt_fill import (NS, Deck, Estilos, _fill_hex, _color, _numerica, _rpr, _tabla_grid, _txt)


def _slide_parts(z: zipfile.ZipFile):
    return sorted([n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)],
                  key=lambda s: int(re.findall(r"\d+", s)[0]))


def firma_estructura(root, sin_menu: bool = False) -> dict:
    """Huella de una lámina sin considerar el texto. sin_menu: ignora los botones «Menu · ...» (se quitan a propósito)."""
    nombre = lambda e: e.xpath("string(.//p:cNvPr/@name)", namespaces=NS)
    elems = [e for e in root.xpath("//p:spTree/*", namespaces=NS)
             if etree.QName(e).localname != "nvGrpSpPr" and not (sin_menu and nombre(e).startswith("Menu"))]
    formas = [(etree.QName(e).localname, nombre(e)) for e in elems]
    tablas = [(len(t.findall("a:tr", NS)), len(t.findall("a:tblGrid/a:gridCol", NS)),
               [tc.get("gridSpan") or tc.get("rowSpan") or tc.get("hMerge") or tc.get("vMerge") or ""
                for tr in t.findall("a:tr", NS) for tc in tr.findall("a:tc", NS)])
              for t in root.xpath("//a:tbl", namespaces=NS)]
    links = sorted(h.get("action") or "" for e in elems for h in e.xpath(".//a:hlinkClick", namespaces=NS))
    return {"formas": formas, "tablas": tablas, "links": links}


def validar_estructura(original: Path, generado: Path, quitadas: list[str] = (),
                       agregadas: list[str] = (), sin_menu: bool = False) -> list[str]:
    """agregadas: partes nuevas a propósito (láminas clonadas). sin_menu: los botones de menú pueden faltar
    (y por eso cambian los rels de las láminas)."""
    errores = []
    with zipfile.ZipFile(original) as a, zipfile.ZipFile(generado) as b:
        na, nb = [n for n in a.namelist() if n not in quitadas], [n for n in b.namelist() if n not in agregadas]
        if na != nb:
            errores.append(f"partes del zip distintas: {set(na) ^ set(nb)}")
        slides = set(_slide_parts(a))
        for n in na:
            if n in slides or n not in nb:
                continue
            if quitadas and n in ("ppt/presentation.xml", "ppt/_rels/presentation.xml.rels", "[Content_Types].xml"):
                continue
            if sin_menu and n.startswith("ppt/slides/_rels/"):
                continue
            if a.read(n) != b.read(n):
                errores.append(f"parte modificada que no debía cambiar: {n}")
        for n in sorted(slides):
            if n not in nb:
                continue
            fa = firma_estructura(etree.fromstring(a.read(n)), sin_menu)
            fb = firma_estructura(etree.fromstring(b.read(n)), sin_menu)
            if fa != fb:
                errores.append(f"estructura distinta en {n}")
    return errores


def validar_estilos(deck: Deck, estilos: Estilos) -> tuple[int, list[str]]:
    desconocidos, revisadas = [], 0
    for i in range(1, len(deck) + 1):
        for tbl in deck.slide(i).xpath("//a:tbl", namespaces=NS):
            ncol = len(tbl.findall("a:tblGrid/a:gridCol", NS))
            for r, fila in enumerate(_tabla_grid(tbl)):
                for c, tc in enumerate(fila):
                    t = _txt(tc).strip()
                    rp = _rpr(tc)
                    if rp is None or not _numerica(t):
                        continue
                    revisadas += 1
                    neg = t.startswith("(")
                    clave = (ncol, c, _fill_hex(tc), neg)
                    est = (rp.get("b"), _color(rp))
                    vistos = estilos.visto.get(clave)
                    if vistos is not None and est not in vistos:
                        desconocidos.append(f"lámina {i} r{r} c{c} '{t}': estilo {est} no existe en la plantilla para {clave}")
    return revisadas, desconocidos


def textos(deck: Deck) -> list[tuple[int, str]]:
    """Todos los textos de párrafo de la presentación, en orden de lámina."""
    out = []
    for i in range(1, len(deck) + 1):
        for p in deck.slide(i).iterfind(".//a:p", NS):
            t = _txt(p)
            if t:
                out.append((i, t))
    return out


def estilos_celdas(deck: Deck) -> list[tuple]:
    out = []
    for i in range(1, len(deck) + 1):
        for tbl in deck.slide(i).xpath("//a:tbl", namespaces=NS):
            for r, fila in enumerate(_tabla_grid(tbl)):
                for c, tc in enumerate(fila):
                    rp = _rpr(tc)
                    out.append((i, r, c, _txt(tc).strip(), (rp.get("b"), _color(rp)) if rp is not None else None))
    return out


def sin_restos(deck: Deck, sem: int) -> list[str]:
    """Toda mención de semana debe ser la actual o la anterior; cualquier otra es un resto de la plantilla."""
    validas = {sem, sem - 1}
    pat = re.compile(r"(?:[Ss]emana |(?<![A-Za-z])S)(\d+)(?!\d)")
    malos = []
    for i, t in textos(deck):
        for m in pat.finditer(t):
            if int(m.group(1)) not in validas:
                malos.append(f"lámina {i}: '{t}'")
                break
    return malos
