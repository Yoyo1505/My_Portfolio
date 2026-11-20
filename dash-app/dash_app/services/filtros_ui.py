# -*- coding: utf-8 -*-
"""dash_app/services/filtros_ui.py — Piezas comunes de los filtros de página (mismo diseño que la barra global)."""
from dash import html


def lista(valor):
    """[] si el filtro está vacío o es el comodín "(Todos)"/"(Todas)" (valores guardados de la versión
    de selección única); si no, la lista de valores elegidos."""
    if valor is None:
        return []
    vals = valor if isinstance(valor, (list, tuple, set)) else [valor]
    return [v for v in vals if v not in (None, "", "(Todos)", "(Todas)")]


def campo(etiqueta, control, **kw):
    """Control con su etiqueta en mayúsculas encima (unidad básica de todos los filtros). `kw` va al contenedor
    (p. ej. id=, style=, className="filtros-span-2")."""
    clase = ("tfb-campo " + kw.pop("className", "")).strip()
    return html.Div(className=clase, children=[html.Span(etiqueta, className="tfb-label"), control], **kw)


def grid(*hijos):
    """Cuadrícula de filtros: columnas iguales que se acomodan solas, todo el mismo alto."""
    return html.Div(className="filtros-grid", children=list(hijos))
