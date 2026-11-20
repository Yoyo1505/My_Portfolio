# -*- coding: utf-8 -*-
"""
fmt.py — Formato de cifras idéntico al de la presentación.

Los formatos salen de `number_format` de las celdas del Excel (solo existen 4):
    '#,##0.0;[Red]\\(#,##0.0\\);0.0'            -> 1 decimal, miles, negativo entre paréntesis
    '0%;[Red]\\(0%\\);0%'                        -> porcentaje sin decimales
    '0.0%;[Red]\\(0.0%\\);0.0%'                  -> porcentaje con 1 decimal
    '\\$#,##0.0" M";[Red]"($"#,##0.0" M)"'       -> tarjeta KPI "$573.8 M" / "($12.3 M)"

Regla propia de la presentación (distinta de Excel): se redondea PRIMERO y el signo
se decide con el valor ya redondeado, por eso -0.006 se ve "0.0" y no "(0.0)".
Redondeo "mitad hacia afuera" como Excel (Decimal, no el round() de Python).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .xl_eval import ExcelErr

FMT_NUM1 = r"#,##0.0;[Red]\(#,##0.0\);0.0"
FMT_PCT0 = r"0%;[Red]\(0%\);0%"
FMT_PCT1 = r"0.0%;[Red]\(0.0%\);0.0%"
FMT_KPI = r'\$#,##0.0" M";[Red]"($"#,##0.0" M)"'

_KINDS = {FMT_NUM1: ("num", 1), FMT_PCT0: ("pct", 0), FMT_PCT1: ("pct", 1), FMT_KPI: ("kpi", 1)}


def _round_dec(x: float, decimals: int) -> Decimal:
    d = Decimal(repr(float(x)))
    return d.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def format_value(value, number_format: str) -> tuple[str, bool]:
    """Devuelve (texto, es_negativo). Texto crudo ('n.a.') se devuelve tal cual."""
    if isinstance(value, ExcelErr):
        raise ValueError(f"la celda devolvió un error de Excel: {value.code}")
    if isinstance(value, str):
        return value, False
    if value is None:
        value = 0.0
    kind = _KINDS.get(number_format)
    if kind is None:
        raise NotImplementedError(f"number_format no soportado: {number_format!r}")
    k, dec = kind
    v = Decimal(repr(float(value)))
    if k == "pct":
        v = v * 100
    r = _round_dec(float(v), dec) if k != "pct" else v.quantize(Decimal(1).scaleb(-dec), rounding=ROUND_HALF_UP)
    neg = r < 0
    body = f"{abs(r):,.{dec}f}"
    if k == "num":
        return (f"({body})" if neg else body), neg
    if k == "pct":
        return (f"({body}%)" if neg else f"{body}%"), neg
    return (f"(${body} M)" if neg else f"${body} M"), neg
