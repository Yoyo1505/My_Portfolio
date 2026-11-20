# -*- coding: utf-8 -*-
"""
dash_app/pages/detalle_pdv.py
Pestaña Detalle PDV conectada a aggs/pdc.parquet y aggs/division.parquet reales:
- Jerarquía Territorial: División → Territorio → PDC
- Concentración de Gasto Real 2026 por División (Donut)
- Árbol jerárquico drill-down instantáneo
- Histograma de Gauss "Alineación vs Plan" con bandas σ (Plan, ±1σ, ±2σ)
- Tablas Top y Bottom PDCs por desviación vs Plan
"""
import dash
from dash import html, dcc, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import numpy as np
import pandas as pd
from pathlib import Path
from dash_app.services.data_service import DataService, MC
from dash_app.services.duckdb_service import DuckDBService
from dash_app.components.tree_view import create_tree_view
from dash_app.components import charts
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla, tabla_descarga
from dash_app.services.filtros_ui import lista, campo, grid

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
AGGS_DIR = BASE_DIR / "aggs"

ds = DataService()
ddb = DuckDBService()

def _opciones_pdc(columna):
    # 2026-10-21: desde el rollup más chico de dashboard.duckdb que trae la columna (antes pdc.parquet, 887 MB).
    vals = ddb.valores_distintos(columna)
    return sorted(x for x in vals if x and str(x) not in ("(Sin dato)", "Sin dato", "nan", "None"))

_AGR_REALES_OPTS = _opciones_pdc("cat_Agrupador_Reales")
_PREFIJO_OPTS = _opciones_pdc("cat_Prefijo")
_DIVISION_OPTS = _opciones_pdc("cat_Direccion_Division")

def layout():
    return html.Div(
        children=[
            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                style={"marginBottom": "16px"},
                children=[
                    html.H5("Análisis por Puntos de Venta", style={"fontWeight": "700", "color": "#1e2430"}),
                    grid(
                        campo("Vista", html.Div(className="segmented-control", children=dbc.RadioItems(
                            id="pdv-jerarquia-radio",
                            options=[{"label": "General", "value": "General"},
                                     {"label": "Por Cuenta", "value": "Por Cuenta"}],
                            value="General", inline=True))),
                        campo("Buscar PDV / ECO / Tienda", dcc.Dropdown(
                            id="input-buscar-pdv",
                            options=[{"label": f"{k} · {v}" if v else f"PDV {k}", "value": str(k)}
                                     for k, v in ds.pdv_nombres.items()],
                            multi=True, placeholder="Buscar PDV / ECO / Tienda")),
                        campo("Agrupador de Reales", dcc.Dropdown(
                            id="pdv-filtro-agrupador-reales",
                            options=[{"label": a, "value": a} for a in _AGR_REALES_OPTS],
                            multi=True, placeholder="Agrupador de Reales")),
                        campo("Prefijo", dcc.Dropdown(
                            id="pdv-filtro-prefijo",
                            options=[{"label": p, "value": p} for p in _PREFIJO_OPTS],
                            multi=True, placeholder="Prefijo (todos)")),
                        campo("División", dcc.Dropdown(
                            id="pdv-filtro-division",
                            options=[{"label": d, "value": d} for d in _DIVISION_OPTS],
                            multi=True, placeholder="División (todas)"),
                            id="pdv-col-division", style={"display": "none"}),
                    )
                ]
            ),
            # Controles de expansión del árbol (Expandir todo solo si hay pocos
            # PDV/CECO seleccionados en la búsqueda, para no trabar el navegador).
            dcc.Store(id="pdv-tree-mode", data="normal"),
            html.Div(
                id="pdv-tree-controles",
                style={"marginBottom": "10px", "display": "none"},
                children=[
                    dbc.Button("Expandir todo", id="pdv-btn-expandir-todo", color="secondary", outline=True, size="sm", className="me-2"),
                    dbc.Button("Comprimir todo", id="pdv-btn-comprimir-todo", color="secondary", outline=True, size="sm")
                ]
            ),
            # Árbol Jerárquico Territorial Completo
            dcc.Loading(
                type="circle",
                color="#0071e3",
                children=html.Div(
                    id="detalle-pdv-tree-container",
                    style={"marginBottom": "20px"}
                )
            ),
            dcc.Download(id="pdv-download"),
            html.Div(download_buttons("pdv-btn-download"), style={"marginBottom": "20px"}),

            # Sección de Histograma de Gauss y Top/Bottom PDCs
            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                children=[
                    html.H5("Alineación vs Plan 2026 (ECOs PDV)", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "16px"}),
                    dcc.Graph(id="pdv-grafica-histograma"),
                    html.Hr(style={"margin": "20px 0"}),
                    html.H6("Top y Bottom ECOs por Desviación vs Plan (MDP)", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "12px"}),
                    dbc.Row(
                        children=[
                            dbc.Col([
                                html.Div("Top Sobre Plan (Mayor Sobregasto)", style={"fontWeight": "700", "color": "#a93226", "fontSize": "0.88rem", "marginBottom": "8px"}),
                                html.Div(id="pdv-tabla-top")
                            ], width=6),
                            dbc.Col([
                                html.Div("Bottom Sobre Plan (Mayor Ahorro)", style={"fontWeight": "700", "color": "#1e7d43", "fontSize": "0.88rem", "marginBottom": "8px"}),
                                html.Div(id="pdv-tabla-bottom")
                            ], width=6)
                        ]
                    )
                ]
            )
        ]
    )

def _filtros_locales(filters, q_pdv, agr_reales, prefijo, jerarquia, division):
    """Filtros de la consulta DuckDB del árbol (globales + buscador/filtros locales de la
    pestaña). Compartido por el árbol y la descarga para que el Excel traiga exactamente
    lo que el usuario está viendo."""
    local_filt = {}
    if filters:
        if filters.get("fg_resp"): local_filt["cat_Responsable_Grupo"] = filters["fg_resp"]
        if filters.get("fg_gpo"): local_filt["cat_Grupo_de_Cuentas"] = filters["fg_gpo"]
        if filters.get("fg_cta"): local_filt["cat_Cuentas"] = filters["fg_cta"]
        if filters.get("fg_subtipo"): local_filt["cat_Subtipo"] = filters["fg_subtipo"]
        local_filt["canal"] = filters.get("canal")

    text_search = None
    if q_pdv:
        vals = q_pdv if isinstance(q_pdv, list) else [q_pdv]
        ecos = [int(x) for x in vals if str(x).isdigit()]
        names = [str(x).strip() for x in vals if not str(x).isdigit()]
        if ecos:
            local_filt["ECO_PDV"] = ecos
        if names:
            text_search = ("cat_PDC", names[0])

    if agr_reales:
        local_filt["cat_Agrupador_Reales"] = agr_reales
    if lista(prefijo):
        local_filt["cat_Prefijo"] = lista(prefijo)
    if jerarquia == "Por Cuenta" and lista(division):
        local_filt["cat_Direccion_Division"] = lista(division)
    return local_filt, text_search


_COLS_ARBOL_PDV = {
    "General": ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ECO_PDV"],
    "Por Cuenta": ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "ECO_PDV"],
}


@callback(
    Output("pdv-download", "data"),
    *download_inputs("pdv-btn-download"),
    State("global-filter-store", "data"),
    State("input-buscar-pdv", "value"),
    State("pdv-jerarquia-radio", "value"),
    State("pdv-filtro-agrupador-reales", "value"),
    State("pdv-filtro-prefijo", "value"),
    State("pdv-filtro-division", "value"),
    prevent_initial_call=True,
)
def download_detalle_pdv(_n_num, _n_conta, filters, q_pdv, jerarquia, agr_reales, prefijo, division):
    """Árbol completo a Excel (paridad con "Preparar descarga del árbol completo" del Streamlit)."""
    contable = modo_descarga()
    if contable is None:
        return no_update
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    si = filters.get("si", 1) if filters else 1
    sf = filters.get("sf", ds.smr) if filters else ds.smr
    jerarquia = jerarquia or "General"
    cols = _COLS_ARBOL_PDV[jerarquia]
    local_filt, text_search = _filtros_locales(filters, q_pdv, agr_reales, prefijo, jerarquia, division)
    raw = ddb.query_ceco_summary(cols, si=si, sf=sf, filters=local_filt, tipo_monto=tm,
                                 exclude_zero_cols=["ECO_PDV"], text_search=text_search)
    raw = raw.dropna(subset=[cols[0]])
    if raw.empty:
        return no_update
    nombre = "pdc_general" if jerarquia == "General" else "pdc_por_cuenta"
    return enviar_tabla(tabla_descarga(raw, cols), f"{nombre}_sem{si}-{sf}", contable)


@callback(
    Output("pdv-search-filters", "data"),
    Input("input-buscar-pdv", "value"),
    Input("pdv-filtro-agrupador-reales", "value"),
    Input("pdv-filtro-prefijo", "value"),
    Input("pdv-filtro-division", "value")
)
def sync_pdv_search_to_tree(q_pdv, agr_reales, prefijo, division):
    """Propaga el buscador/filtros locales de PDV al store global que lee
    toggle_tree_branch, así también aplican a los niveles del árbol cargados
    por lazy-loading (antes solo filtraban la raíz, no las ramas expandidas)."""
    data = {"tree_key": "pdv-tree", "eco_pdv_exclude_zero": True}
    tiene_algo = False

    if q_pdv:
        tiene_algo = True
        if isinstance(q_pdv, list) and len(q_pdv) > 0:
            ecos = [int(x) for x in q_pdv if str(x).isdigit()]
            names = [str(x).strip().lower() for x in q_pdv if not str(x).isdigit()]
            if ecos:
                data["eco_pdv_in"] = ecos
            if names:
                data["cat_pdc_query"] = names[0]
        elif isinstance(q_pdv, str) and q_pdv.strip():
            q_clean = q_pdv.strip().lower()
            if q_clean.isdigit():
                data["eco_pdv_in"] = [int(q_clean)]
            else:
                data["cat_pdc_query"] = q_clean

    if agr_reales:
        data["cat_Agrupador_Reales"] = agr_reales
        tiene_algo = True
    if lista(prefijo):
        data["cat_Prefijo"] = lista(prefijo)
        tiene_algo = True
    if lista(division):
        data["cat_Direccion_Division"] = lista(division)
        tiene_algo = True

    # Siempre se devuelve `data`: el árbol raíz excluye ECO_PDV=0 (exclude_zero_cols) aunque no haya búsqueda, y los
    # hijos deben excluirlo igual; con {} los hijos traían ECO_PDV=0 y no sumaban al padre (2026-11-05).
    return data

@callback(
    Output("pdv-col-division", "style"),
    Input("pdv-jerarquia-radio", "value")
)
def toggle_col_division(jerarquia):
    return {} if jerarquia == "Por Cuenta" else {"display": "none"}

# Umbral: "Expandir todo" solo aparece si hay pocos PDV seleccionados en la
# búsqueda, para no trabar el navegador armando un árbol gigante de una vez.
UMBRAL_EXPANDIR_TODO = 8

@callback(
    Output("pdv-tree-controles", "style"),
    Output("pdv-btn-expandir-todo", "style"),
    Input("input-buscar-pdv", "value")
)
def toggle_controles_pdv(q_pdv):
    if not q_pdv:
        return {"display": "none"}, {"display": "none"}
    n = len(q_pdv) if isinstance(q_pdv, list) else 1
    btn_expandir_style = {} if n <= UMBRAL_EXPANDIR_TODO else {"display": "none"}
    return {"marginBottom": "10px"}, btn_expandir_style

@callback(
    Output("pdv-tree-mode", "data"),
    Input("pdv-btn-expandir-todo", "n_clicks"),
    Input("pdv-btn-comprimir-todo", "n_clicks"),
    Input("input-buscar-pdv", "value"),
    prevent_initial_call=True
)
def set_pdv_tree_mode(n_exp, n_comp, q_pdv):
    ctx_id = dash.callback_context.triggered[0]["prop_id"].split(".")[0] if dash.callback_context.triggered else ""
    if ctx_id == "pdv-btn-expandir-todo":
        return "expandido"
    if ctx_id == "pdv-btn-comprimir-todo":
        return "normal"
    return "normal"

@callback(
    Output("detalle-pdv-tree-container", "children"),
    Output("pdv-grafica-histograma", "figure"),
    Output("pdv-tabla-top", "children"),
    Output("pdv-tabla-bottom", "children"),
    Input("global-filter-store", "data"),
    Input("input-buscar-pdv", "value"),
    Input("pdv-jerarquia-radio", "value"),
    Input("pdv-tree-mode", "data"),
    Input("pdv-filtro-agrupador-reales", "value"),
    Input("pdv-filtro-prefijo", "value"),
    Input("pdv-filtro-division", "value")
)
def update_detalle_pdv(filters, q_pdv, jerarquia, tree_mode, agr_reales, prefijo, division):
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    si = filters.get("si", 1) if filters else 1
    sf = filters.get("sf", ds.smr) if filters else ds.smr
    jerarquia = jerarquia or "General"

    # Jerarquía: General (6 niveles) vs Por Cuenta (4 niveles) — igual al original,
    # ambas terminan en ECO_PDV (no cat_PDC: un mismo Eco PDV puede agrupar más de
    # un nombre de PDC, agrupar por cat_PDC partiría esos casos).
    cols_arbol = _COLS_ARBOL_PDV[jerarquia]
    primer_col = cols_arbol[0]

    # 1. Árbol raíz (primer nivel de la jerarquía elegida), excluyendo ECO_PDV=0
    # (sin PDV oficial asignado) — regla siempre activa, igual al original.
    # Vía DuckDB (motor C++, con cursor por query — seguro para 50+ usuarios
    # concurrentes) en vez de escanear pdc.parquet con Polars.
    local_filt, text_search = _filtros_locales(filters, q_pdv, agr_reales, prefijo, jerarquia, division)

    # El árbol solo se auto-expande cuando el usuario pulsa "Expandir todo" (no
    # automáticamente al buscar) — la búsqueda filtra las ramas en todos los
    # niveles (ver pdv-search-filters), pero el usuario decide qué abre y qué no.
    modo_expandido = bool(q_pdv) and tree_mode == "expandido"

    if modo_expandido:
        raw = ddb.query_ceco_summary(cols_arbol, si=si, sf=sf, filters=local_filt, tipo_monto=tm,
                                      exclude_zero_cols=["ECO_PDV"], text_search=text_search)
        raw = raw.dropna(subset=[cols_arbol[0]])

        if raw.empty:
            empty_fig = go.Figure()
            alert = dbc.Alert("Sin resultados para los PDVs seleccionados.", color="warning")
            return alert, empty_fig, html.Div(), html.Div()

        from dash_app.components.tree_view import create_tree_view_expanded
        totals = {"Real_2025": raw["Real_2025"].sum(), "Plan_2026": raw["Plan_2026"].sum(), "Real_2026": raw["Real_2026"].sum()}
        tree = create_tree_view_expanded("pdv-tree", raw, cols_arbol, total_dict=totals)
    else:
        grouped = ddb.query_ceco_summary([primer_col], si=si, sf=sf, filters=local_filt, tipo_monto=tm,
                                          exclude_zero_cols=["ECO_PDV"], text_search=text_search)

        if grouped.empty:
            empty_fig = go.Figure()
            alert = dbc.Alert("Sin resultados en el rango.", color="warning")
            return alert, empty_fig, html.Div(), html.Div()

        df_div = grouped.rename(columns={primer_col: "Concepto"})
        df_div = df_div[df_div["Concepto"].notna() & (df_div["Concepto"] != "nan")].copy()

        tot_r25 = df_div["Real_2025"].sum()
        tot_p26 = df_div["Plan_2026"].sum()
        tot_r26 = df_div["Real_2026"].sum()

        df_div["Pct_r25"] = (df_div["Real_2025"] / tot_r25 * 100) if tot_r25 else 0.0
        df_div["Pct_p26"] = (df_div["Plan_2026"] / tot_p26 * 100) if tot_p26 else 0.0
        df_div["Pct_r26"] = (df_div["Real_2026"] / tot_r26 * 100) if tot_r26 else 0.0
        df_div["Vs_Plan_Abs"] = df_div["Real_2026"] - df_div["Plan_2026"]
        df_div["Vs_Plan_Pct"] = (df_div["Vs_Plan_Abs"] / df_div["Plan_2026"].abs() * 100).fillna(0.0)
        df_div["Vs_AA_Abs"] = df_div["Real_2026"] - df_div["Real_2025"]
        df_div["Vs_AA_Pct"] = (df_div["Vs_AA_Abs"] / df_div["Real_2025"].abs() * 100).fillna(0.0)

        if primer_col == "cat_Grupo_de_Cuentas":
            df_div["_ord"] = df_div["Concepto"].map(ds.orden_gpo_key)
            df_div = df_div.sort_values("_ord").drop(columns=["_ord"]).reset_index(drop=True)
        else:
            df_div = df_div.sort_values("Real_2026", ascending=False).reset_index(drop=True)

        totals = {"Real_2025": tot_r25, "Plan_2026": tot_p26, "Real_2026": tot_r26}
        tree = create_tree_view(
            "pdv-tree",
            df_div,
            total_dict=totals,
            cols=cols_arbol
        )

    # 2. Histograma de Gauss sobre ECO PDV — misma fuente DuckDB, sin filtro de
    # búsqueda de PDV (el histograma es panorama general, no del PDV buscado).
    local_filt_hist = {}
    if filters:
        if filters.get("fg_resp"): local_filt_hist["cat_Responsable_Grupo"] = filters["fg_resp"]
        if filters.get("fg_gpo"): local_filt_hist["cat_Grupo_de_Cuentas"] = filters["fg_gpo"]
        if filters.get("fg_cta"): local_filt_hist["cat_Cuentas"] = filters["fg_cta"]
        if filters.get("fg_subtipo"): local_filt_hist["cat_Subtipo"] = filters["fg_subtipo"]
        local_filt_hist["canal"] = filters.get("canal")

    pdc_agg = ddb.query_ceco_summary(["ECO_PDV", "cat_Direccion_Division"], si=si, sf=sf,
                                      filters=local_filt_hist, tipo_monto=tm,
                                      exclude_zero_cols=["ECO_PDV"])
    pdc_agg = pdc_agg.drop(columns=["Real_2025"], errors="ignore")

    if pdc_agg.empty:
        empty_fig = go.Figure()
        return tree, empty_fig, html.Div("Sin datos."), html.Div("Sin datos.")

    pdc_agg["Vs_Plan_Abs"] = pdc_agg["Real_2026"] - pdc_agg["Plan_2026"]
    pdc_agg["Vs_Plan_MDP"] = pdc_agg["Vs_Plan_Abs"] / 1e6

    # Calcular bandas de desviación
    mdp_series = pdc_agg["Vs_Plan_MDP"]
    lim = min(15.0, max(2.0, float(np.percentile(mdp_series.abs(), 98))))
    dentro = mdp_series[mdp_series.abs() <= lim]
    sigma = float(dentro.std()) if len(dentro) > 1 else 1.0

    counts, bin_edges = np.histogram(dentro, bins=35, range=(-lim, lim))
    centers = (bin_edges[:-1] + bin_edges[1:]) / 2
    width = bin_edges[1] - bin_edges[0]

    def _color_banda(c0):
        if abs(c0) < width:
            return "#ccd1d9"
        a = abs(c0) / sigma
        return "#87fc72" if a <= 1 else ("#f5ff38" if a <= 2 else "#ff6456")

    fig_hist = go.Figure(go.Bar(
        x=centers,
        y=counts,
        width=width * 0.9,
        marker={"color": [_color_banda(c0) for c0 in centers], "line": {"width": 0}},
        hovertemplate="%{x:.2f} MDP vs Plan<br>%{y} ECOs<extra></extra>",
        showlegend=False
    ))
    fig_hist.add_vline(x=0, line_dash="dash", line_color="#000000", annotation_text="Plan", annotation_position="top")
    if 1 * sigma < lim:
        fig_hist.add_vline(x=sigma, line_dash="dot", line_color="#8a93a0")
        fig_hist.add_vline(x=-sigma, line_dash="dot", line_color="#8a93a0")
    if 2 * sigma < lim:
        fig_hist.add_vline(x=2*sigma, line_dash="dot", line_color="#8a93a0")
        fig_hist.add_vline(x=-2*sigma, line_dash="dot", line_color="#8a93a0")

    fig_hist.update_layout(
        height=320,
        margin={"l": 50, "r": 10, "t": 20, "b": 40},
        xaxis={"title": "MDP vs Plan · (← Ahorro | Sobregasto →)", "range": [-lim, lim]},
        yaxis={"title": "Número de ECOs"}
    )
    charts.estilo_basico(fig_hist)

    # 3. Top y Bottom ECOs
    pdc_valid = pdc_agg[(pdc_agg["Plan_2026"] != 0) | (pdc_agg["Real_2026"] != 0)].copy()
    top_p = pdc_valid.sort_values("Vs_Plan_Abs", ascending=False).head(8)
    bot_p = pdc_valid.sort_values("Vs_Plan_Abs", ascending=True).head(8)

    def _render_tabla_extremos(df_ext, es_sobregasto):
        color = "#a93226" if es_sobregasto else "#1e7d43"
        bg = "#fbeeec" if es_sobregasto else "#eaf6ec"
        rows = []
        for _, r in df_ext.iterrows():
            rows.append(html.Tr([
                html.Td(str(int(r["ECO_PDV"])) if pd.notna(r["ECO_PDV"]) else "", style={"fontWeight": "600", "fontSize": "0.82rem"}),
                html.Td(str(r["cat_Direccion_Division"]), style={"fontSize": "0.78rem", "color": "#6b7280"}),
                html.Td(MC(r["Real_2026"]), style={"textAlign": "right", "fontSize": "0.82rem"}),
                html.Td(MC(r["Plan_2026"]), style={"textAlign": "right", "fontSize": "0.82rem"}),
                html.Td(MC(r["Vs_Plan_Abs"]), style={"textAlign": "right", "fontWeight": "700", "color": color, "backgroundColor": bg, "fontSize": "0.82rem"}),
            ]))
        return dbc.Table([
            html.Thead(html.Tr([
                html.Th("ECO PDV"), html.Th("División"), html.Th("Real 2026"), html.Th("Plan 2026"), html.Th("vs Plan")
            ])),
            html.Tbody(rows)
        ], size="sm", bordered=True, hover=True, style={"fontSize": "0.82rem"})

    tab_top = _render_tabla_extremos(top_p, True)
    tab_bot = _render_tabla_extremos(bot_p, False)

    return tree, fig_hist, tab_top, tab_bot

