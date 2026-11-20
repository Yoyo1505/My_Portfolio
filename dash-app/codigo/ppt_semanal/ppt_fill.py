# -*- coding: utf-8 -*-
"""
ppt_fill.py — Rellena la plantilla .pptx editando solo el XML de texto.

Garantías de formato:
  * El .pptx se reescribe copiando cada parte del zip tal cual; solo se serializan
    de nuevo las láminas (slideN.xml). Imágenes, logos, layouts, hipervínculos
    (rels), notas y el objeto OLE quedan byte a byte iguales.
  * En las tablas solo cambia el <a:t> de las celdas dinámicas y, cuando el signo
    cambia, el color/negrita del primer run (regla verificada contra la semana 39).
  * No se agregan ni quitan filas, columnas, formas ni láminas.

Qué es "dinámico": una celda de la tabla es dinámica si la celda equivalente de la
hoja L## del Excel es una fórmula. Todo lo demás (rótulos, encabezados) es estático
salvo los textos de semana/fecha, que se sustituyen por expresión regular.
"""
from __future__ import annotations

import collections
import copy
import re
import zipfile
from pathlib import Path

from lxml import etree
from openpyxl.utils import get_column_letter

from .fmt import FMT_KPI, format_value
from .xl_eval import Book, ExcelErr

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"a": A, "p": P, "r": R}

ROJO_VAR, ROJO_BASE, GRIS_TXT, FILL_VAR = "D10609", "E00000", "3F3F3F", "FFF2CC"
RE_NUM = re.compile(r"^\(?[\d,]*\.?\d+%?\)?$|^n\.a\.$")
RE_KPI_VAL = re.compile(r"^\(?\$[\d,]+\.\d M\)?$")
RE_FECHA = re.compile(r"^Del \d{1,2}(?: de [a-záéíóú]+)? al \d{1,2} de [a-záéíóú]+$")           # semana
RE_FECHA_ACUM = re.compile(r"^Del \d{1,2} de [a-z]{3} \d{4} al \d{1,2} de [a-z]{3}$")           # acumulado
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def texto_fechas(ini, fin) -> str:
    """Rango de la semana: 'Del 21 al 27 de septiembre'. Si cruza de mes se abrevia ('Del 28 sep al 4 oct')
    porque el encabezado de la tabla es angosto y una fecha larga se parte en dos renglones."""
    if ini.month == fin.month:
        return f"Del {ini.day} al {fin.day} de {MESES[fin.month - 1]}"
    return f"Del {ini.day} {MESES[ini.month - 1][:3]} al {fin.day} {MESES[fin.month - 1][:3]}"


def texto_fechas_acum(ini_anio, fin) -> str:
    """Rango del acumulado: 'Del 29 de dic 2025 al 27 de sep'."""
    return (f"Del {ini_anio.day} de {MESES[ini_anio.month - 1][:3]} {ini_anio.year} "
            f"al {fin.day} de {MESES[fin.month - 1][:3]}")


# ---------------------------------------------------------------- paquete pptx
class Deck:
    def __init__(self, path: Path):
        self.path = Path(path)
        with zipfile.ZipFile(self.path) as z:
            self.infos = z.infolist()
            self.data = {i.filename: z.read(i.filename) for i in self.infos}
        self.order = self._orden_laminas()
        self.trees: dict[str, etree._Element] = {}

    def _orden_laminas(self) -> list[str]:
        pres = etree.fromstring(self.data["ppt/presentation.xml"])
        rels = etree.fromstring(self.data["ppt/_rels/presentation.xml.rels"])
        rid2t = {r.get("Id"): r.get("Target") for r in rels}
        out = []
        for sld in pres.xpath("//p:sldIdLst/p:sldId", namespaces=NS):
            t = rid2t[sld.get("{%s}id" % R)]
            out.append(t if t.startswith("ppt/") else "ppt/" + t.lstrip("/"))
        return out

    def __len__(self) -> int:
        return len(self.order)

    def slide(self, idx: int):
        name = self.order[idx - 1]
        if name not in self.trees:
            self.trees[name] = etree.fromstring(self.data[name])
        return self.trees[name]

    def titulo_portada(self, sem: int):
        """Portada: 'Canales físicos' / 'Gasto semana NN y' / 'Acumulado a semana NN'."""
        import copy
        ps = self.slide(1).xpath("//p:sp//a:p", namespaces=NS)
        sub = ps[-1]
        nuevo = copy.deepcopy(sub)
        sub.getparent().append(nuevo)
        for p, txt in ((sub, f"Gasto semana {sem} y"), (nuevo, f"Acumulado a semana {sem}")):
            ts = p.findall(".//a:t", NS)
            ts[0].text = txt
            for t in ts[1:]:
                t.text = ""

    def quitar_lamina(self, idx: int):
        """Elimina la lámina idx (lista, rel, content-types y partes). Llamar al final, tras validar."""
        name = self.order.pop(idx - 1)
        base = name.rsplit("/", 1)[-1]
        rels_name = f"ppt/slides/_rels/{base}.rels"
        pres = etree.fromstring(self.data["ppt/presentation.xml"])
        rels = etree.fromstring(self.data["ppt/_rels/presentation.xml.rels"])
        rid = next(r.get("Id") for r in rels if (r.get("Target") or "").endswith("slides/" + base))
        for r in list(rels):
            if r.get("Id") == rid:
                rels.remove(r)
        for s in pres.xpath("//p:sldIdLst/p:sldId", namespaces=NS):
            if s.get("{%s}id" % R) == rid:
                s.getparent().remove(s)
        self.data["ppt/presentation.xml"] = etree.tostring(pres, xml_declaration=True, encoding="UTF-8", standalone=True)
        self.data["ppt/_rels/presentation.xml.rels"] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
        ct = etree.fromstring(self.data["[Content_Types].xml"])
        for o in list(ct):
            if o.get("PartName") == "/" + name:
                ct.remove(o)
        self.data["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)
        for n in (name, rels_name):
            self.data.pop(n, None)
            self.trees.pop(n, None)
        self.infos = [i for i in self.infos if i.filename not in (name, rels_name)]
        self.quitadas = getattr(self, "quitadas", []) + [name, rels_name]

    def clonar_lamina(self, idx: int, destino: int | None = None) -> int:
        """Duplica la lámina idx (con su estado actual) después de la lámina `destino` (por omisión, de ella misma);
        devuelve el índice de la copia. La copia comparte layout/imágenes/hipervínculos por rels;
        no se admiten láminas con notas."""
        destino = destino or idx
        src = self.order[idx - 1]
        dest = self.order[destino - 1]
        dbase = dest.rsplit("/", 1)[-1]
        base = src.rsplit("/", 1)[-1]
        rels_src = f"ppt/slides/_rels/{base}.rels"
        if b"notesSlide" in self.data[rels_src]:
            raise ValueError(f"{base}: no se puede clonar una lámina con notas")
        n = 1 + max(int(re.search(r"slide(\d+)\.xml$", k).group(1))
                    for k in self.data if re.match(r"ppt/slides/slide\d+\.xml$", k))
        new, new_rels = f"ppt/slides/slide{n}.xml", f"ppt/slides/_rels/slide{n}.xml.rels"
        ser = lambda t: etree.tostring(t, xml_declaration=True, encoding="UTF-8", standalone=True)
        self.data[new] = ser(self.slide(idx))
        self.data[new_rels] = self.data[rels_src]
        ct = etree.fromstring(self.data["[Content_Types].xml"])
        ov = next(o for o in ct if o.get("PartName") == "/" + src)
        nuevo_ov = copy.deepcopy(ov)
        nuevo_ov.set("PartName", "/" + new)
        ov.addnext(nuevo_ov)
        self.data["[Content_Types].xml"] = ser(ct)
        rels = etree.fromstring(self.data["ppt/_rels/presentation.xml.rels"])
        rid_src = next(r for r in rels if (r.get("Target") or "").endswith("slides/" + base))
        rid = "rId%d" % (1 + max(int(re.sub(r"\D", "", r.get("Id")) or 0) for r in rels))
        nr = copy.deepcopy(rid_src)
        nr.set("Id", rid)
        nr.set("Target", rid_src.get("Target").replace(base, f"slide{n}.xml"))
        rid_src.addnext(nr)
        self.data["ppt/_rels/presentation.xml.rels"] = ser(rels)
        pres = etree.fromstring(self.data["ppt/presentation.xml"])
        lst = pres.xpath("//p:sldIdLst/p:sldId", namespaces=NS)
        rid_dest = next(r for r in rels if (r.get("Target") or "").endswith("slides/" + dbase)).get("Id")
        s_dest = next(s for s in lst if s.get("{%s}id" % R) == rid_dest)
        s_new = copy.deepcopy(s_dest)
        s_new.set("{%s}id" % R, rid)
        s_new.set("id", str(1 + max(int(s.get("id")) for s in lst)))
        s_dest.addnext(s_new)
        self.data["ppt/presentation.xml"] = ser(pres)
        for nombre in (new, new_rels):
            zi = zipfile.ZipInfo(nombre, date_time=self.infos[0].date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            self.infos.append(zi)
        self.order.insert(destino, new)
        self.trees[new] = etree.fromstring(self.data[new])
        self.agregadas = getattr(self, "agregadas", []) + [new, new_rels]
        return destino + 1

    def quitar_enlaces_a(self, bases_quitadas: set[str]) -> int:
        """Elimina de todas las láminas los botones de menú (y rels) que apuntan a láminas quitadas.
        Los botones de menú de abajo suben para cubrir el hueco. Devuelve cuántos botones quitó."""
        quitados = 0
        for name in self.order:
            base = name.rsplit("/", 1)[-1]
            rels_name = f"ppt/slides/_rels/{base}.rels"
            rels = etree.fromstring(self.data[rels_name])
            malos = {r.get("Id") for r in rels
                     if (r.get("Type") or "").endswith("/slide") and (r.get("Target") or "").rsplit("/", 1)[-1] in bases_quitadas}
            if not malos:
                continue
            root = self.trees.setdefault(name, etree.fromstring(self.data[name]))
            menu = [sp for sp in root.xpath("//p:sp", namespaces=NS)
                    if (sp.xpath("string(p:nvSpPr/p:cNvPr/@name)", namespaces=NS)).startswith("Menu")
                    and "Panel" not in sp.xpath("string(p:nvSpPr/p:cNvPr/@name)", namespaces=NS)]
            for h in root.xpath("//a:hlinkClick", namespaces=NS):
                if h.get("{%s}id" % R) not in malos:
                    continue
                sp = next((a for a in h.iterancestors("{%s}sp" % P)), None)
                if sp is not None and sp in menu:
                    y0 = _y_de(sp)
                    sp.getparent().remove(sp)
                    menu.remove(sp)
                    debajo = sorted((s for s in menu if _y_de(s) > y0), key=_y_de)
                    if debajo:
                        delta = _y_de(debajo[0]) - y0
                        for s in debajo:
                            s.find("p:spPr/a:xfrm/a:off", NS).set("y", str(_y_de(s) - delta))
                    quitados += 1
                else:
                    h.getparent().remove(h)
            for r in list(rels):
                if r.get("Id") in malos:
                    rels.remove(r)
            self.data[rels_name] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)
        return quitados

    def save(self, out: Path):
        out = Path(out)
        tmp = out.with_suffix(out.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w") as zo:
            for info in self.infos:
                payload = self.data[info.filename]
                if info.filename in self.trees:
                    payload = etree.tostring(self.trees[info.filename], xml_declaration=True,
                                             encoding="UTF-8", standalone=True)
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                zo.writestr(zi, payload)
        tmp.replace(out)


# ---------------------------------------------------------------- utilidades xml
def _txt(el) -> str:
    return "".join(t.text or "" for t in el.iterfind(".//a:t", NS))


def _tabla_grid(tbl):
    return [tr.findall("a:tc", NS) for tr in tbl.findall("a:tr", NS)]


def _fill_hex(tc):
    v = tc.xpath("./a:tcPr/a:solidFill/a:srgbClr/@val", namespaces=NS)
    return v[0] if v else None


def _rpr(tc):
    r = tc.xpath(".//a:r/a:rPr", namespaces=NS)
    return r[0] if r else None


def _color(rpr):
    v = rpr.xpath("./a:solidFill/a:srgbClr/@val", namespaces=NS)
    return v[0] if v else None


def _set_color(rpr, hexv: str):
    el = rpr.find("a:solidFill/a:srgbClr", NS)
    if el is None:
        raise ValueError("run sin color explícito: la plantilla cambió")
    el.set("val", hexv)


def _numerica(texto: str) -> bool:
    return bool(RE_NUM.match(texto.strip()))


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


# ---------------------------------------------------------------- estilos
class Estilos:
    """Estilos observados en la plantilla: lista blanca para validar y color 'positivo' por columna."""

    def __init__(self, deck: Deck):
        self.visto: dict[tuple, set] = collections.defaultdict(set)
        pos_color: dict[tuple, collections.Counter] = collections.defaultdict(collections.Counter)
        for i in range(1, len(deck) + 1):
            for tbl in deck.slide(i).xpath("//a:tbl", namespaces=NS):
                ncol = len(tbl.findall("a:tblGrid/a:gridCol", NS))
                for fila in _tabla_grid(tbl):
                    for ci, tc in enumerate(fila):
                        t = _txt(tc).strip()
                        rp = _rpr(tc)
                        if rp is None or not _numerica(t):
                            continue
                        neg = t.startswith("(")
                        self.visto[(ncol, ci, _fill_hex(tc), neg)].add((rp.get("b"), _color(rp)))
                        if not neg and _fill_hex(tc) != FILL_VAR:
                            pos_color[(ncol, ci)][_color(rp)] += 1
        self.pos_color = {k: c.most_common(1)[0][0] for k, c in pos_color.items()}

    def estilo(self, ncol, ci, fill, neg_plantilla, neg_nuevo, b_act, color_act):
        if neg_plantilla == neg_nuevo:
            return b_act, color_act
        if neg_nuevo:
            return ("1", ROJO_VAR) if fill == FILL_VAR else (b_act, ROJO_BASE)
        if fill == FILL_VAR:
            return "0", GRIS_TXT
        return b_act, self.pos_color.get((ncol, ci), GRIS_TXT)


# ---------------------------------------------------------------- texto por regex (run-aware)
def _reemplazar_en_parrafo(p, patron: re.Pattern, repl) -> bool:
    runs = p.findall("a:r", NS)
    if not runs:
        return False
    textos = [(r.find("a:t", NS).text or "") for r in runs]
    full = "".join(textos)
    ms = [m for m in patron.finditer(full) if m.end() > m.start()]
    if not ms:
        return False
    dueno = [i for i, t in enumerate(textos) for _ in t]
    nuevos = [""] * len(runs)
    pos = 0
    for m in ms:
        for k in range(pos, m.start()):
            nuevos[dueno[k]] += full[k]
        nuevos[dueno[m.start()]] += repl(m)
        pos = m.end()
    for k in range(pos, len(full)):
        nuevos[dueno[k]] += full[k]
    if nuevos == textos:
        return False
    for r, t in zip(runs, nuevos):
        r.find("a:t", NS).text = t
    return True


class Sustituciones:
    def __init__(self, sem_plantilla: int, sem: int, fechas_nuevas: str, fechas_acum: str):
        self.mapa = {sem_plantilla: sem, sem_plantilla - 1: sem - 1}
        self.fechas = fechas_nuevas
        self.fechas_acum = fechas_acum
        self.re_sem = re.compile(r"(?P<pre>[Ss]emana )(?P<n>\d+)(?!\d)|(?<![A-Za-z])(?P<pre2>S)(?P<n2>\d+)(?!\d)")
        self.cambios = 0

    def _rep(self, m):
        n = int(m.group("n") or m.group("n2"))
        pre = m.group("pre") or m.group("pre2")
        return f"{pre}{self.mapa.get(n, n)}"

    def aplicar(self, root) -> int:
        n = 0
        for p in root.iterfind(".//a:p", NS):
            t = _txt(p)
            if not t or _numerica(t):
                continue
            nueva = self.fechas if RE_FECHA.match(t.strip()) else (
                self.fechas_acum if RE_FECHA_ACUM.match(t.strip()) else None)
            if nueva is not None:
                runs = p.findall("a:r", NS)
                if runs and t.strip() != nueva:
                    runs[0].find("a:t", NS).text = nueva
                    for r in runs[1:]:
                        p.remove(r)
                    n += 1
                continue
            if _reemplazar_en_parrafo(p, self.re_sem, self._rep):
                n += 1
        self.cambios += n
        return n


# ---------------------------------------------------------------- tabla <-> hoja
def mapa_hojas(book: Book) -> dict[int, str]:
    m = {}
    for nombre in book.wb.sheetnames:
        mm = re.match(r"^L(\d{2})\b", nombre)
        if mm:
            m[int(mm.group(1))] = nombre
    return m


def _localizar_region(book: Book, hoja: str, grid_txt, ncol, nrow):
    """Fila inicial (1-based) de la hoja que corresponde a la fila 0 de la tabla.

    Criterio: las celdas numéricas de la tabla deben coincidir exactamente con las celdas
    con fórmula de la hoja. Debe existir al menos una ubicación perfecta."""
    ws = book.wb[hoja]
    formula = {}
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=2, max_col=1 + ncol):
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("="):
                formula[(c.row, c.column - 1)] = True
    mejores = []
    for r0 in range(1, ws.max_row - nrow + 2):
        malas = 0
        for r in range(nrow):
            for c in range(ncol):
                if _numerica(grid_txt[r][c]) != ((r0 + r, c + 1) in formula):
                    malas += 1
        mejores.append((malas, -r0, r0))
    mejores.sort()
    malas, _, r0 = mejores[0]
    if malas != 0:
        raise ValueError(f"{hoja}: la tabla no calza con la hoja ({malas} celdas distintas en el mejor caso, fila {r0})")
    perfectas = [x for x in mejores if x[0] == 0]
    return r0, len(perfectas)


def llenar_tabla(book: Book, hoja: str, tbl, estilos: Estilos, rep: dict):
    grid = _tabla_grid(tbl)
    nrow, ncol = len(grid), len(grid[0])
    grid_txt = [[_txt(tc).strip() for tc in fila] for fila in grid]
    r0, n_perf = _localizar_region(book, hoja, grid_txt, ncol, nrow)
    ws = book.wb[hoja]
    cambiadas = 0
    for r in range(nrow):
        for c in range(ncol):
            cel = ws.cell(r0 + r, 2 + c)
            if not (isinstance(cel.value, str) and cel.value.startswith("=")):
                continue
            valor = book.get(hoja, cel.coordinate)
            texto, neg = format_value(valor, cel.number_format)
            tc = grid[r][c]
            ps = tc.findall("a:txBody/a:p", NS)
            runs = ps[0].findall("a:r", NS)
            if not runs:
                raise ValueError(f"{hoja} {cel.coordinate}: celda sin run en la plantilla")
            for extra in ps[1:]:
                tc.find("a:txBody", NS).remove(extra)
            for extra in runs[1:]:
                ps[0].remove(extra)
            rp = runs[0].find("a:rPr", NS)
            neg_pl = grid_txt[r][c].startswith("(")
            b, color = estilos.estilo(ncol, c, _fill_hex(tc), neg_pl, neg, rp.get("b"), _color(rp))
            runs[0].find("a:t", NS).text = texto
            rp.set("b", b)
            _set_color(rp, color)
            cambiadas += 1
    rep.setdefault("celdas", 0)
    rep["celdas"] += cambiadas
    return r0, n_perf


# ---------------------------------------------------------------- tarjetas KPI
def _x_de(sp) -> int:
    off = sp.find("p:spPr/a:xfrm/a:off", NS)
    return int(off.get("x")) if off is not None else -1


def _y_de(sp) -> int:
    off = sp.find("p:spPr/a:xfrm/a:off", NS)
    return int(off.get("y")) if off is not None else -1


def llenar_kpis(book: Book, hoja: str, root, rep: dict) -> int:
    ws = book.wb[hoja]
    sps = [sp for sp in root.iterfind(".//p:sp", NS) if sp.find("p:txBody", NS) is not None]
    vals = sorted([sp for sp in sps if RE_KPI_VAL.match(_txt(sp).strip())], key=_x_de)
    if not vals:
        return 0
    fila_kpi = None
    for row in ws.iter_rows(min_row=1, max_row=12):
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("=") and c.number_format == FMT_KPI:
                fila_kpi = c.row
                break
        if fila_kpi:
            break
    if fila_kpi is None:
        raise ValueError(f"{hoja}: la lámina tiene tarjetas KPI y la hoja no")
    n = 0
    x2idx = {}
    for i, sp in enumerate(vals):
        col = 2 + 3 * i
        cel = ws.cell(fila_kpi, col)
        texto, _ = format_value(book.get(hoja, cel.coordinate), cel.number_format)
        runs = sp.findall(".//a:r", NS)
        runs[0].find("a:t", NS).text = texto
        for extra in runs[1:]:
            extra.getparent().remove(extra)
        x2idx[_x_de(sp)] = i
        n += 1
    for sp in sps:
        t = _txt(sp)
        if " · var. " not in t or _x_de(sp) not in x2idx:
            continue
        i = x2idx[_x_de(sp)]
        col = 2 + 3 * i
        # La plantilla trae 4 runs si hay negativos (valor en rojo) y 1 solo run si todo es positivo:
        # se reconstruye siempre [prefijo][var $][ · ][var %] clonando el estilo del primer run.
        runs = sp.findall(".//a:r", NS)
        base = runs[0]
        parrafo = base.getparent()
        pos = list(parrafo).index(base)
        prefijo = t.split(" · var. ")[0] + " · var. "
        piezas = [(prefijo, GRIS_TXT), None, (" · ", GRIS_TXT), None]
        for k, off in ((1, 1), (3, 2)):
            cel = ws.cell(fila_kpi + 1, col + off)
            texto, neg = format_value(book.get(hoja, cel.coordinate), cel.number_format)
            piezas[k] = (texto, ROJO_VAR if neg else GRIS_TXT)
        for r in runs:
            parrafo.remove(r)
        for k, (texto, color) in enumerate(piezas):
            nuevo = copy.deepcopy(base)
            nuevo.find("a:t", NS).text = texto
            rp = nuevo.find("a:rPr", NS)
            rp.set("b", "0")
            _set_color(rp, color)
            parrafo.insert(pos + k, nuevo)
        n += 1
    rep["kpis"] = rep.get("kpis", 0) + n
    return n


# ---------------------------------------------------------------- orquestación
ALIAS_LAMINA = {4: 3}      # la lámina 4 repite la 3 (no existe hoja L04); se elimina al guardar (LAMINAS_QUITAR)
LAMINAS_QUITAR = [4]


def llenar_deck(deck: Deck, book: Book, sem_plantilla: int, sem: int, fechas: str, fechas_acum: str,
                estilos: Estilos) -> dict:
    hojas = mapa_hojas(book)
    sust = Sustituciones(sem_plantilla, sem, fechas, fechas_acum)
    rep: dict = {"laminas": len(deck), "tablas": 0, "alineacion_ambigua": []}
    for idx in range(1, len(deck) + 1):
        root = deck.slide(idx)
        tbls = root.xpath("//a:tbl", namespaces=NS)
        if len(tbls) > 1:
            raise ValueError(f"lámina {idx}: más de una tabla")
        if tbls:
            hoja = hojas.get(ALIAS_LAMINA.get(idx, idx))
            if hoja is None:
                raise ValueError(f"lámina {idx} tiene tabla y no hay hoja L{idx:02d} en el Excel")
            r0, n_perf = llenar_tabla(book, hoja, tbls[0], estilos, rep)
            if n_perf > 1:
                rep["alineacion_ambigua"].append((idx, hoja, n_perf))
            llenar_kpis(book, hoja, root, rep)
            rep["tablas"] += 1
        sust.aplicar(root)
    rep["textos_sustituidos"] = sust.cambios
    return rep
