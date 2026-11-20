# -*- coding: utf-8 -*-
"""
dash_app/pages/presupuesto_2027.py

Página "Presupuesto 2027" — integra el tablero real de
"E:\\Desarrollos\\Ppto 2027" (Streamlit) en este dashboard Dash: mismas ramas y
misma profundidad reales del árbol (algunos niveles se saltan cuando no
aportan información — ver `ppto2027_service.construir_arbol`, que replica
1:1 la lógica de Ppto 2027/app.py), mismas 4 métricas (PLAN/FCST/
COMPARABILIDAD/CONTA) y mismos filtros de intervalo de tiempo (Mes/Semana),
centro de costos (CECO_N7/CECO_N8) e ID_CUENTAS (Cuentas).
"""
import base64
import json

import pandas as pd
from dash import html, dcc, callback, Input, Output, State, ALL, ctx, no_update
import dash_bootstrap_components as dbc

from dash_app.services import ppto2027_service as svc
from dash_app.services.data_service import DataService


def _encode_path(pairs: list) -> str:
    raw = json.dumps(pairs, ensure_ascii=False)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _fmt_monto(val) -> tuple:
    """Réplica de Ppto 2027/app.py:fmt_monto_base() — gasto positivo en rojo
    entre paréntesis, negativo/ingreso en negro, sin escala (siempre pesos)."""
    if val is None or (isinstance(val, float) and pd.isna(val)) or val == 0:
        return "0", "ppto-c-cero"
    if val < 0:
        return f"{abs(val):,.0f}", "ppto-c-ingreso"
    return f"({val:,.0f})", "ppto-c-gasto"


def _pct_var(var: float, base: float) -> str:
    if var is None or pd.isna(var) or var == 0:
        return "0.0%"
    if base is None or pd.isna(base) or base == 0:
        return "—"
    pct = (var / abs(base)) * 100
    return f"({abs(pct):,.1f}%)" if pct < 0 else f"{pct:,.1f}%"


def _fila(label: str, montos: dict, nivel: int, tiene_hijos: bool, path_key: str, abierto: bool = False) -> html.Div:
    plan_sys, fcst, comp, conta = (montos.get(m) or 0.0 for m in svc.METRICAS_V1)
    t_plan, c_plan = _fmt_monto(plan_sys)
    t_fcst, c_fcst = _fmt_monto(fcst)
    t_comp, c_comp = _fmt_monto(comp)
    t_conta, c_conta = _fmt_monto(conta)

    var_plan, var_fcst, var_comp = plan_sys - conta, fcst - conta, comp - conta
    t_vs, c_vs = _fmt_monto(var_plan)
    t_vf, c_vf = _fmt_monto(var_fcst)
    t_vc, c_vc = _fmt_monto(var_comp)

    etiqueta = str(label) if label is not None and str(label) != "nan" else "(Sin dato)"

    primera_col = [
        html.Button("−" if abierto else "+", id={"type": "ppto-toggle", "path": path_key}, className="ppto-toggle-btn",
                     n_clicks=0) if tiene_hijos else html.Span(style={"width": "18px", "display": "inline-block"}),
        html.Span(etiqueta, title=etiqueta),
    ]

    return html.Div(
        className=f"ppto-row ppto-nvl{nivel}",
        children=[
            html.Span(primera_col),
            html.Span(t_plan, className=c_plan),
            html.Span(t_fcst, className=c_fcst),
            html.Span(t_comp, className=c_comp),
            html.Span(t_conta, className=f"ppto-col-conta {c_conta}"),
            html.Span(t_vs, className=c_vs),
            html.Span(_pct_var(var_plan, conta), className="ppto-pct"),
            html.Span(t_vf, className=c_vf),
            html.Span(_pct_var(var_fcst, conta), className="ppto-pct"),
            html.Span(t_vc, className=c_vc),
            html.Span(_pct_var(var_comp, conta), className="ppto-pct"),
        ],
    )


def _cabecera() -> html.Div:
    return html.Div(
        className="ppto-row ppto-head",
        children=[
            html.Span("Grupo de ctas (abanico)"),
            html.Span("PLAN"), html.Span("FCST"), html.Span("COMPARABILIDAD"),
            html.Span("CONTA", className="ppto-col-conta"),
            html.Span("vs PLAN"), html.Span("%", className="ppto-pct"),
            html.Span("vs FCST"), html.Span("%", className="ppto-pct"),
            html.Span("vs Comp."), html.Span("%", className="ppto-pct"),
        ],
    )


def _fila_total(total: dict) -> html.Div:
    plan_sys, fcst, comp, conta = (total.get(m) or 0.0 for m in svc.METRICAS_V1)
    t_plan, _ = _fmt_monto(plan_sys)
    t_fcst, _ = _fmt_monto(fcst)
    t_comp, _ = _fmt_monto(comp)
    t_conta, _ = _fmt_monto(conta)
    var_plan, var_fcst, var_comp = plan_sys - conta, fcst - conta, comp - conta
    t_vs, _ = _fmt_monto(var_plan)
    t_vf, _ = _fmt_monto(var_fcst)
    t_vc, _ = _fmt_monto(var_comp)
    return html.Div(
        className="ppto-row ppto-tot",
        children=[
            html.Span("Total"),
            html.Span(t_plan), html.Span(t_fcst), html.Span(t_comp),
            html.Span(t_conta, className="ppto-col-conta"),
            html.Span(t_vs), html.Span(_pct_var(var_plan, conta), className="ppto-pct"),
            html.Span(t_vf), html.Span(_pct_var(var_fcst, conta), className="ppto-pct"),
            html.Span(t_vc), html.Span(_pct_var(var_comp, conta), className="ppto-pct"),
        ],
    )


def _renderizar_nodo(nodo: dict, abiertos: set, nivel: int = 0) -> list:
    path_key = _encode_path(nodo["path"])
    tiene_hijos = nodo.get("hijos") is not None
    esta_abierto = tiene_hijos and path_key in abiertos
    filas = [_fila(nodo["label"], nodo["montos"], nivel, tiene_hijos, path_key, abierto=esta_abierto)]
    if esta_abierto:
        for hijo in nodo["hijos"]:
            filas.extend(_renderizar_nodo(hijo, abiertos, nivel + 1))
    return filas


def _renderizar_arbol(tree_data: dict, abiertos_list: list) -> html.Div:
    if not tree_data or not tree_data.get("nodos"):
        return html.Div("Sin datos para los filtros seleccionados.", className="text-muted p-3")
    abiertos = set(abiertos_list or [])
    filas = [_cabecera()]
    for nodo in tree_data["nodos"]:
        filas.extend(_renderizar_nodo(nodo, abiertos, 0))
    filas.append(_fila_total(tree_data["total"]))
    return html.Div(className="ppto-tree-wrap", children=[html.Div(className="ppto-tree", children=filas)])


def _filtros_from_store(data: dict, cecos=None) -> dict:
    """Traduce el filtro global (semanas / meses / cuentas) y el CECO local a los argumentos del servicio."""
    data = data or {}
    meses = data.get("meses") if data.get("modo") == "Mes" else None
    # fg_cta ya trae Responsable+Cuenta(+Grupo) cruzados; si solo hay Grupo, se traduce a sus cuentas.
    cuentas = data.get("fg_cta") or None
    if not cuentas and data.get("fg_gpo"):
        gpos = set(data["fg_gpo"])
        cuentas = sorted(c for c, g in DataService().cta_a_gpo.items() if g in gpos) or ["__sin_cuentas__"]
    return {
        "meses": [float(m) for m in meses] if meses else None,
        "semana_rango": (int(data["si"]), int(data["sf"])) if data.get("si") and data.get("sf") else None,
        "cecos": cecos or None,
        "cuentas": cuentas,
    }


def layout():
    cecos_op = sorted(set(svc.opciones("CECO_N7")) | set(svc.opciones("CECO_N8")))

    # Periodo y Cuenta salen de los filtros globales (global-filter-store); aquí solo queda
    # Centro de Costos, que no existe en la barra general.
    return html.Div([
        dcc.Store(id="ppto-tree-store", data=None),
        dcc.Store(id="ppto-open-paths", data=[]),
        html.Div(
            className="filter-cluster mb-3",
            children=[
                dbc.Row([
                    dbc.Col([
                        html.Div("Centro de Costos", className="top-filter-label"),
                        dcc.Dropdown(id="ppto-f-ceco", options=[{"label": c, "value": c} for c in cecos_op],
                                     multi=True, placeholder="Todos"),
                    ], md=4),
                    dbc.Col(
                        html.Div("Periodo y Cuenta siguen los filtros generales de arriba.",
                                 className="top-filter-label mb-0", style={"textTransform": "none", "letterSpacing": 0}),
                        md=8, className="d-flex align-items-end"),
                ]),
            ],
        ),
        html.Div(id="ppto-kpis-container", style={"marginBottom": "20px"}),
        html.Div(id="ppto-tree-container"),
    ])


@callback(
    Output("ppto-open-paths", "data", allow_duplicate=True),
    Input("global-filter-store", "data"),
    Input("ppto-f-ceco", "value"),
    prevent_initial_call=True,
)
def _colapsar_al_filtrar(_global, _cecos):
    # Cambiar filtros colapsa el árbol (las ramas abiertas ya no aplican).
    return []


@callback(
    Output("ppto-kpis-container", "children"),
    Output("ppto-tree-store", "data"),
    Input("global-filter-store", "data"),
    Input("ppto-f-ceco", "value"),
)
def _refrescar(global_filters, cecos):
    filtros = _filtros_from_store(global_filters, cecos)
    tree_data = svc.construir_arbol(**filtros)

    kpis = tree_data["total"]
    tarjetas = dbc.Row([
        dbc.Col(html.Div(className="vt-kpi-card", children=[
            html.Div(svc.ETIQUETAS_METRICA[m], className="vt-kpi-title"),
            html.Div(f"${kpis[m]:,.0f}", className="vt-kpi-value"),
        ]), md=3) for m in svc.METRICAS_V1
    ], className="g-3")

    return tarjetas, tree_data


@callback(
    Output("ppto-tree-container", "children"),
    Input("ppto-tree-store", "data"),
    Input("ppto-open-paths", "data"),
)
def _pintar_arbol(tree_data, abiertos):
    return _renderizar_arbol(tree_data, abiertos)


@callback(
    Output("ppto-open-paths", "data"),
    Input({"type": "ppto-toggle", "path": ALL}, "n_clicks"),
    State("ppto-open-paths", "data"),
    prevent_initial_call=True,
)
def _toggle_rama(_n_clicks_list, abiertos):
    triggered = ctx.triggered_id
    if not triggered:
        return no_update
    path_key = triggered["path"]
    abiertos = list(abiertos or [])
    if path_key in abiertos:
        abiertos.remove(path_key)
    else:
        abiertos.append(path_key)
    return abiertos
