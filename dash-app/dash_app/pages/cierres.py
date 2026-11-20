# -*- coding: utf-8 -*-
"""
dash_app/pages/cierres.py
Pestaña Cierres — Seguimiento a Proyectos de Expansión (Mismos, Nuevos, Baja,
Transformaciones, Aperturas). Paridad con el Streamlit original:
1. KPIs por tipo de cierre (del Excel "Seguimiento Expansión", Master Cierres SharedDrive).
2. Tabla de Proyectos (detalle completo del Excel).
3. Árbol "Seguimiento a PDC": Grupo de Cuentas -> Cuenta -> Agrupador de Reales -> PDC.
"""
import os
from glob import glob
from dash import html, dcc, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import openpyxl
import pandas as pd
from pathlib import Path
from dash_app.services.cache_ligera import memo
from dash_app.services.data_service import DataService, M
from dash_app.components.tree_view import create_tree_view
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla, tabla_descarga

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
AGGS_DIR = BASE_DIR / "aggs"

ds = DataService()

_MASTER_CIERRES_DIR = (
    r"<SHARED_DRIVE>\1. Capex y Herramientas\Master Cierres"
)


def _excel_seguimiento_mas_reciente():
    archivos = glob(os.path.join(_MASTER_CIERRES_DIR, "Seguimiento Expansión - semana *.xlsx"))
    if not archivos:
        return None, None
    archivo = max(archivos, key=os.path.getmtime)
    return archivo, os.path.getmtime(archivo)


@memo(max_entradas=2, sello=lambda: os.path.getmtime(_excel_seguimiento_mas_reciente()[0] or __file__))
def _leer_seguimiento_excel(archivo):
    """Lee el Excel una vez por versión del archivo (0.4 s con openpyxl); se relee solo si cambia su fecha."""
    if archivo is None:
        return None, None
    wb = openpyxl.load_workbook(archivo, data_only=True)
    ws = wb["Seguimiento semanal"]

    def _a_num(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    tabla_resumen = {}
    for row_idx in range(6, 20):
        proyecto = ws[f"B{row_idx}"].value
        if not proyecto or proyecto in ("Total", None):
            continue
        tabla_resumen[proyecto] = {
            "Plan": _a_num(ws[f"I{row_idx}"].value),
            "Sem26": _a_num(ws[f"J{row_idx}"].value),
            "Sem_Actual": _a_num(ws[f"K{row_idx}"].value),
        }

    tabla_detalle = []
    for row_idx in range(22, 530):
        proyecto = ws[f"B{row_idx}"].value
        if not proyecto:
            continue
        tabla_detalle.append({
            "Proyecto": proyecto,
            "Especialidad": ws[f"C{row_idx}"].value,
            "Formato": ws[f"E{row_idx}"].value,
            "ECO": ws[f"F{row_idx}"].value,
            "PDC": ws[f"G{row_idx}"].value,
            "Comentario": ws[f"H{row_idx}"].value,
            "Plan": _a_num(ws[f"I{row_idx}"].value),
            "Sem26": _a_num(ws[f"J{row_idx}"].value),
            "Sem_Actual": _a_num(ws[f"K{row_idx}"].value),
        })

    return tabla_resumen, tabla_detalle


def _acumulado_por_categoria(si, sf, filters):
    """Real_2026 acumulado del rango vigente, agrupado por Categoria — mismo
    origen (cierres_expansion.parquet) y misma agrupación que cierres_rango()
    del original, simplificado a solo lo que necesitan los KPIs."""
    if not ds.ddb.tiene_agg("cierres_expansion"):
        return {}
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    grouped = ds.agg("cierres_expansion", ["Categoria"], filters, tm)
    return dict(zip(grouped["Categoria"].tolist(), grouped["Real_2026"].tolist()))


def layout():
    return html.Div(
        children=[
            dcc.Download(id="cierres-download-csv"),
            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                style={"marginBottom": "16px"},
                children=[
                    html.H5("Cierres — Seguimiento a Proyectos de Expansión", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "4px"}),
                    html.Div(id="cierres-nombre-archivo", style={"fontSize": "0.8rem", "color": "#94a3b8", "marginTop": "6px"})
                ]
            ),

            html.Div(id="cierres-kpis-container", style={"marginBottom": "16px"}),

            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                style={"marginBottom": "16px"},
                children=[
                    html.H6("Tabla de Proyectos", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "10px"}),
                    dcc.Loading(type="circle", children=html.Div(id="cierres-tabla-proyectos"))
                ]
            ),

            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                style={"marginBottom": "16px"},
                children=[
                    html.H6("Seguimiento a PDC", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "10px"}),
                    dbc.Row(children=[
                        dbc.Col(
                            dcc.Input(
                                id="input-buscar-cierres",
                                type="text",
                                placeholder="Filtrar por PDC o tienda...",
                                className="form-control"
                            ),
                            width=6
                        ),
                        dbc.Col(
                            html.Div(download_buttons("cierres-btn-download"), className="float-end"),
                            width=6
                        )
                    ])
                ]
            ),
            dcc.Loading(type="circle", color="#0071e3", children=html.Div(id="cierres-tree-container"))
        ]
    )


@callback(
    Output("cierres-nombre-archivo", "children"),
    Output("cierres-kpis-container", "children"),
    Output("cierres-tabla-proyectos", "children"),
    Input("global-filter-store", "data")
)
def update_cierres_kpis(filters):
    archivo, _mtime = _excel_seguimiento_mas_reciente()
    tabla_resumen, tabla_detalle = _leer_seguimiento_excel(archivo)

    if tabla_resumen is None:
        return "", dbc.Alert(
            "No se encontró Excel de 'Seguimiento Expansión'. Verifica la carpeta Master Cierres.",
            color="warning"
        ), html.Div()

    si = filters.get("si", 1) if filters else 1
    sf = filters.get("sf", 36) if filters else 36
    acum_por_cat = _acumulado_por_categoria(si, sf, filters)

    # 1. KPIs por tipo de cierre
    kpi_cards = []
    for proyecto, valores in tabla_resumen.items():
        sem_actual = valores["Sem_Actual"]
        plan = valores["Plan"]
        delta = sem_actual - plan
        acumulado = acum_por_cat.get(proyecto, 0.0)
        color = "#a93226" if delta > 0 else "#1e7d43"
        kpi_cards.append(
            dbc.Col(width=2, children=[
                html.Div(
                    className="vt-kpi-card",
                    children=[
                        html.Div(proyecto, className="vt-kpi-title"),
                        html.Div(str(sem_actual), className="vt-kpi-value"),
                        html.Div(f"Acumulado {M(acumulado)}", className="vt-kpi-sub", style={"color": color})
                    ]
                )
            ])
        )
    kpis = dbc.Row(kpi_cards, className="g-2")

    # 2. Tabla de Proyectos
    if not tabla_detalle:
        tabla_html = html.Div("Sin filas en la tabla de Seguimiento.", style={"color": "#64748b", "fontStyle": "italic"})
    else:
        df_det = pd.DataFrame(tabla_detalle)
        df_det["Δ vs Plan"] = df_det["Sem_Actual"] - df_det["Plan"]
        rows = []
        for _, r in df_det.iterrows():
            eco_val = r["ECO"]
            eco_str = "" if pd.isna(eco_val) else (str(int(eco_val)) if isinstance(eco_val, float) and eco_val.is_integer() else str(eco_val))
            delta = r["Δ vs Plan"]
            cls = "vt-var-pos" if delta > 0 else "vt-var-neg"
            rows.append(html.Tr([
                html.Td(str(r["Proyecto"]), style={"fontWeight": "600"}),
                html.Td(str(r["Especialidad"] or "")),
                html.Td(str(r["Formato"] or "")),
                html.Td(eco_str),
                html.Td(str(r["PDC"] or "")),
                html.Td(str(r["Comentario"] or ""), style={"fontSize": "0.8rem", "color": "#64748b"}),
                html.Td(str(r["Plan"]), style={"textAlign": "right"}),
                html.Td(str(r["Sem26"]), style={"textAlign": "right"}),
                html.Td(str(r["Sem_Actual"]), style={"textAlign": "right", "fontWeight": "700"}),
                html.Td(f"{delta:+d}", className=cls, style={"textAlign": "right"})
            ]))
        tabla_html = html.Div(
            className="vt-var-wrap",
            style={"maxHeight": "420px"},
            children=[
                html.Table(className="vt-var", children=[
                    html.Thead(html.Tr([
                        html.Th("Proyecto"), html.Th("Especialidad"), html.Th("Formato"),
                        html.Th("ECO"), html.Th("PDC"), html.Th("Comentario"),
                        html.Th("Plan"), html.Th("Sem 26"), html.Th("Sem Actual"), html.Th("Δ vs Plan")
                    ])),
                    html.Tbody(rows)
                ])
            ]
        )

    return f"Archivo: {os.path.basename(archivo)}" if archivo else "", kpis, tabla_html


@callback(
    Output("cierres-search-filters", "data"),
    Input("input-buscar-cierres", "value")
)
def sync_cierres_search_to_tree(q_pdc):
    if q_pdc and q_pdc.strip():
        return {"tree_key": "cierres-tree", "cat_pdc_query": q_pdc.strip().lower()}
    return {}


@callback(
    Output("cierres-tree-container", "children"),
    Input("global-filter-store", "data"),
    Input("input-buscar-cierres", "value")
)
def update_cierres(filters, q_pdc):
    if not ds.ddb.tiene_agg("cierres_arbol"):
        return dbc.Alert("dashboard.duckdb no trae agg_cierres_arbol: regenera con MENU_DASH opción 6", color="warning")

    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"

    # búsqueda de PDC como texto literal (un "(" o "*" no se interpreta como regex y no truena)
    extra = {"cat_pdc_query": q_pdc.strip().lower()} if q_pdc and q_pdc.strip() else {}

    cols = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "cat_PDC"]
    primer_col = cols[0]
    grouped = ds.agg("cierres_arbol", [primer_col], filters, tm, extra=extra)

    if grouped.empty:
        return dbc.Alert("Sin datos de cierres para el rango seleccionado.", color="warning")

    df = grouped.rename(columns={primer_col: "Concepto"})
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

    df["_ord"] = df["Concepto"].map(ds.orden_gpo_key)
    df = df.sort_values("_ord").drop(columns=["_ord"]).reset_index(drop=True)
    totals = {"Real_2025": tot_r25, "Plan_2026": tot_p26, "Real_2026": tot_r26}

    return create_tree_view("cierres-tree", df, total_dict=totals, cols=cols)


@callback(
    Output("cierres-download-csv", "data"),
    *download_inputs("cierres-btn-download"),
    State("global-filter-store", "data"),
    State("input-buscar-cierres", "value"),
    prevent_initial_call=True
)
def download_cierres_excel(_n_num, _n_conta, filters, q_pdc):
    contable = modo_descarga()
    if contable is None:
        return no_update
    if not ds.ddb.tiene_agg("cierres_arbol"):
        return no_update
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    cols = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "cat_PDC"]
    extra = {"cat_pdc_query": q_pdc.strip().lower()} if q_pdc and q_pdc.strip() else {}
    df = ds.agg("cierres_arbol", cols, filters, tm, extra=extra)
    si = filters.get("si", 1) if filters else 1
    sf = filters.get("sf", ds.smr) if filters else ds.smr
    return enviar_tabla(tabla_descarga(df, cols), f"cierres_expansion_sem{si}-{sf}", contable)
