# -*- coding: utf-8 -*-
"""
dash_app/components/header.py
Encabezado Institucional Ejecutivo: logo + título a la izquierda; a la derecha dos fichas (monto y semana de datos),
cambio de tema y cierre de sesión. Estilos `.hd-*` en assets/zzz_tema.css. El texto de la ficha de monto
(`header-tipo-monto-badge`) lo escribe un callback de app.py.
"""
from dash import html
import dash_bootstrap_components as dbc


def _ficha(icono, etiqueta, valor, id_=None):
    """Ficha informativa: ícono en burbuja, etiqueta pequeña arriba y valor debajo."""
    val = html.Span(valor, className="hd-chip-val")
    if id_:
        val.id = id_
    return html.Div(className="hd-chip", children=[
        html.Span(className="hd-chip-ico", children=html.Span(className=f"ico ico-{icono}")),
        html.Div(children=[html.Span(etiqueta, className="hd-chip-lbl"), val]),
    ])


def create_header(smr=36, user_name=None):
    logout_btn = None
    if user_name:
        logout_btn = dbc.Button(
            f"Cerrar Sesión ({user_name})",
            id="btn-logout",
            color="light",
            size="sm",
            className="ms-auto border text-dark fw-bold shadow-sm px-3 py-1",
            style={"fontSize": "0.78rem", "borderRadius": "8px"}
        )

    return html.Div(
        className="exec-header d-flex align-items-center justify-content-between",
        children=[
            html.Div(
                className="d-flex align-items-center gap-3",
                children=[
                    html.Div(
                        # El PNG es un lockup vertical (isotipo + "grupo aurora" apilado): se recorta a solo el
                        # isotipo. El contenedor debe ser tan ancho como la imagen YA ESCALADA (alto x aspect-ratio
                        # real ~768x576) o se corta también a los lados.
                        style={"height": "40px", "width": "68px", "overflow": "hidden",
                               "display": "flex", "justifyContent": "center"},
                        children=html.Img(
                            src="/assets/aurora_logo.png",
                            style={"height": "54px", "width": "auto", "display": "block", "flexShrink": "0"},
                            alt="Grupo Aurora",
                        ),
                    ),
                    html.Div(className="hd-sep"),
                    html.Div(
                        children=[
                            html.Div("GRUPO AURORA · DIRECCIÓN DE FINANZAS", className="hd-eyebrow"),
                            html.H3("Gasto Canales", className="exec-header-title mb-0"),
                        ]
                    )
                ]
            ),
            html.Div(
                className="d-flex align-items-center gap-3",
                children=[
                    _ficha("coin", "Monto", "Constante · MDP", id_="header-tipo-monto-badge"),
                    _ficha("clock", "Datos a la semana", f"Semana {smr}"),
                    html.Button(
                        id="btn-theme",
                        n_clicks=0,
                        className="theme-toggle",
                        title="Cambiar entre modo claro y oscuro",
                        children=[
                            html.Span([html.Span(className="ico ico-moon"), " Oscuro"], className="th-moon"),
                            html.Span([html.Span(className="ico ico-sun"), " Claro"], className="th-sun"),
                        ],
                    ),
                    logout_btn if logout_btn else html.Div()
                ]
            )
        ]
    )
