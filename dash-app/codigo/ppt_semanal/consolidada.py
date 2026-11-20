# -*- coding: utf-8 -*-
"""
consolidada.py — Presentación única Físicos + Digitales con saltos entre ambas.

Se usa desde digitales_ppt (--consolidar): el Constructor ya anexó las láminas de digitales a las de
físicos; aquí se agrega la portada consolidada y, en TODAS las láminas, los botones de salto
«Canales físicos» / «Canales digitales» al pie del menú lateral.
"""
from __future__ import annotations

import copy
import io

from .digitales_ppt import A, NS, SEC_TOTAL, _t_set

P = "{%s}" % NS["p"]
BTN_X, BTN_W, BTN_H = 164592, 2048256, 402336
BTN_Y = {"fis": 9189720, "dig": 9692640}


def _boton(cb, estilo: str, texto: str, nombre: str, x: int, y: int, cx: int, cy: int, sz: int | None = None):
    el = copy.deepcopy(cb.t_menu[estilo])
    for h in el.findall(".//" + A + "hlinkClick"):
        h.getparent().remove(h)
    _t_set(el, texto)
    el.find(".//" + P + "cNvPr").set("name", nombre)
    el.find(".//" + A + "off").set("x", str(x))
    el.find(".//" + A + "off").set("y", str(y))
    el.find(".//" + A + "ext").set("cx", str(cx))
    el.find(".//" + A + "ext").set("cy", str(cy))
    if sz:
        for rpr in el.iter(A + "rPr"):
            rpr.set("sz", str(sz))
    return el


def _poner(slide, el, destino):
    tree = slide.shapes._spTree
    el.find(".//" + P + "cNvPr").set("id", str(max(int(e.get("id")) for e in tree.iter(P + "cNvPr")) + 1))
    tree.append(el)
    nombre = el.find(".//" + P + "cNvPr").get("name")
    next(s for s in slide.shapes if s.name == nombre).click_action.target_slide = destino


def _portada_consolidada(cb, fis, dig):
    """Copia de la portada de físicos con título consolidado y dos botones grandes."""
    pic = next(s for s in cb.portada.shapes if s.shape_type == 13)
    caja = next(s for s in cb.portada.shapes if s.has_text_frame)
    s = cb.prs.slides.add_slide(cb.blank)
    s.shapes.add_picture(io.BytesIO(pic.image.blob), pic.left, pic.top, pic.width, pic.height)
    el = copy.deepcopy(caja._element)
    s.shapes._spTree.append(el)
    ps = el.findall(".//" + A + "p")
    textos = ["Físicos y digitales", f"Gasto semana {cb.sem} y", f"Acumulado a semana {cb.sem}"]
    for p, txt in zip(ps, textos):
        rs = p.findall(A + "r")
        for r in rs[1:]:
            p.remove(r)
        rs[0].find(A + "t").text = txt
    w, h, gap = 4297680, 640080, 365760
    x0 = (cb.prs.slide_width - (2 * w + gap)) // 2
    y = 8138160
    for i, (clave, txt, dest) in enumerate((("fis", "Canales físicos", fis), ("dig", "Canales digitales", dig))):
        _poner(s, _boton(cb, "Menu · Por concepto", txt, f"Menu · Ir {clave}", x0 + i * (w + gap), y, w, h, sz=2000), dest)
    return s


def finalizar(cb) -> None:
    fis = cb.portada                      # portada de físicos
    dig = cb.slides[0][1]                 # primera lámina de digitales (Resumen)
    ids_dig = {sl.slide_id for _, sl in cb.slides}
    nueva = _portada_consolidada(cb, fis, dig)
    for sl in list(cb.prs.slides):
        if sl.slide_id in (nueva.slide_id, fis.slide_id):
            continue                      # las portadas no llevan menú lateral
        en_dig = sl.slide_id in ids_dig
        for clave, txt, dest in (("fis", "Canales físicos", fis), ("dig", "Canales digitales", dig)):
            activo = (clave == "dig") == en_dig
            estilo = "Menu · Por concepto" if activo else "Menu · Canales físicos"
            _poner(sl, _boton(cb, estilo, txt, f"Menu · Ir {clave}", BTN_X, BTN_Y[clave], BTN_W, BTN_H), dest)
    lst = cb.prs.slides._sldIdLst
    el = lst[-1]                          # la portada consolidada va primero
    lst.remove(el)
    lst.insert(0, el)
    ids_tot = {sl.slide_id for sec, sl in cb.slides if sec == SEC_TOTAL}
    for el in [e for e in lst if int(e.get("id")) in ids_tot]:    # «Fís. + Digitales» va al final
        lst.remove(el)
        lst.append(el)

