# -*- coding: utf-8 -*-
"""
digitales_ppt.py — Presentación semanal de CANALES DIGITALES a partir del Excel oficial.

Mismo diseño que Gasto_semana_NN.pptx de canales físicos (se toma como plantilla visual:
portada, tarjetas KPI, tablas, menú lateral, logo). Las cifras salen de las hojas 8C /
Temporalidad del Excel "Gastos Red GARG (sem NN)...xlsx" (digitales_excel.py).

Uso (desde codigo/):
    python -m ppt_semanal.digitales_ppt --excel "<ruta .xlsx>" [--semana 42]

Requiere python-pptx (no está en requirements.txt del proyecto).
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import re
from datetime import date, timedelta
from pathlib import Path

from lxml import etree
from pptx import Presentation

from . import digitales_excel as dx

ROOT = Path(__file__).resolve().parents[2]
SALIDAS = ROOT / "salidas" / "presentacion"

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
A = "{%s}" % NS["a"]
ROJO, GRIS, GRIS_CLARO = "D10609", "3F3F3F", "7F7F7F"
FILA_ALTA, FILA_BAJA = "F7F7F7", "FFFFFF"

SEC_TOTAL = "Fís. + Digitales"
SECCIONES = ["Resumen", SEC_TOTAL, "Consolidado", "Shopinbaz", "Baz Digital", "Aurora Digital",
             "DG Canales Dig."]

SEMANA_1_INICIO = date(2025, 12, 29)          # lunes de la semana 1 (29 dic 2025 al 4 ene 2026)
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]
GASTO_SEMANAL = Path(r"<USER_HOME>\SharedDrive\Archivos de Analyst - "
                     r"Planeación Financiera\5. Presupuestos\3. Forecast 2026\Gasto semanal")


def inicio_semana(n: int) -> date:
    return SEMANA_1_INICIO + timedelta(days=7 * (n - 1))


def fin_semana(n: int) -> date:
    return inicio_semana(n) + timedelta(days=6)


def rango_texto(ini: date, fin: date) -> str:
    """'Del 12 al 18 de octubre'; si cruza de mes 'Del 28 sep al 4 oct'."""
    if ini.month == fin.month:
        return f"Del {ini.day} al {fin.day} de {MESES[fin.month - 1]}"
    return f"Del {ini.day} {MESES[ini.month - 1][:3]} al {fin.day} {MESES[fin.month - 1][:3]}"


def buscar_excel_semana(n: int, carpetas: list[Path]) -> Path | None:
    """Excel 'Gastos Red Garg (sem NN)...' más reciente en las carpetas dadas."""
    pat = re.compile(rf"Gastos Red Garg \(sem {n}\)", re.I)
    cand = [f for c in carpetas if c.is_dir() for f in c.glob("Gastos Red Garg (sem *)*.xlsx")
            if pat.search(f.name) and not f.name.startswith("~$")]
    return max(cand, key=lambda f: f.stat().st_mtime) if cand else None


def restar_acum(filas_n: list[dx.Fila], filas_p: list[dx.Fila] | None, avisos: list, nombre: str) -> list[dx.Fila]:
    """Últimas 4 semanas = acumulado(N) - acumulado(N-4), fila a fila (clave: cuenta + etiqueta + ocurrencia)."""
    def claves(fs):
        vistos, out = {}, {}
        for f in fs:
            k0 = (f.cuenta, f.label.split(" (")[0] if f.label.startswith("Total Gasto") else f.label)
            vistos[k0] = vistos.get(k0, 0) + 1
            out[(k0, vistos[k0])] = f
        return out
    prev = claves(filas_p) if filas_p else {}
    ck = claves(filas_n)
    out = []
    for k, f in ck.items():
        p = prev.get(k)
        if filas_p and p is None:
            avisos.append(f"[{nombre}] fila '{f.label}' no existe en el Excel de la sem. anterior; se toma completa")
        y = {c: f.y[c] - (p.y[c] if p else 0.0) for c in dx.Y}
        out.append(dx.Fila(f.cuenta, f.label, {c: 0.0 for c in dx.W}, y,
                           {"plan": 0.0, "semant": 0.0, "r25": 0.0}, {"plan": 0.0, "r25": 0.0}))
    return out

# (etiqueta que se muestra, etiqueta del Excel, es_cuenta)
FILAS_GRUPO = [
    ("Gastos de Personal", "Gastos de Personal", False),
    ("   de los cuales: Nómina", "Gastos de Nomina", False),
    ("Gastos de Publicidad", "Gastos de Publicidad", False),
    ("Gastos de Operación", "Gastos de Operación", False),
    ("Renta, Luz y Teléfono", "Renta, Luz y Teléfono", False),
    ("Flete Neto", "Flete Neto", False),
    ("Impuestos y Derechos", "Impuestos y Derechos", False),
    ("Gastos Despacho", "Gastos Despacho", True),
    ("Otros Gastos (Productos)", "Otros Gastos (Productos)", False),
    ("Servicios Admin. Back Office", "Servicios Administrativos Back Office", False),
    ("Gastos Extraordinarios", "Gastos Extraordinarios", False),
    ("Costo Activo Fijo", "Costo Activo Fijo", True),
    ("Costo Inventario", "Costo Inventario", True),
]
CUENTAS_COMO_GRUPO = {"Gastos Despacho", "Costo Activo Fijo", "Costo Inventario"}
SUMAN_AL_TOTAL = ["Gastos de Personal", "Gastos de Publicidad", "Gastos de Operación",
                  "Renta, Luz y Teléfono", "Flete Neto", "Impuestos y Derechos", "Gastos Despacho",
                  "Otros Gastos (Productos)", "Servicios Administrativos Back Office",
                  "Gastos Extraordinarios", "Costo Activo Fijo", "Costo Inventario"]


# --------------------------------------------------------------------------- datos
class Linea:
    """Una línea de tabla: etiqueta + valores por columna (millones, gasto negativo)."""

    def __init__(self, label, w=None, y=None, kind="normal", xw=None, xy=None):
        self.label, self.w, self.y, self.kind, self.xw, self.xy = label, w, y, kind, xw, xy


def sumar(filas_por_canal: list[list[dx.Fila]], avisos: list) -> list[dx.Fila]:
    """Suma fila a fila (mismo layout en las 4 hojas). Total Gasto se normaliza: cada hoja lo nombra distinto."""
    base = filas_por_canal[0]
    out = []
    for i, f in enumerate(base):
        for fs in filas_por_canal[1:]:
            if fs[i].cuenta != f.cuenta and fs[i].label == f.label:
                avisos.append(f"fila {i + 1}: la cuenta es '{f.cuenta}' en Shopinbaz y '{fs[i].cuenta}' en otra hoja "
                              f"(misma etiqueta '{f.label}'); se suman en la misma fila")
        w = {k: 0.0 for k in dx.W}
        y = {k: 0.0 for k in dx.Y}
        xw = {"plan": 0.0, "semant": 0.0, "r25": 0.0}
        xy = {"plan": 0.0, "r25": 0.0}
        for fs in filas_por_canal:
            g = fs[i]
            if f.label.startswith("Total Gasto"):
                assert g.label.startswith("Total Gasto"), f"filas desalineadas: {g.label} vs {f.label}"
            else:
                assert g.label == f.label, f"filas desalineadas: {g.label} vs {f.label}"
            for k in w: w[k] += g.w[k]
            for k in y: y[k] += g.y[k]
            for k in xw: xw[k] += g.xw[k]
            for k in xy: xy[k] += g.xy[k]
        out.append(dx.Fila(f.cuenta, f.label, w, y, xw, xy))
    return out


def buscar(filas, label, cuenta=None):
    for f in filas:
        if f.label == label and (cuenta is None or bool(f.cuenta) == cuenta):
            return f
    return None


def total_de(filas):
    cand = [f for f in filas if f.es_grupo and f.label.startswith("Total Gasto")]
    return cand[-1] if cand else None


def lineas_grupo(filas, periodo: str, avisos: list, nombre: str):
    """Filas por grupo de cuentas + Total. Devuelve lista de Linea."""
    campo = "w" if periodo == "w" else "y"
    tot = total_de(filas)
    lin = []
    for mostrar, lab, es_cta in FILAS_GRUPO:
        f = buscar(filas, lab, cuenta=es_cta)
        if f is None:
            avisos.append(f"[{nombre}] falta la fila '{lab}'")
            continue
        lin.append(Linea(mostrar, getattr(f, campo), getattr(f, campo), kind="normal"))
    # diferencia contra el Total del Excel (si hay) -> fila de ajuste para que la tabla cuadre
    sumar_filas = [buscar(filas, l, cuenta=(l in CUENTAS_COMO_GRUPO)) for l in SUMAN_AL_TOTAL]
    ks = dx.W if periodo == "w" else dx.Y
    dif = {k: getattr(tot, campo)[k] - sum(getattr(f, campo)[k] for f in sumar_filas if f) for k in ks}
    if any(abs(v) > 0.005 for v in dif.values()):
        avisos.append(f"[{nombre}/{periodo}] Total del Excel - suma de grupos = "
                      + ", ".join(f"{k}:{v:+.3f}" for k, v in dif.items()))
        lin.append(Linea("Otros (diferencia vs. Total)", dif, dif))
    lin.append(Linea("Total", getattr(tot, campo), getattr(tot, campo), kind="total"))
    return _sin_ceros(lin, periodo)


def _sin_ceros(lin, periodo):
    out = []
    for l in lin:
        vals = l.w
        if l.kind == "total" or any(abs(v) > 0.0049 for v in vals.values()):
            out.append(l)
    return out


def lineas_detalle(filas, periodo: str):
    """Cuentas + subtotales de grupo en el orden del Excel, sin filas en cero."""
    campo = "w" if periodo == "w" else "y"
    out = []
    for f in filas:
        vals = getattr(f, campo)
        if all(abs(v) <= 0.0049 for v in vals.values()):
            continue
        if f.label.startswith("Total Gasto"):
            out.append(Linea("Total", vals, vals, kind="total"))
        elif f.es_grupo:
            out.append(Linea(f.label.replace("Gastos de Nomina", "Gastos de Nómina"), vals, vals, kind="sub"))
        else:
            out.append(Linea(f.label, vals, vals, kind="normal"))
    return out


# ------------------------------------------------------------------------- formato
def fnum(x, dec=2):
    v = -x                                   # gasto: positivo
    if round(v, dec) == 0:
        return f"{0:.{dec}f}"
    return f"{v:.{dec}f}" if v > 0 else f"({abs(v):.{dec}f})"


def fvar(x, dec=2):
    if round(x, dec) == 0:
        return f"{0:.{dec}f}"
    return f"{x:.{dec}f}" if x > 0 else f"({abs(x):.{dec}f})"


def fpct(x, dec=1):
    p = x * 100
    if abs(p) >= 100:
        dec = 0
    if round(p, dec) == 0:
        return f"{0:.{dec}f}%"
    return f"{p:.{dec}f}%" if p > 0 else f"({abs(p):.{dec}f}%)"


def fshare(x, base):
    if not base:
        return "0%"
    return f"{abs(x / base) * 100:.0f}%"


def var(real, comp):
    v = real - comp
    return v, (v / abs(comp) if abs(comp) > 1e-9 else 0.0)


# --------------------------------------------------------------------------- pptx
def _t_set(el, text):
    """Deja un solo párrafo/run con `text` conservando el formato del primero."""
    ps = el.findall(".//" + A + "p")
    for p in ps[1:]:
        p.getparent().remove(p)
    rs = ps[0].findall(A + "r")
    for r in rs[1:]:
        ps[0].remove(r)
    rs[0].find(A + "t").text = text


def _tc_style(tc, text, color=None, bold=None, fill=None):
    _t_set(tc, text)
    rpr = tc.find(".//" + A + "rPr")
    if bold is not None:
        rpr.set("b", "1" if bold else "0")
    if color:
        rpr.find(A + "solidFill/" + A + "srgbClr").set("val", color)
    if fill:
        tc.find(A + "tcPr/" + A + "solidFill/" + A + "srgbClr").set("val", fill)


class Constructor:
    def __init__(self, base_pptx: Path, sem: int, anexar: bool = False):
        """anexar=True: conserva las láminas de físicos y agrega las de digitales al final (consolidada)."""
        self.prs = Presentation(str(base_pptx))
        self.sem = sem
        self.anexar = anexar
        sl = list(self.prs.slides)
        self.portada, proto = sl[0], sl[1]
        self.blank = next(l for l in self.prs.slide_layouts if l.name == "Blank")
        sh = {s.name: s for s in proto.shapes}
        self.t_title = copy.deepcopy(sh["TextBox 1"]._element)
        self.t_sub = copy.deepcopy(sh["TextBox 2"]._element)
        pic = sh["Picture 3"]
        self.logo = (pic.image.blob, pic.left, pic.top, pic.width, pic.height)
        self.t_kpi = [(copy.deepcopy(sh[f"Rectangle {r}"]._element), copy.deepcopy(sh[f"TextBox {r+1}"]._element),
                       copy.deepcopy(sh[f"TextBox {r+2}"]._element)) for r in (4, 7, 10, 13)]
        self.t_menu = {n: copy.deepcopy(s._element) for n, s in sh.items() if n.startswith("Menu ·")}
        frame = [s for s in proto.shapes if s.has_table][0]
        self.t_frame = copy.deepcopy(frame._element)
        tbl = self.t_frame.find(".//" + A + "tbl")
        trs = tbl.findall(A + "tr")
        self.t_hdr = [copy.deepcopy(t) for t in trs[:3]]
        self.t_row = copy.deepcopy(trs[3])
        self.t_tot = copy.deepcopy(trs[-1])
        self.grid = [int(g.get("w")) for g in tbl.find(A + "tblGrid").findall(A + "gridCol")]
        txt = "".join(t.text or "" for t in trs[0].findall(".//" + A + "tc")[8].iter(A + "t"))
        m = re.search(r"Del (\d+) al (\d+) de (\w+)", txt)
        self.rango = m.group(0) if m else ""
        self.fin = f"{m.group(2)} de {m.group(3)}" if m else ""
        self.slides: list[tuple[str, object]] = []          # (sección, slide)
        # fuera todo salvo la portada (la lámina modelo también: ya tenemos sus piezas)
        if anexar:
            return
        lst = self.prs.slides._sldIdLst
        for sid in list(lst)[1:]:
            self.prs.part.drop_rel(sid.rId)
            lst.remove(sid)
        self._portada()

    def _portada(self):
        sp = [s for s in self.portada.shapes if s.has_text_frame][0]
        ps = sp._element.findall(".//" + A + "p")
        for p, txt in zip(ps, ["Canales digitales", f"Gasto semana {self.sem} y", f"Acumulado a semana {self.sem}"]):
            rs = p.findall(A + "r")
            for r in rs[1:]:
                p.remove(r)
            rs[0].find(A + "t").text = txt
        self.portada.name = "Portada"

    # ---- piezas
    def _slide(self, seccion, titulo, sub):
        s = self.prs.slides.add_slide(self.blank)
        tree = s.shapes._spTree
        for el in (self.t_title, self.t_sub):
            c = copy.deepcopy(el)
            tree.append(c)
        tit, st = list(tree)[-2:]
        _t_set(tit, titulo)
        _t_set(st, sub)
        # títulos largos: letra más chica para que quepan en una línea sin pisar el subtítulo
        rpr = tit.find(".//" + A + "rPr")
        if rpr is not None and rpr.get("sz") and len(titulo) > 30:
            rpr.set("sz", str(max(1800, int(int(rpr.get("sz")) * 30 / len(titulo)))))
        blob, l, t, w, h = self.logo
        s.shapes.add_picture(io.BytesIO(blob), l, t, w, h)
        self._menu(s, seccion)
        self.slides.append((seccion, s))
        return s

    def _menu(self, slide, activa):
        tree = slide.shapes._spTree
        cab = copy.deepcopy(self.t_menu["Menu · Canales físicos"])
        sem = copy.deepcopy(self.t_menu["Menu · Semana 39"])
        tree.append(copy.deepcopy(self.t_menu["Menu · Panel"]))
        for el, txt, nombre in ((cab, "Canales digitales", "Menu · Inicio"), (sem, f"Semana {self.sem}", "Menu · Semana")):
            for h in el.findall(".//" + A + "hlinkClick"):
                h.getparent().remove(h)
            _t_set(el, txt)
            el.find(".//{%s}cNvPr" % NS["p"]).set("name", nombre)
            tree.append(el)
        y = 1133856
        for sec in [s for s in SECCIONES if s != SEC_TOTAL or self.anexar]:
            el = copy.deepcopy(self.t_menu["Menu · Por concepto" if sec == activa else
                                           next(n for n in ("Menu · Por Dirección", "Menu · Por geografía") if n in self.t_menu)])
            for h in el.findall(".//" + A + "hlinkClick"):
                h.getparent().remove(h)
            _t_set(el, sec)
            cnv = el.find(".//{%s}cNvPr" % NS["p"])
            cnv.set("name", f"Menu · {sec}")
            off = el.find(".//" + A + "off")
            ext = el.find(".//" + A + "ext")
            off.set("y", str(y))
            off.set("x", "420624")
            ext.set("cy", "329184" if sec == activa else "310896")
            tree.append(el)
            y += 384048
        # ids únicos
        for i, e in enumerate(tree.iter("{%s}cNvPr" % NS["p"]), 2):
            e.set("id", str(i))

    def _kpis(self, slide, tarjetas):
        tree = slide.shapes._spTree
        for (rect, val, lab), (valor, etiqueta) in zip(self.t_kpi, tarjetas):
            for el in (rect, val, lab):
                tree.append(copy.deepcopy(el))
            v, lb = list(tree)[-2:]
            _t_set(v, valor)
            _t_set(lb, etiqueta)

    def _tabla(self, slide, y, lineas, cabecera, kind, share_base=None, dec=2, alto_fila=328646, real_label=None):
        """kind: 'w' semana (15 col) | 'y' acumulado (11 col)."""
        frame = copy.deepcopy(self.t_frame)
        tbl = frame.find(".//" + A + "tbl")
        for tr in tbl.findall(A + "tr"):
            tbl.remove(tr)
        hdr = [copy.deepcopy(t) for t in self.t_hdr]
        # textos de cabecera
        tcs0 = hdr[0].findall(A + "tc")
        _t_set(tcs0[8], cabecera)                      # una sola línea (se funde en 3 filas)
        for tr in hdr:
            tbl.append(tr)
        if real_label:
            _t_set(tcs0[6], real_label)
        for i, ln in enumerate(lineas):
            tr = copy.deepcopy(self.t_tot if ln.kind in ("total", "sub") else self.t_row)
            tcs = tr.findall(A + "tc")
            fill = {"total": "D9D9D9", "sub": "D9D9D9"}.get(ln.kind, FILA_BAJA if i % 2 == 0 else FILA_ALTA)
            for tc in tcs:
                tc.find(A + "tcPr/" + A + "solidFill/" + A + "srgbClr").set("val", fill)
            bold = ln.kind in ("total", "sub")
            if kind == "w":
                v = ln.w
                cols = [("r25", 0, 1), ("semant", 2, 3), ("plan", 4, 5), ("real", 6, 7)]
            else:
                v = ln.y
                cols = [("r25", 0, 1), ("plan", 4, 5), ("real", 6, 7)]
            for k, c_v, c_p in cols:
                _tc_style(tcs[c_v], fnum(v[k], dec), color=GRIS, bold=bold)
                if share_base is not None:
                    _tc_style(tcs[c_p], fshare(v[k], share_base[k]), color=GRIS_CLARO, bold=bold)
                else:
                    _tc_style(tcs[c_p], "", color=GRIS_CLARO, bold=bold)
            if kind == "y":                              # columnas de sem. anterior (2,3) se quitan después
                pass
            _tc_style(tcs[8], ln.label, color=GRIS, bold=bold)
            for (c_v, c_p), comp in (((9, 10), "plan"), ((11, 12), "semant"), ((13, 14), "r25")):
                if kind == "y" and comp == "semant":
                    continue
                d, pc = var(v["real"], v[comp])
                col = ROJO if d < -0.0049 else GRIS
                _tc_style(tcs[c_v], fvar(d, dec), color=col, bold=(d < -0.0049) or bold)
                _tc_style(tcs[c_p], fpct(pc), color=col, bold=(d < -0.0049) or bold)
            tr.set("h", str(alto_fila))
            tbl.append(tr)
        for tr in hdr:
            tr.set("h", "256032")
        if kind == "y":
            self._quitar_semant(tbl)
        # geometría
        ancho = sum(int(g.get("w")) for g in tbl.find(A + "tblGrid").findall(A + "gridCol"))
        off = frame.find(".//" + A + "off"); ext = frame.find(".//" + A + "ext")
        off.set("x", "2606040"); off.set("y", str(y))
        ext.set("cx", str(ancho)); ext.set("cy", str(768096 + alto_fila * len(lineas)))
        frame.find(".//{%s}cNvPr" % NS["p"]).set("name", "Tabla")
        slide.shapes._spTree.append(frame)

    def _quitar_semant(self, tbl):
        quitar = [2, 3, 11, 12]
        grid = tbl.find(A + "tblGrid").findall(A + "gridCol")
        for i in sorted(quitar, reverse=True):
            grid[i].getparent().remove(grid[i])
        for tr in tbl.findall(A + "tr"):
            tcs = tr.findall(A + "tc")
            for i in sorted(quitar, reverse=True):
                tr.remove(tcs[i])
        h0 = tbl.findall(A + "tr")[0].findall(A + "tc")
        for tc in h0:
            if tc.get("gridSpan") == "6":
                tc.set("gridSpan", "4")
        # el encabezado fundido de "Sem Anterior" ya no existe: la fila 0 conserva 4 pares (Año, Plan, Real) -> 3
        # (se retiraron las celdas 2 y 3 que eran Sem Anterior)
        # reparte el ancho sobrante para que la tabla siga ocupando el ancho de la lámina
        grid = tbl.find(A + "tblGrid").findall(A + "gridCol")
        total = sum(self.grid)
        etq = next(g for g in grid if int(g.get("w")) > 2000000)
        resto = total - int(etq.get("w"))
        n = len(grid) - 1
        for g in grid:
            if g is not etq:
                g.set("w", str(resto // n))


# ------------------------------------------------------------------ construcción
def kpi_semana(tot, sem):
    v = tot.w
    dp, pp = var(v["real"], v["plan"])
    d25, p25 = var(v["real"], v["r25"])
    ds, ps = var(v["real"], v["semant"])
    return [(f"${-v['real']:,.1f} M", f"Real semana {sem}"),
            (f"${-v['plan']:,.1f} M", f"Presupuesto · var. {fvar(dp, 1)} · {fpct(pp)}"),
            (f"${-v['r25']:,.1f} M", f"Año anterior · var. {fvar(d25, 1)} · {fpct(p25)}"),
            (f"${-v['semant']:,.1f} M", f"Semana {sem-1} · var. {fvar(ds, 1)} · {fpct(ps)}")]


def kpi_acum(tot, sem):
    v = tot.y
    dp, pp = var(v["real"], v["plan"])
    d25, p25 = var(v["real"], v["r25"])
    return [(f"${-v['real']:,.1f} M", f"Real acumulado a sem. {sem}"),
            (f"${-v['plan']:,.1f} M", f"Presupuesto · var. {fvar(dp, 1)} · {fpct(pp)}"),
            (f"${-v['r25']:,.1f} M", f"Año anterior · var. {fvar(d25, 1)} · {fpct(p25)}")]


def kpi_4sem(tot, sem):
    v = tot.y
    dp, pp = var(v["real"], v["plan"])
    d25, p25 = var(v["real"], v["r25"])
    return [(f"${-v['real']:,.1f} M", f"Real sem. {sem-3} a {sem}"),
            (f"${-v['plan']:,.1f} M", f"Presupuesto · var. {fvar(dp, 1)} · {fpct(pp)}"),
            (f"${-v['r25']:,.1f} M", f"Año anterior · var. {fvar(d25, 1)} · {fpct(p25)}")]


def validar(d, dig_filas, avisos):
    """Contrastes contra el propio Excel. Devuelve dict con resultados."""
    res = {"sumas_canales_vs_total_digital": {}, "variaciones": {}}
    ct = d["canales_total"]
    td = next(f for f in ct if f.label == "Total Digital")
    tdig = total_de(dig_filas)
    for per, campos in (("w", dx.W), ("y", dx.Y)):
        for k in campos:
            a, b = getattr(tdig, per)[k], getattr(td, per)[k]
            res["sumas_canales_vs_total_digital"][f"{per}.{k}"] = {"suma_4_canales": round(a, 4), "excel_total_digital": round(b, 4),
                                                                   "dif": round(a - b, 4)}
    # variaciones recalculadas vs variaciones del Excel (todas las filas de todos los canales)
    peor = {"w": 0.0, "y": 0.0}
    n = 0
    for k, filas in d["canales"].items():
        for f in filas:
            for per, xs, comps in (("w", f.xw, ("plan", "semant", "r25")), ("y", f.xy, ("plan", "r25"))):
                vals = f.w if per == "w" else f.y
                for c in comps:
                    mio = vals["real"] - vals[c]
                    peor[per] = max(peor[per], abs(mio - xs[c]))
                    n += 1
    res["variaciones"] = {"celdas_comparadas": n, "max_dif_semana": round(peor["w"], 6), "max_dif_acumulado": round(peor["y"], 6)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--excel", default=None, help="si se omite: el Excel de la semana más reciente (Descargas / Gasto semanal)")
    ap.add_argument("--semana", type=int, default=None)
    ap.add_argument("--base", default=None, help="pptx de canales físicos que se usa como diseño")
    ap.add_argument("--salida", default=None)
    ap.add_argument("--excel-previo", default=None, help="Excel de la sem. N-4 (si no, se busca solo)")
    ap.add_argument("--consolidar", action="store_true",
                    help="una sola presentación: láminas de físicos (--base) + digitales, con saltos entre ambas")
    ap.add_argument("--fuente", choices=("excel", "sql"), default="sql",
                    help="sql (default): datos/digitales/*.parquet; excel: fuente alterna desde el Excel oficial")
    a = ap.parse_args()
    if a.fuente == "sql" and not a.semana:
        a.semana = json.loads((Path(__file__).resolve().parents[2] / "aggs" / "_meta.json")
                              .read_text(encoding="utf-8-sig"))["sem_max_real"]
    if a.fuente == "excel" and a.excel is None:
        carpetas = [Path.home() / "Downloads", GASTO_SEMANAL]
        sems = sorted({int(m.group(1)) for c in carpetas if c.is_dir() for f in c.glob("Gastos Red Garg (sem *)*.xlsx")
                       if (m := re.search(r"\(sem (\d+)\)", f.name, re.I))}, reverse=True)
        a.semana = a.semana or (sems[0] if sems else None)
        xl = buscar_excel_semana(a.semana, carpetas) if a.semana else None
        if xl is None:
            raise SystemExit("No encontré el Excel 'Gastos Red Garg (sem NN)' de canales digitales; usa --excel")
        a.excel = str(xl)
        print(f"Excel sem. {a.semana}: {xl}")
    sem = a.semana or int(re.search(r"\(sem (\d+)\)", a.excel, re.I).group(1))
    base = Path(a.base) if a.base else SALIDAS / f"Gasto_semana_{sem}.pptx"
    salida = Path(a.salida) if a.salida else SALIDAS / (
        f"Gasto_consolidado_semana_{sem}.pptx" if a.consolidar else f"Gasto_digitales_semana_{sem}.pptx")

    avisos: list[str] = []
    if a.fuente == "sql":
        from .digitales_sql import cargar_sql
        ds = cargar_sql(sem, avisos)
        d = {"canales": ds["canales"], "canales_total": None, "temp": {}}
    else:
        d = dx.cargar(Path(a.excel))
    dig = sumar([d["canales"][k] for k in dx.CANALES], avisos)
    ents = {"dig": ("Canales Digitales", dig)}
    for k, (_, _, nom) in dx.CANALES.items():
        ents[k] = (nom, d["canales"][k])

    # ---- últimas 4 semanas = acumulado(N) - acumulado(N-4) ----
    ct = d["canales_total"]
    ct4 = None
    if a.fuente == "sql":                      # la ventana de 4 semanas sale directo de las tablas
        f4 = ds["ventana4"]
    elif sem > 4:
        p_xlsx = Path(a.excel_previo) if a.excel_previo else buscar_excel_semana(sem - 4, [Path(a.excel).parent, GASTO_SEMANAL])
        if p_xlsx is None:
            raise SystemExit(f"No encontré el Excel de la semana {sem - 4} (necesario para las 4 semanas); "
                             f"pásalo con --excel-previo")
        print(f"Excel sem. {sem - 4} (base 4 semanas): {p_xlsx}")
        dp_ = dx.cargar(p_xlsx)
        for k in dx.CANALES:
            if not dp_["canales"][k]:
                avisos.append(f"[{dx.CANALES[k][2]}] el Excel de la sem. {sem - 4} no tiene hoja de este canal: "
                              f"las 4 semanas llevan su acumulado completo")
        f4 = {k: restar_acum(d["canales"][k], dp_["canales"][k] or None, avisos, k) for k in dx.CANALES}
        ct4 = restar_acum(ct, dp_["canales_total"], avisos, "Canales")
    else:
        f4 = {k: restar_acum(d["canales"][k], None, avisos, k) for k in dx.CANALES}
        ct4 = restar_acum(ct, None, avisos, "Canales")
    dig4 = sumar(list(f4.values()), avisos)
    ents4 = {"dig": ("Canales Digitales", dig4), **{k: (dx.CANALES[k][2], f4[k]) for k in dx.CANALES}}

    # ---- pestaña «Físicos + Digitales» (solo en la consolidada): físicos del tablero + digitales ----
    if a.consolidar:
        from .total_canales import cargar_fisicos
        fis, fis4 = cargar_fisicos(sem)
        ents["tot"] = ("Físicos + Digitales", sumar([fis, dig], avisos))
        ents4["tot"] = ("Físicos + Digitales", sumar([fis4, dig4], avisos))

    cb = Constructor(base, sem, anexar=a.consolidar)
    ini_w, fin_w = inicio_semana(sem), fin_semana(sem)
    ini_4 = inicio_semana(sem - 3)
    cifras = "Cifras en millones de pesos constantes"
    sub_w = f"Semana {sem} · {cifras} · {rango_texto(ini_w, fin_w)}"
    sub_4 = f"Últimas 4 semanas: semana {sem - 3} a semana {sem} · {cifras} · {rango_texto(ini_4, fin_w)}"
    sub_y = (f"Acumulado a semana {sem} · {cifras} · Del 29 de dic 2025 al {fin_w.day} de {MESES[fin_w.month - 1]}")

    # (clave, kind de tabla, subtítulo, título, etiqueta columna, kpi, filas por entidad, total red/canales)
    PER = [("w", "w", sub_w, f"Semana {sem}", f"Semana {sem}-2026", kpi_semana, ents, None),
           ("4", "y", sub_4, f"Últimas 4 semanas (sem. {sem - 3}-{sem})", f"Sem. {sem - 3} a {sem}", kpi_4sem, ents4, ct4),
           ("y", "y", sub_y, "Acumulado", f"Acumulado sem. {sem}", kpi_acum, ents, ct)]

    # ---- Resumen por canal ----
    for clave, per, sub, ttl, etq, kp, en, ctp in PER:
        dg = en["dig"][1]
        tot_dig = total_de(dg)
        ctx = ctp if ctp is not None else ct
        if ctx is not None:
            red = next(f for f in ctx if f.label == "Total Red GARG")
            can = [f for f in ctx if f.label == "Total Gasto Canales"][-1]
        s = cb._slide("Resumen", f"Resumen por canal digital · {ttl}", sub)
        cb._kpis(s, kp(tot_dig, sem))
        lins = []
        for k, (_, _, nom) in dx.CANALES.items():
            t = total_de(en[k][1])
            lins.append(Linea(nom, t.w, t.y))
        lins.append(Linea("Total Digital", tot_dig.w, tot_dig.y, kind="total"))
        if ctx is not None:
            lins.append(Linea("Total Red GARG", red.w, red.y, kind="sub"))
            lins.append(Linea("Total Canales", can.w, can.y, kind="sub"))
            base_pct, etq_res = (can.w if per == "w" else can.y), "Gasto · % s/ Total Canales"
        else:                                  # SQL digital no trae Red/Canales: % sobre Total Digital
            base_pct, etq_res = (tot_dig.w if per == "w" else tot_dig.y), "Gasto · % s/ Total Digital"
        cb._tabla(s, 3520440, lins, etq_res, per,
                  share_base=base_pct, dec=2, real_label=None)

    # ---- Por entidad ----
    orden = (["tot"] if a.consolidar else []) + ["dig", "shop", "baz", "arg", "dgc"]
    secc = {"tot": SEC_TOTAL, "dig": "Consolidado", "shop": "Shopinbaz", "baz": "Baz Digital", "arg": "Aurora Digital", "dgc": "DG Canales Dig."}
    for k in orden:
        for clave, per, sub, ttl, etq, kp, en, _ in PER:
            nom, filas = en[k]
            tot = total_de(filas)
            s = cb._slide(secc[k], f"{nom} · {ttl}", sub)
            cb._kpis(s, kp(tot, sem))
            lins = lineas_grupo(filas, per, avisos, nom)
            cb._tabla(s, 3520440, lins, etq, per, share_base=getattr(tot, per))

    # ---- hipervínculos del menú ----
    primera = {}
    for sec, sl in cb.slides:
        primera.setdefault(sec, sl)
    inicio = primera["Resumen"]
    for sec, sl in cb.slides:
        for shp in sl.shapes:
            if shp.name == "Menu · Inicio":
                shp.click_action.target_slide = inicio
            elif shp.name.startswith("Menu · ") and shp.name[7:] in primera:
                shp.click_action.target_slide = primera[shp.name[7:]]

    if a.consolidar:
        from .consolidada import finalizar
        finalizar(cb)
    # guarda anti-repetidas: dos láminas con el mismo título+subtítulo = error, no se guarda
    vistos: dict[str, int] = {}
    for n, sl in enumerate(cb.prs.slides, 1):
        txt = [sh.text_frame.text.strip() for sh in sl.shapes
               if sh.has_text_frame and sh.text_frame.text.strip() and not sh.name.startswith("Menu")]
        clave = " | ".join(txt[:2])
        if clave in vistos:
            raise SystemExit(f"Láminas repetidas ({vistos[clave]} y {n}): {clave[:90]}")
        vistos[clave] = n
    salida.parent.mkdir(parents=True, exist_ok=True)
    cb.prs.save(str(salida))

    val = validar(d, dig, avisos) if a.fuente == "excel" else {"fuente": "sql"}
    val["avisos"] = avisos
    val["laminas"] = len(cb.prs.slides)
    (salida.with_suffix(".validacion.json")).write_text(json.dumps(val, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK {salida}  ({val['laminas']} láminas)")
    print(json.dumps(val, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

