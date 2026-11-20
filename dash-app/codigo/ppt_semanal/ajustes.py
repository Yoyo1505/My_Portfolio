# -*- coding: utf-8 -*-
"""
ajustes.py — Diferencias del formato vigente respecto de la plantilla aprobada (semana 39).

  * Se quitan las láminas que ya no van: KPI repetido de Total (semana y acumulado), «Integrador» de
    Total (semana y acumulado) y «Integrador» semana de Centro, Norte, Sur y Otros.
  * Los botones de menú que enlazaban a esas láminas se eliminan y el resto del menú sube.
  * Después de la lámina KPI de semana de cada división (Centro, Norte, Sur, Otros) se agrega una lámina
    «Detalle por cuenta»: mismas tarjetas y mismas columnas, una fila por grupo de cuentas.

Los números de lámina son los de la plantilla (ppt_semanal/plantilla/Gasto_plantilla.pptx).
Cada uno se verifica contra el título de la lámina antes de tocar nada.
"""
from __future__ import annotations

import copy
import re

from .fmt import FMT_NUM1, FMT_PCT0, FMT_PCT1, format_value
from .ppt_fill import (ALIAS_LAMINA, NS, RE_FECHA, RE_KPI_VAL, Deck, Estilos, _color, _fill_hex, _numerica,
                       _reemplazar_en_parrafo, _txt, _x_de, llenar_kpis, llenar_tabla)

QUITAR = {3: "Gasto", 4: "Gasto", 6: "Integrador", 8: "Gasto", 10: "Integrador",
          13: "Integrador", 17: "Integrador", 21: "Integrador", 25: "Integrador"}
# lámina KPI de semana de cada división -> (título esperado, grupos del catálogo que suma)
POR_CUENTA = {12: ("Centro", ["Division Centro"]),
              16: ("Norte", ["Division Norte"]),
              20: ("Sur", ["Division Sur"]),
              24: ("Otros", ["Otros", "Direccion De Geografia Nacional", "Corporativo"])}

# Territorios que el catálogo manda a «Otros» pero que la hoja de Excel de Otros no tiene como fila: la tarjeta KPI
# de Otros no los suma. El detalle por cuenta sigue a la tarjeta para que lámina y tarjeta cuadren.
SIN_FILA_EN_EXCEL = {"No Clasificado"}

ALTO_FILA = 330000         # EMU; 17 filas + 3 de encabezado caben bajo las tarjetas
SZ_CUENTA = "1200"


def _titulo(deck: Deck, idx: int) -> str:
    return _txt(deck.slide(idx).xpath("//p:sp", namespaces=NS)[0])


def _cuentas(book) -> list[str]:
    hoja = next(n for n in book.wb.sheetnames if re.match(r"^L13\b", n))
    ws = book.wb[hoja]
    out = []
    for r in range(1, ws.max_row + 1):
        if str(ws.cell(r, 2).value).strip().lower() == "total":      # abajo vienen Presupuesto / Año anterior / Sem. anterior
            break
        if isinstance(ws.cell(r, 5).value, str) and ws.cell(r, 5).value.startswith("=-SUMIFS"):
            out.append(ws.cell(r, 2).value)
    if len(out) < 10:
        raise ValueError(f"{hoja}: no se encontró la lista de cuentas")
    return out


def _serie(df, grupos, col, cuentas):
    d = df[df["Grupo"].isin(grupos) & ~df["Subdirección / Territorio"].isin(SIN_FILA_EN_EXCEL)]
    s = -d.groupby("Grupo de Cuentas")[col].sum() / 1000.0
    resto = float(s.drop(index=[c for c in cuentas if c in s.index]).abs().sum())
    if resto > 1e-6:
        raise ValueError(f"hay grupos de cuentas fuera de la lista del Excel (suman {resto:,.3f} M): "
                         f"{sorted(set(s.index) - set(cuentas))}")
    return [float(s.get(c, 0.0)) for c in cuentas]


def _pct(num, den):
    """Variación %; «n.a.» si la referencia es 0; se topa en ±1,000% para que la cifra quepa en la celda."""
    if not den:
        return "n.a."
    return max(-10.0, min(10.0, num / abs(den)))


def _textos_fila(ant, sem, plan, real, tot, label):
    """15 celdas: [Año ant $,%] [Sem ant $,%] [Plan $,%] [Real $,%] etiqueta [var Plan, Sem ant, Año ant: $,%]."""
    c = []
    for v, t in ((ant, tot[0]), (sem, tot[1]), (plan, tot[2]), (real, tot[3])):
        c += [format_value(v, FMT_NUM1), format_value(v / t if t else 0.0, FMT_PCT0)]
    c.append((label, False))
    for ref in (plan, sem, ant):
        d = ref - real
        c += [format_value(d, FMT_NUM1), format_value(_pct(d, ref), FMT_PCT1)]
    return c


def _poner(tr, celdas, estilos: Estilos, ncol: int, total: bool):
    for ci, (tc, (texto, neg)) in enumerate(zip(tr.findall("a:tc", NS), celdas)):
        ps = tc.findall("a:txBody/a:p", NS)
        for extra in ps[1:]:
            tc.find("a:txBody", NS).remove(extra)
        runs = ps[0].findall("a:r", NS)
        for extra in runs[1:]:
            ps[0].remove(extra)
        rp = runs[0].find("a:rPr", NS)
        if ci != 8:
            neg_pl = _txt(tc).strip().startswith("(")
            b, color = estilos.estilo(ncol, ci, _fill_hex(tc), neg_pl, neg, rp.get("b"), _color(rp))
            rp.set("b", b)
            rp.find("a:solidFill/a:srgbClr", NS).set("val", color)
        rp.set("sz", SZ_CUENTA)
        runs[0].find("a:t", NS).text = texto


def _tabla_por_cuenta(root, cuentas, filas, total, estilos: Estilos):
    tbl = root.xpath("//a:tbl", namespaces=NS)[0]
    trs = tbl.findall("a:tr", NS)
    ncol = len(tbl.findall("a:tblGrid/a:gridCol", NS))
    base = [trs[3], trs[4]]                       # filas blanca / gris alternadas de la plantilla
    fila_total = trs[-1]
    etiqueta_total = _txt(fila_total.findall("a:tc", NS)[8]).strip()
    moldes = [copy.deepcopy(t) for t in base]
    molde_total = copy.deepcopy(fila_total)
    for tr in trs[3:]:
        tbl.remove(tr)
    tot = total[:4]
    for i, (cuenta, v) in enumerate(zip(cuentas, filas)):
        tr = copy.deepcopy(moldes[i % 2])
        _poner(tr, _textos_fila(*v, tot, cuenta), estilos, ncol, False)
        tr.set("h", str(ALTO_FILA))
        tbl.append(tr)
    tr = molde_total
    _poner(tr, _textos_fila(*total, tot, etiqueta_total), estilos, ncol, True)
    tr.set("h", str(ALTO_FILA))
    tbl.append(tr)
    alto = sum(int(t.get("h")) for t in tbl.findall("a:tr", NS))
    marco = tbl.getparent().getparent().getparent()              # p:graphicFrame
    marco.find("p:xfrm/a:ext", NS).set("cy", str(alto))
    return alto


def _kpi_real(root) -> float:
    vals = sorted([sp for sp in root.xpath("//p:sp", namespaces=NS) if RE_KPI_VAL.match(_txt(sp).strip())], key=_x_de)
    t = _txt(vals[0]).strip()
    m = float(re.sub(r"[^\d.]", "", t))
    return -m if t.startswith("(") else m


def _a_ventana(root, sem: int, n: int, fechas: str):
    """Convierte una lámina de «Semana N» (ya llena con las cifras de la ventana) a «Últimas n semanas»."""
    ini = f"S{sem - n + 1}-S{sem}"
    pats = [
        (re.compile(rf"[Ss]emana {sem}-2026"), lambda m: f"{ini} 2026"),
        (re.compile(rf"Semana {sem - 1}(?=\D|$)"), lambda m: f"{n} semanas previas"),
        (re.compile(rf"(?<=Real )semana {sem}(?!\d)"), lambda m: f"últimas {n} semanas"),
        (re.compile(rf"Semana {sem}(?!\d)"), lambda m: f"Últimas {n} semanas"),
    ]
    cambios = 0
    for p in root.iterfind(".//a:p", NS):
        t = _txt(p).strip()
        if not t or _numerica(t):
            continue
        if RE_FECHA.match(t):
            runs = p.findall("a:r", NS)
            runs[0].find("a:t", NS).text = fechas
            for r in runs[1:]:
                p.remove(r)
            cambios += 1
            continue
        if t in ("Sem", "Sem Anterior"):                # encabezados de la columna «semana anterior»
            runs = p.findall("a:r", NS)
            runs[0].find("a:t", NS).text = f"{n} {t}"
            for r in runs[1:]:
                p.remove(r)
            cambios += 1
            continue
        for pat, rep in pats:
            if _reemplazar_en_parrafo(p, pat, rep):
                cambios += 1
    for sp in root.xpath("//p:sp", namespaces=NS):          # botón de menú «Semana N» (angosto)
        if sp.xpath("string(p:nvSpPr/p:cNvPr/@name)", namespaces=NS).startswith("Menu · Semana"):
            runs = sp.findall(".//a:r", NS)
            if runs:
                runs[0].find("a:t", NS).text = f"{n} semanas"
                for r in runs[1:]:
                    r.getparent().remove(r)
    resto = [t for t in (_txt(p) for p in root.iterfind(".//a:p", NS))
             if re.search(rf"[Ss]emana {sem}(?!\d)|[Ss]emana {sem - 1}(?!\d)", t)]
    if resto:
        raise ValueError(f"lámina de {n} semanas con texto de semana sin convertir: {resto[:3]}")
    return cambios


def aplicar(deck: Deck, book, bases: dict, nombres_bd: tuple, estilos: Estilos, ventana: dict | None = None) -> dict:
    """Clona y llena las láminas por cuenta y las de «últimas N semanas», quita láminas/botones.

    ventana: {"book", "bases", "sem", "n", "fechas", "hojas"} para el bloque de últimas N semanas acumuladas
    (entre el de la semana y el acumulado); None = sin ese bloque."""
    # 1. verificar que los números de lámina siguen siendo los esperados
    for idx, clave in {**QUITAR, **{k: v[0] for k, v in POR_CUENTA.items()}}.items():
        if clave not in _titulo(deck, idx):
            raise ValueError(f"plantilla cambió: la lámina {idx} debía contener '{clave}' y dice '{_titulo(deck, idx)}'")
    # nombres de partes (estables) ANTES de clonar: los clones desplazan los índices
    nombres = [deck.order[i - 1] for i in QUITAR]
    plantilla_idx = {n: i for i, n in enumerate(deck.order, 1)}
    k_div = {idx: deck.order[idx - 1] for idx in POR_CUENTA}
    territorios = [deck.order[i - 1] for i in range(29, len(deck) + 1)
                   if deck.slide(i).xpath("//a:tbl", namespaces=NS) and "Acumulado" not in _titulo(deck, i)]
    total_sem = [deck.order[2 - 1], deck.order[5 - 1]]
    bd_a, bd_s, bd_p = nombres_bd
    cuentas = _cuentas(book)

    def por_cuenta(root, bs, bp, grupos):
        filas = list(zip(_serie(bs, grupos, "Real 2025.", cuentas), _serie(bp, grupos, "Real 2026", cuentas),
                         _serie(bs, grupos, "Plan 2026", cuentas), _serie(bs, grupos, "Real 2026", cuentas)))
        total = tuple(sum(f[k] for f in filas) for k in range(4))
        kpi = _kpi_real(root)
        if abs(round(total[3], 1) - kpi) > 0.051:
            raise ValueError(f"el detalle por cuenta suma {total[3]:,.2f} M y la tarjeta dice {kpi:,.1f} M")
        _tabla_por_cuenta(root, cuentas, filas, total, estilos)
        r0 = root.xpath("//p:sp", namespaces=NS)[1].find(".//a:r/a:t", NS)
        r0.text = "Detalle por cuenta · " + (r0.text or "")

    def posicion(nombre):
        return deck.order.index(nombre) + 1

    # 2. detalle por cuenta de la semana (de la última a la primera para no mover los índices pendientes)
    p_sem = {}
    for idx in sorted(POR_CUENTA, reverse=True):
        nuevo = deck.clonar_lamina(idx)
        por_cuenta(deck.slide(nuevo), bases[bd_s], bases[bd_p], POR_CUENTA[idx][1])
        p_sem[idx] = deck.order[nuevo - 1]
    # 3. bloque de últimas N semanas: cada lámina de semana se copia después del último de su bloque
    bloque4 = []
    if ventana:
        bk, b4, sem, n = ventana["book"], ventana["bases"], ventana["sem"], ventana["n"]
        hojas = ventana["hojas"]
        rep: dict = {}

        def copiar(nombre, destino_nombre, tpl_idx):
            nuevo = deck.clonar_lamina(posicion(nombre), posicion(destino_nombre))
            root = deck.slide(nuevo)
            hoja = hojas[ALIAS_LAMINA.get(tpl_idx, tpl_idx)]
            llenar_tabla(bk, hoja, root.xpath("//a:tbl", namespaces=NS)[0], estilos, rep)
            llenar_kpis(bk, hoja, root, rep)
            _a_ventana(root, sem, n, ventana["fechas"])
            bloque4.append(deck.order[nuevo - 1])
            return deck.order[nuevo - 1]

        # Total: concepto + geografía, después de la geografía
        destino = total_sem[-1]
        for nom in total_sem:
            destino = copiar(nom, destino, plantilla_idx[nom])
        # divisiones: KPI + detalle por cuenta
        for idx, (_, grupos) in POR_CUENTA.items():
            k, p = k_div[idx], p_sem[idx]
            k4 = copiar(k, p, idx)
            nuevo = deck.clonar_lamina(posicion(k4), posicion(k4))
            root = deck.slide(nuevo)
            por_cuenta(root, b4[bd_s], b4[bd_p], grupos)
            bloque4.append(deck.order[nuevo - 1])
        # territorios: una lámina de semana por territorio
        for nom in territorios:
            copiar(nom, nom, plantilla_idx[nom])
    # 4. quitar botones y láminas
    bases_quitadas = {n.rsplit("/", 1)[-1] for n in nombres}
    botones = deck.quitar_enlaces_a(bases_quitadas)
    for n in sorted(nombres, key=deck.order.index, reverse=True):
        deck.quitar_lamina(deck.order.index(n) + 1)
    return {"por_cuenta": list(p_sem.values()), "laminas_ventana": len(bloque4), "botones_quitados": botones,
            "laminas_quitadas": sorted(QUITAR), "laminas_finales": len(deck)}
