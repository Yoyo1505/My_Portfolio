# -*- coding: utf-8 -*-
"""
dash_app/pages/detalle_cuenta.py
Pestaña Detalle Cuenta — Paridad con el Streamlit original: un único árbol
Agrupa1 -> Agrupa2 -> Agrupa3 -> Agrupador de Reales (fuente: agg_agrupadores_reales de dashboard.duckdb).
Desde 2026-10-21 ese agg trae Grupo, Cuenta y Subtipo: los filtros globales (Responsable→cuentas, Grupo, Cuenta,
Subtipo) y el buscador de cuenta aplican al árbol, a sus ramas hijas, al contador y a la descarga.
"""
from dash import html, dcc, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import pandas as pd
from pathlib import Path
from dash_app.services.data_service import DataService
from dash_app.components.tree_view import create_tree_view
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla, tabla_descarga
from dash_app.services.filtros_ui import lista, campo, grid

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
AGGS_DIR = BASE_DIR / "aggs"

ds = DataService()

def layout():
    return html.Div(
        children=[
            dcc.Download(id="cuenta-download-csv"),
            html.Div(
                className="vt-kpi-card",
                style={"marginBottom": "16px"},
                children=[
                    html.H5("Jerarquia Contable y Agrupadores", style={"fontWeight": "700", "color": "#0f172a", "marginBottom": "12px"}),
                    grid(
                        campo("Agrupa 1", dcc.Dropdown(
                            id="select-filtro-agrupa1",
                            options=[{"label": x, "value": x} for x in (ds.agrupa1 if hasattr(ds, "agrupa1") else [])],
                            multi=True, placeholder="Agrupa 1 (todas)")),
                        campo("Cuentas", html.Div(id="cuenta-count-label", className="filtros-dato")),
                    )
                ]
            ),
            dcc.Loading(type="circle", color="#0071e3", children=html.Div(id="detalle-cuenta-tree-container")),

            html.Div(
                className="mt-3 d-flex justify-content-end",
                children=[
                    download_buttons("cuenta-btn-download", style={"fontWeight": "600"})
                ]
            )
        ]
    )

@callback(
    Output("cuenta-search-filters", "data"),
    Input("select-filtro-agrupa1", "value")
)
def sync_cuenta_search_to_tree(f_agrupa1):
    """Propaga Agrupa1 al store que lee toggle_tree_branch, así también aplica a los
    niveles del árbol cargados por lazy-loading."""
    out = {}
    if lista(f_agrupa1):
        out["cat_Agrupa1"] = lista(f_agrupa1)
        out["tree_key"] = "agrup-real-tree"
    return out

@callback(
    Output("cuenta-count-label", "children"),
    Input("global-filter-store", "data")
)
def update_cuenta_count(filters):
    """Contador "N cuentas": cuentas del agg `cuentas` bajo los filtros globales."""
    if not ds.ddb.tiene_agg("cuentas"):
        return ""
    n = len(ds.ddb.distintos_agg("cuentas", "cat_Cuentas", filters, sem=True))
    return f"{n:,} cuentas"

@callback(
    Output("detalle-cuenta-tree-container", "children"),
    Input("global-filter-store", "data"),
    Input("select-filtro-agrupa1", "value")
)
def update_detalle_cuenta(filters, f_agrupa1):
    if not ds.ddb.tiene_agg("agrupadores_reales"):
        return dbc.Alert("dashboard.duckdb no trae agg_agrupadores_reales: regenera con MENU_DASH opción 6", color="warning")

    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    cols_agg = ds.ddb.columnas_agg("agrupadores_reales")
    if not {"cat_Cuentas", "cat_Grupo_de_Cuentas"} <= cols_agg and (
            (filters or {}).get("fg_cta") or (filters or {}).get("fg_gpo") or (filters or {}).get("fg_subtipo")
            ):
        return dbc.Alert("agrupadores_reales es de un build anterior (sin Grupo/Cuenta/Subtipo): regenera con "
                         "MENU_DASH opción 3 → agrupadores_reales y luego la opción 6.", color="warning")

    extra = {}
    if lista(f_agrupa1):
        extra["cat_Agrupa1"] = lista(f_agrupa1)
    grouped = ds.agg("agrupadores_reales", ["cat_Agrupa1"], filters, tm, extra=extra)

    if grouped.empty:
        return dbc.Alert("Sin datos para los criterios seleccionados.", color="warning")

    df = grouped.rename(columns={"cat_Agrupa1": "Concepto"})
    df = df[df["Concepto"].notna() & (df["Concepto"] != "nan")].copy()

    tot_r25 = df["Real_2025"].sum()
    tot_p26 = df["Plan_2026"].sum()
    tot_r26 = df["Real_2026"].sum()

    df["Pct_r25"] = (df["Real_2025"] / tot_r25 * 100) if tot_r25 else 0.0
    df["Pct_p26"] = (df["Plan_2026"] / tot_p26 * 100) if tot_p26 else 0.0
    df["Pct_r26"] = (df["Real_2026"] / tot_r26 * 100) if tot_r26 else 0.0
    df["Vs_Plan_Abs"] = df["Real_2026"] - df["Plan_2026"]
    df["Vs_Plan_Pct"] = (df["Vs_Plan_Abs"] / df["Plan_2026"].abs() * 100).fillna(0.0)
    df["Vs_AA_Abs"] = df["Real_2026"] - df["Real_2025"]
    df["Vs_AA_Pct"] = (df["Vs_AA_Abs"] / df["Real_2025"].abs() * 100).fillna(0.0)
    df = df.sort_values("Real_2026", ascending=False).reset_index(drop=True)

    totals = {"Real_2025": tot_r25, "Plan_2026": tot_p26, "Real_2026": tot_r26}
    cols = ["cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3", "cat_Agrupador_Reales"]
    return create_tree_view("agrup-real-tree", df, total_dict=totals, cols=cols)

@callback(
    Output("cuenta-download-csv", "data"),
    *download_inputs("cuenta-btn-download"),
    State("global-filter-store", "data"),
    prevent_initial_call=True
)
def download_cuenta_excel(_n_num, _n_conta, filters):
    contable = modo_descarga()
    if contable is None:
        return no_update
    if not ds.ddb.tiene_agg("cuentas"):
        return no_update
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    cols = ["cat_Grupo_de_Cuentas", "cat_Cuentas"]
    df = ds.agg("cuentas", cols, filters, tm)
    si = filters.get("si", 1) if filters else 1
    sf = filters.get("sf", ds.smr) if filters else ds.smr
    tabla = tabla_descarga(df, cols)

    # Segundo árbol de la pantalla (Agrupa1 → Agrupa2 → Agrupa3 → Agrupador Reales): la descarga
    # trae los dos, uno debajo del otro (paridad con Streamlit 2026-10-01).
    if ds.ddb.tiene_agg("agrupadores_reales"):
        cols2 = ["cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3", "cat_Agrupador_Reales"]
        g2 = ds.agg("agrupadores_reales", cols2, filters, tm)
        if not g2.empty:
            t2 = tabla_descarga(g2, cols2)
            tabla = pd.concat([tabla.assign(Arbol="Grupo de Cuentas → Cuenta"),
                               t2.assign(Arbol="Agrupadores Reales")], ignore_index=True)
            tabla = tabla[["Arbol"] + [c for c in tabla.columns if c != "Arbol"]]
    return enviar_tabla(tabla, f"cuentas_sem{si}-{sf}", contable)
