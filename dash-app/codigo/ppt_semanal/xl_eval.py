# -*- coding: utf-8 -*-
"""
xl_eval.py — Evaluador mínimo de las fórmulas del Excel de la presentación semanal.

El Excel "Gasto_semana_NN_presentacion_PSA.xlsx" calcula cada lámina con solo
cuatro funciones (SUMIFS, IFERROR, ABS, SUM) y operadores + - * / &. En vez de
depender de Excel/LibreOffice para recalcular, este módulo interpreta esas mismas
fórmulas leyéndolas de la plantilla, así la lógica del analista sigue siendo la
fuente de verdad y no se duplica en Python.

Las hojas "BD ..." se inyectan como tablas de numpy (no se evalúan celda a celda).
Cualquier función u operador no soportado lanza NotImplementedError: nunca se
devuelve un número "aproximado".
"""
from __future__ import annotations

import re
from typing import Any

import numpy as np
import openpyxl
from openpyxl.utils import column_index_from_string, get_column_letter


class ExcelErr(Exception):
    """Error de hoja de cálculo (#DIV/0!, #VALUE!...). Se propaga hasta IFERROR."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

    def __repr__(self) -> str:
        return self.code


# ---------------------------------------------------------------- tokenizer
_SHEET = r"(?:'[^']+'|[A-Za-z_À-ÿ][\wÀ-ÿ\.]*)"
_TOKEN_RE = re.compile(
    r"""
    \s*(?:
      (?P<str>"(?:[^"]|"")*")
    | (?P<func>[A-Z][A-Z0-9\.]*)(?=\()
    | (?P<ref>(?:""" + _SHEET + r"""!)?(?:\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?|\$?[A-Z]{1,3}:\$?[A-Z]{1,3}))
    | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
    | (?P<op>[-+*/&(),])
    )""",
    re.X,
)


def _tokenize(f: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    while pos < len(f):
        m = _TOKEN_RE.match(f, pos)
        if not m or m.end() == pos:
            if f[pos:].strip() == "":
                break
            raise NotImplementedError(f"fórmula no soportada cerca de {f[pos:pos+30]!r}: {f!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


_CELL_RE = re.compile(r"^\$?([A-Z]{1,3})\$?(\d+)$")
_COL_RE = re.compile(r"^\$?([A-Z]{1,3})$")


def _split_ref(ref: str, cur_sheet: str):
    sheet = cur_sheet
    if "!" in ref:
        sh, ref = ref.rsplit("!", 1)
        sheet = sh[1:-1] if sh.startswith("'") else sh
    if ":" in ref:
        a, b = ref.split(":")
    else:
        a = b = ref
    ma, mb = _CELL_RE.match(a), _CELL_RE.match(b)
    if ma and mb:
        return sheet, (int(ma.group(2)), column_index_from_string(ma.group(1))), \
            (int(mb.group(2)), column_index_from_string(mb.group(1)))
    ca, cb = _COL_RE.match(a), _COL_RE.match(b)
    if ca and cb:  # columna completa: $H:$H
        return sheet, (None, column_index_from_string(ca.group(1))), (None, column_index_from_string(cb.group(1)))
    raise NotImplementedError(f"referencia no soportada: {ref!r}")


# ---------------------------------------------------------------- tablas BD
class Table:
    """Hoja de datos plana (BD). Cols por letra; fila 1 = encabezados, datos desde la fila 2."""

    def __init__(self, cols: dict[str, np.ndarray]):
        self.cols = cols
        self.n = len(next(iter(cols.values())))
        self._lower: dict[str, np.ndarray] = {}
        self._mask: dict[tuple[str, Any], np.ndarray] = {}

    def col(self, letter: str) -> np.ndarray:
        if letter not in self.cols:
            raise NotImplementedError(f"columna {letter} no existe en la tabla BD")
        return self.cols[letter]

    def mask(self, letter: str, crit) -> np.ndarray:
        key = (letter, crit)
        m = self._mask.get(key)
        if m is None:
            c = self.col(letter)
            if isinstance(crit, str):
                if crit[:1] in "<>=" or "*" in crit or "?" in crit or "~" in crit:
                    raise NotImplementedError(f"criterio SUMIFS con operador/comodín: {crit!r}")
                low = self._lower.get(letter)
                if low is None:
                    low = np.array([str(x).lower() if isinstance(x, str) else "" for x in c], dtype=object)
                    self._lower[letter] = low
                m = low == crit.lower()
            else:
                m = np.array([isinstance(x, (int, float)) and x == crit for x in c], dtype=bool)
            self._mask[key] = m
        return m


# ---------------------------------------------------------------- libro
class Book:
    def __init__(self, path_or_wb, sheets_data: dict[str, Table] | None = None):
        wb = path_or_wb if isinstance(path_or_wb, openpyxl.Workbook) else openpyxl.load_workbook(path_or_wb, data_only=False)
        self.wb = wb
        self.tables: dict[str, Table] = dict(sheets_data or {})
        self.memo: dict[tuple[str, int, int], Any] = {}
        self._parsed: dict[str, Any] = {}

    # --- acceso a celdas
    def raw(self, sheet: str, row: int, col: int):
        return self.wb[sheet].cell(row, col).value

    def value(self, sheet: str, row: int, col: int):
        key = (sheet, row, col)
        if key in self.memo:
            v = self.memo[key]
            if isinstance(v, ExcelErr):
                raise v
            return v
        if sheet in self.tables:
            raise NotImplementedError(f"acceso a celda suelta de tabla BD {sheet}!{row},{col}")
        raw = self.raw(sheet, row, col)
        try:
            if isinstance(raw, str) and raw.startswith("="):
                v = self._eval_formula(raw[1:], sheet)
            else:
                v = raw
        except ExcelErr as e:
            self.memo[key] = e
            raise
        self.memo[key] = v
        return v

    def get(self, sheet: str, coord: str):
        """Valor de una celda para consumo externo: ExcelErr se devuelve como objeto."""
        m = _CELL_RE.match(coord)
        try:
            return self.value(sheet, int(m.group(2)), column_index_from_string(m.group(1)))
        except ExcelErr as e:
            return e

    # --- evaluación
    def _eval_formula(self, f: str, sheet: str):
        toks = self._parsed.get(f)
        if toks is None:
            toks = _tokenize(f)
            self._parsed[f] = toks
        p = _Parser(self, toks, sheet)
        v = p.parse()
        return v


def _num(v):
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float, np.floating, np.integer)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            raise ExcelErr("#VALUE!")
    raise ExcelErr("#VALUE!")


class _Range:
    def __init__(self, book: Book, sheet: str, a, b):
        self.book, self.sheet, self.a, self.b = book, sheet, a, b

    def is_table(self) -> bool:
        return self.sheet in self.book.tables

    def column_letter(self) -> str:
        return get_column_letter(self.a[1])

    def values(self) -> list:
        out = []
        r0, c0 = self.a
        r1, c1 = self.b
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                try:
                    out.append(self.book.value(self.sheet, r, c))
                except ExcelErr as e:
                    raise e
        return out


class _Parser:
    def __init__(self, book: Book, toks, sheet: str):
        self.book, self.toks, self.sheet, self.i = book, toks, sheet, 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self):
        v = self.concat()
        if self.i != len(self.toks):
            raise NotImplementedError(f"sobran tokens: {self.toks[self.i:]}")
        return self._scalar(v)

    def _scalar(self, v):
        if isinstance(v, _Range):
            if v.a == v.b and not v.is_table():
                return self.book.value(v.sheet, v.a[0], v.a[1])
            raise ExcelErr("#VALUE!")
        return v

    # operadores
    def concat(self):
        v = self.add()
        while self.peek() == ("op", "&"):
            self.take()
            r = self.add()
            a, b = self._scalar(v), self._scalar(r)
            v = _txt(a) + _txt(b)
        return v

    def add(self):
        v = self.mul()
        while self.peek()[0] == "op" and self.peek()[1] in "+-":
            op = self.take()[1]
            r = self.mul()
            a, b = _num(self._scalar(v)), _num(self._scalar(r))
            v = a + b if op == "+" else a - b
        return v

    def mul(self):
        v = self.unary()
        while self.peek()[0] == "op" and self.peek()[1] in "*/":
            op = self.take()[1]
            r = self.unary()
            a, b = _num(self._scalar(v)), _num(self._scalar(r))
            if op == "*":
                v = a * b
            else:
                if b == 0:
                    raise ExcelErr("#DIV/0!")
                v = a / b
        return v

    def unary(self):
        t = self.peek()
        if t[0] == "op" and t[1] in "+-":
            self.take()
            v = self.unary()
            x = self._scalar(v)
            if t[1] == "+":
                return x if isinstance(x, str) else _num(x)
            return -_num(x)
        return self.atom()

    def atom(self):
        kind, txt = self.take()
        if kind == "num":
            return float(txt)
        if kind == "str":
            return txt[1:-1].replace('""', '"')
        if kind == "ref":
            sh, a, b = _split_ref(txt, self.sheet)
            return _Range(self.book, sh, a, b)
        if kind == "op" and txt == "(":
            v = self.concat()
            if self.take() != ("op", ")"):
                raise NotImplementedError("paréntesis sin cerrar")
            return v
        if kind == "func":
            self.take()  # '('
            return self.call(txt)
        raise NotImplementedError(f"token inesperado {kind}:{txt}")

    # funciones
    def _args_raw(self):
        """Devuelve el rango de tokens de cada argumento (sin evaluar) para soportar IFERROR perezoso."""
        args, depth, cur = [], 0, []
        while True:
            k, t = self.take()
            if k == "op" and t == "(":
                depth += 1
            elif k == "op" and t == ")":
                if depth == 0:
                    args.append(cur)
                    return args
                depth -= 1
            elif k == "op" and t == "," and depth == 0:
                args.append(cur)
                cur = []
                continue
            cur.append((k, t))

    def _eval_sub(self, toks):
        p = _Parser(self.book, toks, self.sheet)
        v = p.concat()
        if p.i != len(toks):
            raise NotImplementedError(f"sobran tokens en argumento: {toks[p.i:]}")
        return v

    def call(self, name: str):
        args = self._args_raw()
        if name == "IFERROR":
            if len(args) != 2:
                raise NotImplementedError("IFERROR requiere 2 argumentos")
            try:
                return self._scalar(self._eval_sub(args[0]))
            except ExcelErr:
                return self._scalar(self._eval_sub(args[1]))
        vals = [self._eval_sub(a) for a in args]
        if name == "ABS":
            return abs(_num(self._scalar(vals[0])))
        if name == "SUM":
            tot = 0.0
            for v in vals:
                if isinstance(v, _Range):
                    for x in v.values():
                        if isinstance(x, (int, float)) and not isinstance(x, bool):
                            tot += x
                else:
                    tot += _num(v)
            return tot
        if name == "SUMIFS":
            return self._sumifs(vals)
        raise NotImplementedError(f"función no soportada: {name}")

    def _sumifs(self, vals):
        sr = vals[0]
        if not isinstance(sr, _Range) or not sr.is_table():
            raise NotImplementedError("SUMIFS solo soportado sobre tablas BD")
        tbl = self.book.tables[sr.sheet]
        total = tbl.col(sr.column_letter())
        mask = np.ones(tbl.n, dtype=bool)
        pairs = vals[1:]
        if len(pairs) % 2:
            raise NotImplementedError("SUMIFS con número impar de criterios")
        for i in range(0, len(pairs), 2):
            cr, crit = pairs[i], self._scalar(pairs[i + 1])
            if not isinstance(cr, _Range) or cr.sheet != sr.sheet:
                raise NotImplementedError("rango de criterio fuera de la tabla BD")
            if isinstance(crit, float) and crit == int(crit):
                crit = int(crit)
            mask &= tbl.mask(cr.column_letter(), crit)
        return float(total[mask].sum())


def _txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v)
