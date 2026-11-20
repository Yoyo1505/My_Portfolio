# -*- coding: utf-8 -*-
"""
dash_app/components/top_filter_bar.py
Filtros globales: dock flotante de 4 íconos sin palabras (Canal · Monto · Cuándo · Qué), fijo bajo el título.
Cada ícono abre solo su panel (assets/dock.js alterna `.abierta`). Mismos ids/valores/callbacks de siempre.
Estilos en assets/zz_filtros.css (bloque final); íconos en assets/iconos.css.
"""
from dash import html, dcc
import dash_bootstrap_components as dbc
from dash_app.services.data_service import DataService

ds = DataService()

NOM_MES = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
           7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"}


def _burbuja(icono, texto):
    return html.Span(className="tfb-burbuja", title=texto, **{"aria-label": texto},
                     children=html.Span(className=f"ico ico-{icono}"))


def _seg(id_, clase, opciones, valor):
    """Control segmentado con texto por opción."""
    return html.Div(className=f"segmented-control {clase}", children=dbc.RadioItems(
        id=id_, value=valor, inline=True, options=[{"label": t, "value": v} for t, v in opciones]))


def _dd(id_, etiqueta, ancho, opciones, placeholder="Todos"):
    """Menú múltiple con su etiqueta dentro del control; `ancho` = espacio reservado a la etiqueta (px)."""
    return html.Div(className="tfb-dd", style={"--lw": f"{ancho}px"}, children=[
        html.Span(etiqueta, className="tfb-dd-lbl"),
        dcc.Dropdown(id=id_, options=opciones, multi=True, placeholder=placeholder),
    ])


def _num(id_, etiqueta, valor):
    """Semana escrita a mano (Enter o salir del campo); se sincroniza con la barra deslizable."""
    return html.Label(className="tfb-num-campo", children=[
        html.Span(etiqueta),
        dcc.Input(id=id_, type="number", min=1, max=ds.smp, step=1, value=valor, debounce=True, className="tfb-num-input"),
    ])


def create_top_filter_bar():
    canal = html.Div(className="tfb-celda", children=[
        _burbuja("shop", "Canal"),
        html.Div(className="tfb-panel", children=html.Div(className="tfb-pila", children=[
            _seg("sidebar-canal", "segmented-control--canal", [("Físico", "Fisico"), ("Digital", "Digital"), ("Ambos", "Ambos")], "Fisico"),
        ])),
    ])

    monto = html.Div(className="tfb-celda", children=[
        _burbuja("coin", "Monto"),
        html.Div(className="tfb-panel", children=html.Div(className="tfb-pila", children=[
            _seg("sidebar-tipo-monto", "segmented-control--monto", [("Monto Constante", "Monto Constante"), ("Monto Local", "Monto Local")], "Monto Constante"),
        ])),
    ])

    cuando = html.Div(className="tfb-celda tfb-celda-cuando", children=[
        _burbuja("clock", "Cuándo"),
        html.Span(id="tfb-periodo-chip", className="tfb-chip-periodo", children=f"Sem 1 – {ds.smr}"),
        html.Div(className="tfb-panel", children=html.Div(className="tfb-pila", children=[
            _seg("sidebar-modo-filtro", "segmented-control--filter",
                 [("Rango", "Rango de semanas"), ("Mes", "Mes"), ("Trimestre", "Trimestre"), ("Semana", "Semana individual")],
                 "Rango de semanas"),
            html.Div(className="tfb-fila", children=[
                html.Div(children=[
                    html.Div(id="ctrl-rango-semanas", className="premium-slider-wrap", children=[
                        dcc.RangeSlider(
                            id="sidebar-slider-semanas", min=1, max=ds.smp, step=1, value=[1, ds.smr],
                            marks={1: "1", 13: "Q1", 26: "Q2", ds.smr: str(ds.smr), 53: "53"},
                            tooltip={"placement": "bottom", "always_visible": False}),
                        html.Div(className="tfb-num", children=[
                            _num("sidebar-sem-desde", "Desde", 1),
                            _num("sidebar-sem-hasta", "Hasta", ds.smr)])]),
                    html.Div(id="ctrl-mes", style={"display": "none"}, children=dcc.Dropdown(
                        id="sidebar-mes-multi", multi=True, searchable=True, placeholder="Elige mes(es)",
                        options=[{"label": NOM_MES[m], "value": m} for m in range(1, 13)])),
                    html.Div(id="ctrl-trimestre", style={"display": "none"}, children=dcc.Dropdown(
                        id="sidebar-tri-multi", multi=True, searchable=True, placeholder="Elige trimestre(s)",
                        options=[{"label": q, "value": q} for q in ["Q1", "Q2", "Q3", "Q4"]])),
                    html.Div(id="ctrl-sem-individual", className="premium-slider-wrap", style={"display": "none"}, children=[
                        dcc.Slider(id="sidebar-sem-individual", min=1, max=ds.smp, step=1, value=ds.smr, marks=None,
                                   tooltip={"placement": "bottom", "always_visible": True}),
                        html.Div(className="tfb-num", children=[_num("sidebar-sem-num", "Semana", ds.smr)])]),
                ]),
                html.Span(id="sidebar-periodo-badge", children=f"Sem 1 – {ds.smr}", className="tfb-badge"),
            ]),
        ])),
    ])

    que = html.Div(className="tfb-celda", children=[
        _burbuja("funnel", "Qué"),
        html.Div(className="tfb-panel", children=html.Div(className="tfb-quad", children=[
            _dd("sidebar-fg-resp", "Responsable", 104, [{"label": r, "value": r} for r in ds.responsables]),
            _dd("sidebar-fg-gpo", "Grupo", 58, [{"label": g, "value": g} for g in ds.grupos]),
            _dd("sidebar-fg-subtipo", "Subtipo", 66, [{"label": s, "value": s} for s in ds.subtipos]),
            _dd("sidebar-fg-cta", "Cuenta", 62, [{"label": c, "value": c} for c in ds.cuentas], "Todas"),
        ])),
    ])

    return html.Div(className="top-filter-bar", children=html.Div(className="tfb-grid", children=[canal, monto, cuando, que]))