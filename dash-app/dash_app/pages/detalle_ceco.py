# -*- coding: utf-8 -*-
"""
dash_app/pages/detalle_ceco.py
Pestaña Análisis por centro de costos.
Paridad funcional 100% con Streamlit (Vistas General, Detalle y Agrupadores con lazy tree multinivel).
"""
import dash
from dash import html, dcc, callback, Input, Output, State
import dash_bootstrap_components as dbc
from dash_app.services.duckdb_service import DuckDBService
from dash_app.services.data_service import DataService
from dash_app.components.tree_view import create_tree_view
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla, tabla_descarga
from dash_app.services.filtros_ui import lista, campo, grid

ddb = DuckDBService()
ds = DataService()

def _opciones_ceco(columna):
    vals = ddb.get_unique_values(columna)
    return sorted(x for x in vals if x and str(x) not in ("(Sin dato)", "Sin dato", "No Clasificado", "nan"))

def layout():
    return html.Div(
        children=[
            dcc.Download(id="ceco-download-csv"),
            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                style={"marginBottom": "16px"},
                children=[
                    dbc.Row(
                        children=[
                            dbc.Col(
                                children=[
                                    html.H5("Análisis por centro de costos", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "0"})
                                ],
                                width=8
                            ),
                            dbc.Col(
                                html.Div(download_buttons("ceco-btn-download"), className="float-end"),
                                width=4,
                                className="d-flex align-items-center justify-content-end"
                            )
                        ]
                    ),
                    html.Hr(style={"margin": "12px 0"}),
                    grid(
                        campo("Vista", html.Div(className="segmented-control", children=dbc.RadioItems(
                            id="radio-ceco-vista",
                            options=[{"label": "General", "value": "General"},
                                     {"label": "Detalle", "value": "Detalle"},
                                     {"label": "Agrupadores", "value": "Agrupadores"}],
                            value="General", inline=True))),
                        campo("Buscar CECO", dcc.Dropdown(
                            id="input-buscar-ceco",
                            options=[{"label": f"{k} · {v}" if v else f"CECO {k}", "value": str(k)}
                                     for k, v in ds.ceco_nombres.items()],
                            multi=True, placeholder="Buscar CECO por ID o nombre")),
                        campo("División", dcc.Dropdown(
                            id="ceco-filtro-division",
                            options=[{"label": d, "value": d} for d in _opciones_ceco("cat_Direccion_Division")],
                            multi=True, placeholder="División (todas)"),
                            id="ceco-col-division"),
                        campo("Agrupador de Reales", dcc.Dropdown(
                            id="ceco-filtro-agrupador-reales",
                            options=[{"label": a, "value": a} for a in _opciones_ceco("cat_Agrupador_Reales")],
                            multi=True, placeholder="Agrupador de Reales"),
                            id="ceco-col-agrupador", style={"display": "none"}),
                    )
                ]
            ),
            dcc.Store(id="ceco-tree-mode", data="normal"),
            html.Div(
                id="ceco-tree-controles",
                style={"marginBottom": "10px", "display": "none"},
                children=[
                    dbc.Button("Expandir todo", id="ceco-btn-expandir-todo", color="secondary", outline=True, size="sm", className="me-2"),
                    dbc.Button("Comprimir todo", id="ceco-btn-comprimir-todo", color="secondary", outline=True, size="sm")
                ]
            ),
            dcc.Loading(
                type="circle",
                color="#0071e3",
                children=html.Div(id="detalle-ceco-tree-container")
            )
        ]
    )

@callback(
    Output("ceco-search-filters", "data"),
    Input("input-buscar-ceco", "value"),
    Input("ceco-filtro-division", "value"),
    Input("ceco-filtro-agrupador-reales", "value"),
    Input("radio-ceco-vista", "value")
)
def sync_ceco_search_to_tree(q_ceco, division, agr_reales, vista):
    """Propaga el buscador/filtros locales de CECO al store global que lee
    toggle_tree_branch, así también aplican a los niveles del árbol cargados
    por lazy-loading (antes solo filtraban la raíz)."""
    data = {"tree_key": "ceco-tree"}
    tiene_algo = False
    if q_ceco:
        tiene_algo = True
        if isinstance(q_ceco, list) and len(q_ceco) > 0:
            data["id_centro_costos_in"] = [str(x).strip() for x in q_ceco]
        elif isinstance(q_ceco, str) and q_ceco.strip():
            data["id_centro_costos_in"] = [q_ceco.strip()]
    if lista(division) and vista in ("General", "Detalle"):
        data["cat_Direccion_Division"] = lista(division)
        tiene_algo = True
    if agr_reales and vista == "Detalle":
        data["cat_Agrupador_Reales"] = agr_reales
        tiene_algo = True
    return data if tiene_algo else {}

@callback(
    Output("ceco-col-division", "style"),
    Output("ceco-col-agrupador", "style"),
    Input("radio-ceco-vista", "value")
)
def toggle_filtros_ceco(vista):
    div_style = {} if vista in ("General", "Detalle") else {"display": "none"}
    agr_style = {} if vista == "Detalle" else {"display": "none"}
    return div_style, agr_style

# Umbral: "Expandir todo" solo aparece si hay pocos CECO seleccionados en la
# búsqueda, para no trabar el navegador armando un árbol gigante de una vez.
UMBRAL_EXPANDIR_TODO_CECO = 8

@callback(
    Output("ceco-tree-controles", "style"),
    Output("ceco-btn-expandir-todo", "style"),
    Input("input-buscar-ceco", "value")
)
def toggle_controles_ceco(q_ceco):
    if not q_ceco:
        return {"display": "none"}, {"display": "none"}
    n = len(q_ceco) if isinstance(q_ceco, list) else 1
    btn_expandir_style = {} if n <= UMBRAL_EXPANDIR_TODO_CECO else {"display": "none"}
    return {"marginBottom": "10px"}, btn_expandir_style

@callback(
    Output("ceco-tree-mode", "data"),
    Input("ceco-btn-expandir-todo", "n_clicks"),
    Input("ceco-btn-comprimir-todo", "n_clicks"),
    Input("input-buscar-ceco", "value"),
    prevent_initial_call=True
)
def set_ceco_tree_mode(n_exp, n_comp, q_ceco):
    ctx_id = dash.callback_context.triggered[0]["prop_id"].split(".")[0] if dash.callback_context.triggered else ""
    if ctx_id == "ceco-btn-expandir-todo":
        return "expandido"
    if ctx_id == "ceco-btn-comprimir-todo":
        return "normal"
    return "normal"

@callback(
    Output("detalle-ceco-tree-container", "children"),
    Input("global-filter-store", "data"),
    Input("radio-ceco-vista", "value"),
    Input("input-buscar-ceco", "value"),
    Input("ceco-tree-mode", "data"),
    Input("ceco-filtro-division", "value"),
    Input("ceco-filtro-agrupador-reales", "value")
)
def update_detalle_ceco(filters, vista, q_ceco, tree_mode, division, agr_reales):
    si = filters.get("si", 1) if filters else 1
    sf = filters.get("sf", 36) if filters else 36
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"

    if vista == "General":
        cols = ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ID_CENTRO_COSTOS"]
    elif vista == "Detalle":
        cols = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "ID_CENTRO_COSTOS"]
    else:
        cols = ["cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3", "ID_CENTRO_COSTOS"]

    local_filt = {}
    if filters:
        if filters.get("fg_resp"): local_filt["cat_Responsable_Grupo"] = filters["fg_resp"]
        if filters.get("fg_gpo"): local_filt["cat_Grupo_de_Cuentas"] = filters["fg_gpo"]
        if filters.get("fg_cta"): local_filt["cat_Cuentas"] = filters["fg_cta"]
        if filters.get("fg_subtipo"): local_filt["cat_Subtipo"] = filters["fg_subtipo"]
        local_filt["canal"] = filters.get("canal")

    if lista(division) and vista in ("General", "Detalle"):
        local_filt["cat_Direccion_Division"] = lista(division)
    if agr_reales and vista == "Detalle":
        local_filt["cat_Agrupador_Reales"] = agr_reales

    if q_ceco:
        if isinstance(q_ceco, list):
            if len(q_ceco) > 0:
                local_filt["ID_CENTRO_COSTOS"] = [str(x).strip() for x in q_ceco]
        elif isinstance(q_ceco, str) and q_ceco.strip():
            local_filt["ID_CENTRO_COSTOS"] = [q_ceco.strip()]

    df_sql = ddb.query_ceco_summary(cols, si=si, sf=sf, filters=local_filt, tipo_monto=tm)
    if df_sql.empty:
        return dbc.Alert("Sin resultados para los filtros indicados.", color="warning")

    # El árbol solo se auto-expande cuando el usuario pulsa "Expandir todo" (no
    # automáticamente al buscar) — la búsqueda ya filtra las ramas irrelevantes
    # en todos los niveles, pero el usuario decide qué abre y qué no.
    if q_ceco and tree_mode == "expandido":
        from dash_app.components.tree_view import create_tree_view_expanded
        totals = {"Real_2025": df_sql["Real_2025"].sum(), "Plan_2026": df_sql["Plan_2026"].sum(), "Real_2026": df_sql["Real_2026"].sum()}
        return create_tree_view_expanded("ceco-tree", df_sql, cols, total_dict=totals)

    first_col = cols[0]
    agg_df = df_sql.groupby(first_col).agg({
        "Real_2025": "sum", "Plan_2026": "sum", "Real_2026": "sum"
    }).reset_index().rename(columns={first_col: "Concepto"})

    tot_r25 = agg_df["Real_2025"].sum()
    tot_p26 = agg_df["Plan_2026"].sum()
    tot_r26 = agg_df["Real_2026"].sum()

    agg_df["Pct_r25"] = (agg_df["Real_2025"] / tot_r25 * 100) if tot_r25 else 0.0
    agg_df["Pct_p26"] = (agg_df["Plan_2026"] / tot_p26 * 100) if tot_p26 else 0.0
    agg_df["Pct_r26"] = (agg_df["Real_2026"] / tot_r26 * 100) if tot_r26 else 0.0

    agg_df["Vs_Plan_Abs"] = agg_df["Real_2026"] - agg_df["Plan_2026"]
    agg_df["Vs_Plan_Pct"] = (agg_df["Vs_Plan_Abs"] / agg_df["Plan_2026"].abs() * 100).fillna(0.0)
    agg_df["Vs_AA_Abs"] = agg_df["Real_2026"] - agg_df["Real_2025"]
    agg_df["Vs_AA_Pct"] = (agg_df["Vs_AA_Abs"] / agg_df["Real_2025"].abs() * 100).fillna(0.0)

    if first_col == "cat_Grupo_de_Cuentas":
        agg_df["_ord"] = agg_df["Concepto"].map(ds.orden_gpo_key)
        agg_df = agg_df.sort_values("_ord").drop(columns=["_ord"]).reset_index(drop=True)
    else:
        agg_df = agg_df.sort_values("Real_2026", ascending=False).reset_index(drop=True)
    totals = {"Real_2025": tot_r25, "Plan_2026": tot_p26, "Real_2026": tot_r26}

    return create_tree_view("ceco-tree", agg_df, total_dict=totals, cols=cols)

@callback(
    Output("ceco-download-csv", "data"),
    *download_inputs("ceco-btn-download"),
    State("global-filter-store", "data"),
    State("radio-ceco-vista", "value"),
    prevent_initial_call=True
)
def download_ceco_excel(_n_num, _n_conta, filters, vista):
    contable = modo_descarga()
    if contable is None:
        return dash.no_update
    si = filters.get("si", 1) if filters else 1
    sf = filters.get("sf", 36) if filters else 36
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"

    if vista == "General":
        cols = ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ID_CENTRO_COSTOS"]
    elif vista == "Detalle":
        cols = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "ID_CENTRO_COSTOS"]
    else:
        cols = ["cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3", "ID_CENTRO_COSTOS"]

    # Mismos filtros globales que el árbol en pantalla (antes el Excel salía sin ninguno).
    local_filt = {}
    if filters:
        if filters.get("fg_gpo"): local_filt["cat_Grupo_de_Cuentas"] = filters["fg_gpo"]
        if filters.get("fg_cta"): local_filt["cat_Cuentas"] = filters["fg_cta"]
        if filters.get("fg_subtipo"): local_filt["cat_Subtipo"] = filters["fg_subtipo"]
        local_filt["canal"] = filters.get("canal")
    df_sql = ddb.query_ceco_summary(cols, si=si, sf=sf, filters=local_filt, tipo_monto=tm)
    return enviar_tabla(tabla_descarga(df_sql, cols), f"ceco_{vista.lower()}_sem{si}-{sf}", contable)
