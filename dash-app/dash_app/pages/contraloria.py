# -*- coding: utf-8 -*-
"""
dash_app/pages/contraloria.py
Pestaña Contraloría — Paridad funcional 100% con Streamlit.
Soporta direcciones Contraloria->Cuenta y Cuenta->Contraloria con lazy tree de 3 niveles.
Lee contraloria.parquet (36K filas) directamente — igual que el Streamlit original.
El lazy loading de hijos (toggle_tree_branch en app.py) también usa ese parquet
vía data_service.get_tree_children (tree_key "ctrl-tree").
"""
from dash import html, dcc, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
from pathlib import Path
from dash_app.services.data_service import DataService
from dash_app.components.tree_view import create_tree_view
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla, tabla_descarga

ds = DataService()
BASE_DIR = Path(__file__).parent.parent.parent.resolve()
AGGS_DIR = BASE_DIR / "aggs"

def layout():
    return html.Div(
        children=[
            dcc.Download(id="ctrl-download-csv"),
            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                style={"marginBottom": "16px"},
                children=[
                    dbc.Row(
                        children=[
                            dbc.Col(
                                children=[
                                    html.H5("Desglose por Cuentas y Contraloría", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "0"})
                                ],
                                width=8
                            ),
                            dbc.Col(
                                html.Div(download_buttons("ctrl-btn-download"), className="float-end"),
                                width=4,
                                className="d-flex align-items-center justify-content-end"
                            )
                        ]
                    ),
                    html.Hr(style={"margin": "12px 0"}),
                    dbc.Row(
                        children=[
                            dbc.Col(
                                children=[
                                    html.Label("Dirección del Árbol:", style={"fontWeight": "600", "fontSize": "0.85rem", "marginRight": "12px"}),
                                    dbc.RadioItems(
                                        id="radio-contraloria-dir",
                                        options=[
                                            {"label": "Contraloría → Cuenta", "value": "Contraloria-Cuenta"},
                                            {"label": "Cuenta → Contraloría", "value": "Cuenta-Contraloria"}
                                        ],
                                        value="Contraloria-Cuenta",
                                        inline=True
                                    )
                                ],
                                width=12
                            )
                        ]
                    )
                ]
            ),
            html.Div(id="contraloria-tree-container")
        ]
    )

def _agg_contraloria(filters, group_cols):
    """Real 2025 / Plan 2026 / Real 2026 por `group_cols` desde agg_contraloria (dashboard.duckdb) con semanas, Grupo,
    Cuenta y Subtipo del filtro global. None si la base no trae la tabla."""
    if not ds.ddb.tiene_agg("contraloria"):
        return None
    tm = (filters or {}).get("tipo_monto", "Monto Constante")
    return ds.agg("contraloria", group_cols, filters, tm)


@callback(
    Output("contraloria-tree-container", "children"),
    Input("global-filter-store", "data"),
    Input("radio-contraloria-dir", "value")
)
def update_contraloria(filters, direccion):

    if direccion == "Contraloria-Cuenta":
        cols = ["cat_Contraloria_6", "cat_Contraloria", "cat_Grupo_de_Cuentas", "cat_Cuentas"]
    else:
        cols = ["cat_Cuentas", "cat_Grupo_de_Cuentas", "cat_Contraloria", "cat_Contraloria_6"]

    first_col = cols[0]
    grouped = _agg_contraloria(filters, [first_col])
    if grouped is None:
        return dbc.Alert("dashboard.duckdb no trae agg_contraloria: regenera con MENU_DASH opción 6.", color="warning")

    if grouped.empty:
        return dbc.Alert("Sin datos de Contraloría para los filtros aplicados.", color="warning")

    agg_df = grouped.rename(columns={first_col: "Concepto"})
    agg_df = agg_df[agg_df["Concepto"].notna() & (agg_df["Concepto"].astype(str) != "nan")].copy()

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
    elif first_col == "cat_Cuentas":
        agg_df["_ord"] = agg_df["Concepto"].map(ds.orden_cuenta_por_gpo_key)
    else:
        agg_df["_ord"] = None
    if agg_df["_ord"].notna().any():
        agg_df = agg_df.sort_values("_ord").drop(columns=["_ord"]).reset_index(drop=True)
    else:
        agg_df = agg_df.drop(columns=["_ord"]).sort_values("Real_2026", ascending=False).reset_index(drop=True)

    totals = {"Real_2025": tot_r25, "Plan_2026": tot_p26, "Real_2026": tot_r26}
    return create_tree_view("ctrl-tree", agg_df, total_dict=totals, cols=cols)

@callback(
    Output("ctrl-download-csv", "data"),
    *download_inputs("ctrl-btn-download"),
    State("global-filter-store", "data"),
    State("radio-contraloria-dir", "value"),
    prevent_initial_call=True
)
def download_contraloria_excel(_n_num, _n_conta, filters, direccion):
    contable = modo_descarga()
    if contable is None:
        return no_update
    si = int((filters or {}).get("si", 1))
    sf = int((filters or {}).get("sf", ds.smr))

    if direccion == "Contraloria-Cuenta":
        cols = ["cat_Contraloria_6", "cat_Contraloria", "cat_Grupo_de_Cuentas", "cat_Cuentas"]
    else:
        cols = ["cat_Cuentas", "cat_Grupo_de_Cuentas", "cat_Contraloria", "cat_Contraloria_6"]

    grouped = _agg_contraloria(filters, cols)
    if grouped is None:
        return no_update
    return enviar_tabla(tabla_descarga(grouped, cols), f"contraloria_{direccion}_sem{si}-{sf}", contable)
