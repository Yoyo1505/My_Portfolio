# -*- coding: utf-8 -*-
"""
dash_app/pages/temporalidad.py
Pestaña Temporalidad — Evolución Temporal del Gasto con Granularidad (Semanal, Mensual, Trimestral) y Comparativo A vs B.
Paridad funcional 100% con Streamlit.
"""
from dash import html, dcc, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict
from functools import lru_cache
from dash_app.services.data_service import DataService, MC
from dash_app.components.tree_view import create_tree_view
from dash_app.components import charts
from dash_app.services.duckdb_service import DuckDBService
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla

ds = DataService()
ddb = DuckDBService()
BASE_DIR = Path(__file__).parent.parent.parent.resolve()
AGGS_DIR = BASE_DIR / "aggs"

_PALETA_ENTIDADES = ["#a93226", "#1f618d", "#145a32", "#b7950b", "#6c3483",
                     "#117864", "#ba4a00", "#5d6d7e", "#873600", "#1a5276",
                     "#7b241c", "#186a3b"]

# Mismo cálculo que rango_mes()/pesos_mes() del Streamlit original (ver app.py,
# _rango_mes_arg) — necesario aquí para el selector "Periodo A/B" cuando se
# compara por Mes.
_ANCLA_ARG = {2025: date(2024, 12, 30), 2026: date(2025, 12, 29)}

@lru_cache(maxsize=8)
def _pesos_mes_arg(anio):
    ini1 = _ANCLA_ARG[anio]
    out = {}
    for s in range(1, 54):
        ini = ini1 + timedelta(days=7 * (s - 1))
        dias = defaultdict(int)
        for i in range(7):
            d = ini + timedelta(days=i)
            dias[(d.year, d.month)] += 1
        if any(y < anio for (y, _) in dias):
            dias = {(anio, 1): 7}
        elif any(y > anio for (y, _) in dias):
            dias = {(anio, 12): 7}
        if dias:
            out[s] = {k: n / 7 for k, n in dias.items()}
    return out

def _rango_mes_arg(mes, anio=2026):
    p = _pesos_mes_arg(anio)
    sems = [s for s, d in p.items() if (anio, mes) in d]
    return (min(sems), max(sems)) if sems else (1, 1)

_QRANGO_TRI = {"Q1": (1, 13), "Q2": (14, 26), "Q3": (27, 39), "Q4": (40, 53), "FY": (1, 53)}
_NOM_MES = {1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
            7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"}

def layout():
    return html.Div(
        children=[
            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                style={"marginBottom": "20px"},
                children=[
                    dbc.Row(
                        children=[
                            dbc.Col(
                                children=[
                                    html.H5("Evolución Temporal del Gasto", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "0"})
                                ],
                                width=7
                            ),
                            dbc.Col(
                                children=[
                                    html.Label("Granularidad:", style={"fontWeight": "600", "fontSize": "0.85rem", "marginRight": "8px"}),
                                    dbc.RadioItems(
                                        id="radio-temp-gran",
                                        options=[
                                            {"label": "Semanal", "value": "Semanal"},
                                            {"label": "Mensual", "value": "Mensual"},
                                            {"label": "Trimestral", "value": "Trimestral"},
                                            {"label": "Comparativo (A vs B)", "value": "Comparativo"},
                                            {"label": "Seguimiento CECO/División", "value": "Seguimiento"}
                                        ],
                                        value="Semanal",
                                        inline=True
                                    )
                                ],
                                width=5,
                                className="d-flex align-items-center justify-content-end"
                            )
                        ]
                    )
                ]
            ),

            # Vistas normales (Semanal/Mensual/Trimestral): gráfica + tabla + 2 gráficas
            html.Div(id="temp-vista-normal", children=[
                html.Div(
                    className="p-3 bg-white border rounded shadow-sm",
                    style={"marginBottom": "20px"},
                    children=[dcc.Graph(id="temporalidad-grafica-lineas", config={"displayModeBar": True})]
                ),
                html.Div(
                    className="p-3 bg-white border rounded shadow-sm",
                    style={"marginBottom": "20px"},
                    children=[
                        html.Div(
                            className="d-flex justify-content-between align-items-center mb-2",
                            children=[
                                html.H6(id="temporalidad-tabla-titulo", style={"fontWeight": "700", "color": "#1e2430", "margin": "0"}),
                                download_buttons("btn-csv-temporalidad-tabla", color="light", outline=False, className="border")
                            ]
                        ),
                        dcc.Download(id="download-csv-temporalidad-tabla"),
                        html.Div(id="temporalidad-tabla-container", className="table-responsive")
                    ]
                ),
                dbc.Row(
                    children=[
                        dbc.Col(
                            html.Div(
                                className="p-3 bg-white border rounded shadow-sm",
                                children=[
                                    html.H6("Gasto Acumulado por Trimestre", style={"fontWeight": "700", "color": "#1e2430"}),
                                    dcc.Graph(id="temporalidad-grafica-trimestre", config={"displayModeBar": True})
                                ]
                            ),
                            width=6
                        ),
                        dbc.Col(
                            html.Div(
                                className="p-3 bg-white border rounded shadow-sm",
                                children=[
                                    html.H6("Variación del Período vs Plan (MDP)", style={"fontWeight": "700", "color": "#1e2430"}),
                                    dcc.Graph(id="temporalidad-grafica-variacion", config={"displayModeBar": True})
                                ]
                            ),
                            width=6
                        )
                    ]
                )
            ]),

            # Vista Comparativo (A vs B): árbol Grupo de Cuentas -> Cuenta, dos periodos
            html.Div(id="temp-vista-comparativo", style={"display": "none"}, children=[
                html.Div(
                    className="p-3 bg-white border rounded shadow-sm",
                    style={"marginBottom": "16px"},
                    children=[
                        dbc.RadioItems(
                            id="temp-comp-tipo",
                            options=[{"label": t, "value": t} for t in ["Semana", "Mes", "Trimestre"]],
                            value="Semana",
                            inline=True
                        )
                    ]
                ),
                html.Div(
                    className="p-3 bg-white border rounded shadow-sm",
                    style={"marginBottom": "16px"},
                    children=[
                        html.Div(
                            className="d-flex justify-content-between align-items-center",
                            children=[
                                html.Label("Periodo A", style={"fontWeight": "700"}),
                                download_buttons("btn-csv-temp-comp-a", color="light", outline=False, className="border")
                            ]
                        ),
                        dcc.Download(id="download-csv-temp-comp-a"),
                        dcc.Dropdown(id="temp-comp-periodo-a", clearable=False, className="form-select-sm shadow-none", style={"marginBottom": "10px"}),
                        dcc.Loading(type="circle", children=html.Div(id="temp-comp-arbol-a"))
                    ]
                ),
                html.Div(
                    className="p-3 bg-white border rounded shadow-sm",
                    children=[
                        html.Div(
                            className="d-flex justify-content-between align-items-center",
                            children=[
                                html.Label("Periodo B", style={"fontWeight": "700"}),
                                download_buttons("btn-csv-temp-comp-b", color="light", outline=False, className="border")
                            ]
                        ),
                        dcc.Download(id="download-csv-temp-comp-b"),
                        dcc.Dropdown(id="temp-comp-periodo-b", clearable=False, className="form-select-sm shadow-none", style={"marginBottom": "10px"}),
                        dcc.Loading(type="circle", children=html.Div(id="temp-comp-arbol-b"))
                    ]
                )
            ]),

            # Vista Seguimiento CECO/División — 5º modo del original (app.py
            # _vista_seguimiento_entidad, 2026-09-15) que no existía en Dash en
            # absoluto. Gráfica multilínea de Real 2026 semanal + tabla de
            # seguimiento, por División (12 valores, completa) o por los Top 10
            # CECO con mayor |desviación| vs Plan acumulada en el año (66k+
            # CECOs en el catálogo -- mostrarlos todos no sería legible).
            html.Div(id="temp-vista-seguimiento", style={"display": "none"}, children=[
                html.Div(
                    className="p-3 bg-white border rounded shadow-sm",
                    style={"marginBottom": "16px"},
                    children=[
                        html.P(
                            "Seguimiento del gasto Real 2026 semana a semana, desglosado por División o "
                            "por los 10 Centros de Costo (CECO) con mayor desviación absoluta vs Plan en el año.",
                            style={"color": "#6b7280", "fontSize": "0.85rem"}
                        ),
                        dbc.RadioItems(
                            id="temp-seg-agrupar",
                            options=[{"label": "División", "value": "Division"}, {"label": "Top 10 Centros de Costo", "value": "CECO"}],
                            value="Division",
                            inline=True
                        )
                    ]
                ),
                html.Div(
                    className="p-3 bg-white border rounded shadow-sm",
                    style={"marginBottom": "16px"},
                    children=[dcc.Loading(type="circle", children=dcc.Graph(id="temp-seg-grafica", config={"displayModeBar": True}))]
                ),
                html.Div(
                    className="p-3 bg-white border rounded shadow-sm",
                    children=[
                        html.Div(
                            className="d-flex justify-content-between align-items-center mb-2",
                            children=[
                                html.H6(id="temp-seg-tabla-titulo", style={"fontWeight": "700", "color": "#1e2430", "margin": "0"}),
                                download_buttons("btn-csv-temp-seg", color="light", outline=False, className="border")
                            ]
                        ),
                        dcc.Download(id="download-csv-temp-seg"),
                        dcc.Loading(type="circle", children=html.Div(id="temp-seg-tabla-container", className="table-responsive"))
                    ]
                )
            ])
        ]
    )

@callback(
    Output("temp-vista-normal", "style"),
    Output("temp-vista-comparativo", "style"),
    Output("temp-vista-seguimiento", "style"),
    Input("radio-temp-gran", "value")
)
def toggle_vista_temporalidad(gran):
    oculto = {"display": "none"}
    if gran == "Comparativo":
        return oculto, {}, oculto
    if gran == "Seguimiento":
        return oculto, oculto, {}
    return {}, oculto, oculto

@callback(
    Output("temp-comp-periodo-a", "options"),
    Output("temp-comp-periodo-a", "value"),
    Output("temp-comp-periodo-b", "options"),
    Output("temp-comp-periodo-b", "value"),
    Input("temp-comp-tipo", "value")
)
def update_opciones_comparativo(tipo_comp):
    if tipo_comp == "Semana":
        opts = [{"label": f"Semana {s}", "value": s} for s in range(1, 54)]
        return opts, 1, opts, ds.smr
    elif tipo_comp == "Mes":
        opts = [{"label": _NOM_MES[m], "value": m} for m in range(1, 13)]
        return opts, 1, opts, 12
    else:
        opts = [{"label": q, "value": q} for q in ["Q1", "Q2", "Q3", "Q4"]]
        return opts, "Q1", opts, "Q4"

def _rango_comp(tipo_comp, valor):
    if tipo_comp == "Semana":
        return int(valor), int(valor)
    if tipo_comp == "Mes":
        return _rango_mes_arg(valor)
    return _QRANGO_TRI[valor]

def _arbol_comparativo(filters, tipo_comp, valor):
    if valor is None:
        return html.Div()
    si, sf = _rango_comp(tipo_comp, valor)
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"

    # 2026-10-21: siempre cuentas.parquet (trae Grupo, Cuenta y Subtipo, ambos canales). pdc.parquet (887 MB)
    # no traía Subtipo y el filtro global se ignoraba; este árbol solo agrupa por Grupo/Cuenta.
    if not ds.ddb.tiene_agg("cuentas"):
        return dbc.Alert("dashboard.duckdb no trae agg_cuentas: regenera con MENU_DASH opción 6", color="warning")

    f_rango = dict(filters or {})
    f_rango.update({"si": si, "sf": sf})      # el rango es el del periodo A/B, no el del filtro global
    grouped = ds.agg("cuentas", ["cat_Grupo_de_Cuentas"], f_rango, tm)

    if grouped.empty:
        return dbc.Alert("Sin datos para ese período.", color="warning")

    df = grouped.rename(columns={"cat_Grupo_de_Cuentas": "Concepto"})
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
    return create_tree_view(f"cmp-tree-{tipo_comp}-{valor}", df, total_dict=totals, cols=["cat_Grupo_de_Cuentas", "cat_Cuentas"])


def _df_comparativo_completo(filters, tipo_comp, valor):
    """Grupo de Cuentas + Cuenta (ambos niveles, no solo Grupo como el árbol
    en pantalla) para el CSV — paridad con `_agregar_df(..., ["cat_Grupo_de_
    Cuentas","cat_Cuentas"], ...)` del original."""
    if valor is None:
        return pd.DataFrame()
    si, sf = _rango_comp(tipo_comp, valor)
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    if not ds.ddb.tiene_agg("cuentas"):
        return pd.DataFrame()
    f_rango = dict(filters or {})
    f_rango.update({"si": si, "sf": sf})
    grouped = ds.agg("cuentas", ["cat_Grupo_de_Cuentas", "cat_Cuentas"], f_rango, tm)
    if grouped.empty:
        return pd.DataFrame()
    df = grouped
    df["Vs_Plan_Abs"] = df["Real_2026"] - df["Plan_2026"]
    df["Vs_AA_Abs"] = df["Real_2026"] - df["Real_2025"]
    return df

@callback(
    Output("temp-comp-arbol-a", "children"),
    Input("global-filter-store", "data"),
    Input("temp-comp-tipo", "value"),
    Input("temp-comp-periodo-a", "value")
)
def update_arbol_comparativo_a(filters, tipo_comp, valor):
    return _arbol_comparativo(filters, tipo_comp, valor)

@callback(
    Output("temp-comp-arbol-b", "children"),
    Input("global-filter-store", "data"),
    Input("temp-comp-tipo", "value"),
    Input("temp-comp-periodo-b", "value")
)
def update_arbol_comparativo_b(filters, tipo_comp, valor):
    return _arbol_comparativo(filters, tipo_comp, valor)


@callback(
    Output("download-csv-temp-comp-a", "data"),
    *download_inputs("btn-csv-temp-comp-a"),
    State("global-filter-store", "data"),
    State("temp-comp-tipo", "value"),
    State("temp-comp-periodo-a", "value"),
    prevent_initial_call=True
)
def download_temp_comp_a(_n_num, _n_conta, filters, tipo_comp, valor):
    contable = modo_descarga()
    if contable is None:
        return no_update
    df = _df_comparativo_completo(filters, tipo_comp, valor)
    if df.empty:
        return no_update
    return enviar_tabla(df, f"comparativo_{tipo_comp}_{valor}_A", contable)


@callback(
    Output("download-csv-temp-comp-b", "data"),
    *download_inputs("btn-csv-temp-comp-b"),
    State("global-filter-store", "data"),
    State("temp-comp-tipo", "value"),
    State("temp-comp-periodo-b", "value"),
    prevent_initial_call=True
)
def download_temp_comp_b(_n_num, _n_conta, filters, tipo_comp, valor):
    contable = modo_descarga()
    if contable is None:
        return no_update
    df = _df_comparativo_completo(filters, tipo_comp, valor)
    if df.empty:
        return no_update
    return enviar_tabla(df, f"comparativo_{tipo_comp}_{valor}_B", contable)

def _celda_var_temp(delta, texto):
    if delta is None or pd.isna(delta):
        return html.Td(texto, style={"textAlign": "right"})
    color = "#78281f" if delta > 0 else "#145a32"
    return html.Td(texto, style={"textAlign": "right", "color": color, "fontWeight": "600"})


def _forecast_trimestral(df_w, df_plot, filters):
    """Forecast oficial por trimestre (suma de sus semanas), alineado a las filas de df_plot (columna 'periodo')."""
    fc_w = ds.compute_forecast_weekly(filters)
    por_sem = dict(zip(fc_w["sem"], fc_w["Forecast"]))
    tmp = df_w.assign(_fc=df_w["sem"].map(por_sem).fillna(0.0))
    por_q = tmp.groupby("periodo")["_fc"].sum()
    return df_plot["periodo"].map(por_q).fillna(0.0).to_list()


def _tabla_periodo_df(df_plot, gran, forecast_cierre_scalar=None):
    """Tabla de gasto Semanal/Mensual/Trimestral — paridad con la sección
    '2. TABLA' de _vista_temporalidad (app.py:5402-5455): Real25/Plan26/
    Real26 + Vs Plan/Vs AA (Abs y %) + acumulados, y para Trimestral además
    Forecast (oficial, FCST VTA RAPIDA.xlsx) por trimestre y su variación."""
    t = df_plot.copy()
    t["Vs_Plan_Abs"] = t["Real_2026"] - t["Plan_2026"]
    t["Vs_Plan_Pct"] = np.where(t["Plan_2026"] != 0, t["Vs_Plan_Abs"] / t["Plan_2026"].abs() * 100, 0.0)
    t["Vs_AA_Abs"] = t["Real_2026"] - t["Real_2025"]
    t["Vs_AA_Pct"] = np.where(t["Real_2025"] != 0, t["Vs_AA_Abs"] / t["Real_2025"].abs() * 100, 0.0)
    t["Acum_Real_2026"] = t["Real_2026"].cumsum()
    t["Acum_Plan_2026"] = t["Plan_2026"].cumsum()

    if gran == "Semanal":
        t["Periodo"] = t["sem"].apply(lambda s: f"Sem {int(s)}")
    else:
        t["Periodo"] = t["periodo"].astype(str)

    if gran == "Trimestral":
        t["Forecast_Cierre"] = forecast_cierre_scalar if forecast_cierre_scalar is not None else 0.0
    return t


def _tabla_periodo_html(t, gran, titulo_total="TOTAL"):
    cols_money = [("Real_2025", "Real 2025"), ("Plan_2026", "Plan 2026"), ("Real_2026", "Real 2026")]
    ths = [html.Th("Periodo")] + [html.Th(lbl) for _, lbl in cols_money]
    ths += [html.Th("Vs Plan"), html.Th("Vs Plan %"), html.Th("Vs AA"), html.Th("Vs AA %"),
            html.Th("Acum. Real 2026"), html.Th("Acum. Plan 2026")]
    if gran == "Trimestral":
        ths += [html.Th("Forecast Finanzas"), html.Th("Vs Forecast"), html.Th("Vs Forecast %")]

    def _fila(r, es_total=False):
        tds = [html.Td(r["Periodo"], style={"fontWeight": "700" if es_total else "500"})]
        for c, _ in cols_money:
            estilo = {"textAlign": "right"}
            if c == "Real_2026":
                estilo["backgroundColor"] = "#fffbeb"
            if es_total:
                estilo["fontWeight"] = "700"
            tds.append(html.Td(MC(r[c]), style=estilo))
        tds.append(_celda_var_temp(r["Vs_Plan_Abs"], MC(r["Vs_Plan_Abs"])))
        tds.append(_celda_var_temp(r["Vs_Plan_Abs"], f"{r['Vs_Plan_Pct']:+.1f}%"))
        tds.append(_celda_var_temp(r["Vs_AA_Abs"], MC(r["Vs_AA_Abs"])))
        tds.append(_celda_var_temp(r["Vs_AA_Abs"], f"{r['Vs_AA_Pct']:+.1f}%"))
        tds.append(html.Td(MC(r["Acum_Real_2026"]), style={"textAlign": "right"}))
        tds.append(html.Td(MC(r["Acum_Plan_2026"]), style={"textAlign": "right"}))
        if gran == "Trimestral":
            fc = r.get("Forecast_Cierre", 0.0)
            vs_fc = r["Real_2026"] - fc
            pct_fc = (vs_fc / abs(fc) * 100) if fc else 0.0
            tds.append(html.Td(MC(fc), style={"textAlign": "right"}))
            tds.append(_celda_var_temp(vs_fc, MC(vs_fc)))
            tds.append(_celda_var_temp(vs_fc, f"{pct_fc:+.1f}%"))
        style = {"backgroundColor": "#eef0f3", "borderTop": "2px solid #b8bfc8"} if es_total else {}
        return html.Tr(tds, style=style)

    filas = [_fila(r) for _, r in t.iterrows()]
    if not t.empty:
        tot = {
            "Periodo": titulo_total, "Real_2025": t["Real_2025"].sum(), "Plan_2026": t["Plan_2026"].sum(),
            "Real_2026": t["Real_2026"].sum(), "Acum_Real_2026": t["Acum_Real_2026"].iloc[-1],
            "Acum_Plan_2026": t["Acum_Plan_2026"].iloc[-1],
        }
        tot["Vs_Plan_Abs"] = tot["Real_2026"] - tot["Plan_2026"]
        tot["Vs_Plan_Pct"] = (tot["Vs_Plan_Abs"] / abs(tot["Plan_2026"]) * 100) if tot["Plan_2026"] else 0.0
        tot["Vs_AA_Abs"] = tot["Real_2026"] - tot["Real_2025"]
        tot["Vs_AA_Pct"] = (tot["Vs_AA_Abs"] / abs(tot["Real_2025"]) * 100) if tot["Real_2025"] else 0.0
        if gran == "Trimestral":
            tot["Forecast_Cierre"] = t["Forecast_Cierre"].sum()
        filas.append(_fila(tot, es_total=True))

    return dbc.Table(
        [html.Thead(html.Tr(ths)), html.Tbody(filas)],
        bordered=True, hover=True, size="sm", className="mb-0", style={"fontSize": "0.8rem"}
    )


@callback(
    Output("temporalidad-grafica-lineas", "figure"),
    Output("temporalidad-grafica-trimestre", "figure"),
    Output("temporalidad-grafica-variacion", "figure"),
    Output("temporalidad-tabla-container", "children"),
    Output("temporalidad-tabla-titulo", "children"),
    Input("global-filter-store", "data"),
    Input("radio-temp-gran", "value")
)
def update_temporalidad(filters, gran):
    if gran in ("Comparativo", "Seguimiento"):
        return no_update, no_update, no_update, no_update, no_update

    df_w = ds.compute_weekly_series(filters)

    if gran == "Mensual":
        # Agrupar semanas por mes (aprox 4.4 semanas por mes)
        df_w["_mes_num"] = df_w["sem"].apply(lambda s: int(np.ceil(s / 4.41)))
        df_w["periodo"] = df_w["_mes_num"].apply(lambda m: f"Mes {m}")
        df_agg = df_w.groupby(["_mes_num", "periodo"], as_index=False).agg({
            "Real_2025": "sum", "Plan_2026": "sum", "Real_2026": "sum"
        }).sort_values("_mes_num").drop(columns=["_mes_num"]).reset_index(drop=True)
        x_vals = df_agg["periodo"]
        df_plot = df_agg
    elif gran == "Trimestral":
        df_w["_q_num"] = df_w["sem"].apply(lambda s: int(np.ceil(s / 13)))
        df_w["periodo"] = df_w["_q_num"].apply(lambda q: f"Q{q}")
        df_agg = df_w.groupby(["_q_num", "periodo"], as_index=False).agg({
            "Real_2025": "sum", "Plan_2026": "sum", "Real_2026": "sum"
        }).sort_values("_q_num").drop(columns=["_q_num"]).reset_index(drop=True)
        x_vals = df_agg["periodo"]
        df_plot = df_agg
    else:
        x_vals = df_w["sem"]
        df_plot = df_w

    # Tooltips enriquecidos
    hover_r25 = [f"<b>Real 2025:</b> {v/1e6:,.1f} MDP" for v in df_plot["Real_2025"]]
    hover_p26 = [f"<b>Plan 2026:</b> {v/1e6:,.1f} MDP" for v in df_plot["Plan_2026"]]
    
    hover_r26 = []
    for r26, p26, r25 in zip(df_plot["Real_2026"], df_plot["Plan_2026"], df_plot["Real_2025"]):
        if pd.isna(r26) or r26 == 0:
            hover_r26.append("<b>Real 2026:</b> —")
        else:
            vp = (r26 - p26) / 1e6
            pct_p = (r26 - p26) / abs(p26) * 100 if p26 else 0
            va = (r26 - r25) / 1e6
            pct_a = (r26 - r25) / abs(r25) * 100 if r25 else 0
            hover_r26.append(
                f"<b>Real 2026:</b> {r26/1e6:,.1f} MDP<br>"
                f"<b>vs Plan:</b> {vp:+.1f} MDP ({pct_p:+.1f}%)<br>"
                f"<b>vs AA:</b> {va:+.1f} MDP ({pct_a:+.1f}%)"
            )

    fig_lineas = go.Figure()
    fig_lineas.add_trace(go.Bar(
        x=x_vals, y=df_plot["Real_2025"] / 1e6, name="Real 2025",
        text=hover_r25, hoverinfo="text+x",
        marker_color=charts.color_serie("Real_2025"), opacity=0.96
    ))
    fig_lineas.add_trace(go.Scatter(
        x=x_vals, y=df_plot["Plan_2026"] / 1e6, name="Plan 2026",
        text=hover_p26, hoverinfo="text+x",
        line={"color": charts.color_serie("Plan_2026"), "width": 2.5, "dash": "dash"},
        mode="lines+markers", marker={"size": 6}
    ))
    fig_lineas.add_trace(go.Bar(
        x=x_vals, y=df_plot["Real_2026"] / 1e6, name="Real 2026",
        text=hover_r26, hoverinfo="text+x",
        marker_color=charts.color_serie("Real_2026"), opacity=0.96
    ))
    fig_lineas.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    fig_lineas.update_xaxes(title=f"Granularidad: {gran}", tickangle=-35, tickfont={"size": 11})
    fig_lineas.update_traces(marker_cornerradius=4, selector=dict(type="bar"))
    charts.estilo(fig_lineas, height=430, hovermode="closest")

    # 2. Barras por Trimestre
    q_labels = ["Q1 (Sem 1-13)", "Q2 (Sem 14-26)", "Q3 (Sem 27-39)", "Q4 (Sem 40-53)"]
    q_r25 = [
        df_w.iloc[0:13]["Real_2025"].sum() / 1e6,
        df_w.iloc[13:26]["Real_2025"].sum() / 1e6,
        df_w.iloc[26:39]["Real_2025"].sum() / 1e6,
        df_w.iloc[39:53]["Real_2025"].sum() / 1e6,
    ]
    q_p26 = [
        df_w.iloc[0:13]["Plan_2026"].sum() / 1e6,
        df_w.iloc[13:26]["Plan_2026"].sum() / 1e6,
        df_w.iloc[26:39]["Plan_2026"].sum() / 1e6,
        df_w.iloc[39:53]["Plan_2026"].sum() / 1e6,
    ]
    q_r26 = [
        df_w.iloc[0:13]["Real_2026"].sum() / 1e6,
        df_w.iloc[13:26]["Real_2026"].sum() / 1e6,
        df_w.iloc[26:39]["Real_2026"].sum() / 1e6,
        df_w.iloc[39:53]["Real_2026"].sum() / 1e6,
    ]

    fig_q = go.Figure()
    fig_q.add_trace(go.Bar(x=q_labels, y=q_r25, name="Real 2025", marker_color=charts.color_serie("Real_2025")))
    fig_q.add_trace(go.Bar(x=q_labels, y=q_p26, name="Plan 2026", marker_color=charts.color_serie("Plan_2026")))
    fig_q.add_trace(go.Bar(x=q_labels, y=q_r26, name="Real 2026", marker_color=charts.color_serie("Real_2026")))
    fig_q.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    fig_q.update_traces(marker_cornerradius=4)
    charts.estilo(fig_q, height=300, ytitle="MDP", hovermode="closest", margin=dict(l=60, r=10, t=46, b=40))

    # 3. Variación de Período vs Plan
    valid_p = df_plot[df_plot["Real_2026"].notna() & (df_plot["Real_2026"] > 0)].copy()
    if "sem" in valid_p.columns:
        valid_p = valid_p[valid_p["sem"] <= ds.smr]  # 2026-09-21: Real futuro = Plan (híbrido), no es variación
    diff = (valid_p["Real_2026"] - valid_p["Plan_2026"]) / 1e6
    colors = ["#d6453a" if x > 0 else "#1e7d43" for x in diff]

    fig_var = go.Figure()
    fig_var.add_trace(go.Bar(x=valid_p.get("sem", valid_p.get("periodo")), y=diff, marker_color=colors, name="vs Plan"))
    fig_var.update_traces(marker_cornerradius=3)
    fig_var.update_xaxes(title=f"Período ({gran})", tickangle=-35, tickfont={"size": 11})
    charts.estilo(fig_var, height=360, ytitle="Sobre/Bajo Plan (MDP)", hovermode="closest",
                  margin=dict(l=60, r=16, t=20, b=100))
    fig_var.update_layout(showlegend=False)

    # --- 4. Tabla de gasto por periodo (faltaba por completo en Dash) ---
    forecast_cierre_scalar = _forecast_trimestral(df_w, df_plot, filters) if gran == "Trimestral" else None
    tabla_df = _tabla_periodo_df(df_plot, gran, forecast_cierre_scalar)
    titulo_tabla = f"Tabla de gasto {'trimestral' if gran == 'Trimestral' else 'mensual' if gran == 'Mensual' else 'semanal'}"
    tabla_html = _tabla_periodo_html(tabla_df, gran)

    return fig_lineas, fig_q, fig_var, tabla_html, titulo_tabla


@callback(
    Output("download-csv-temporalidad-tabla", "data"),
    *download_inputs("btn-csv-temporalidad-tabla"),
    State("global-filter-store", "data"),
    State("radio-temp-gran", "value"),
    prevent_initial_call=True
)
def download_temporalidad_tabla(_n_num, _n_conta, filters, gran):
    contable = modo_descarga()
    if contable is None or gran in ("Comparativo", "Seguimiento"):
        return no_update
    df_w = ds.compute_weekly_series(filters)
    if gran == "Mensual":
        df_w["periodo"] = df_w["sem"].apply(lambda s: f"Mes {int(np.ceil(s / 4.41))}")
        df_plot = df_w.groupby("periodo", as_index=False).agg({"Real_2025": "sum", "Plan_2026": "sum", "Real_2026": "sum"})
    elif gran == "Trimestral":
        df_w["periodo"] = df_w["sem"].apply(lambda s: f"Q{int(np.ceil(s / 13))}")
        df_plot = df_w.groupby("periodo", as_index=False).agg({"Real_2025": "sum", "Plan_2026": "sum", "Real_2026": "sum"})
    else:
        df_plot = df_w

    forecast_cierre_scalar = _forecast_trimestral(df_w, df_plot, filters) if gran == "Trimestral" else None
    tabla_df = _tabla_periodo_df(df_plot, gran, forecast_cierre_scalar)
    si = (filters or {}).get("si", 1)
    sf = (filters or {}).get("sf", ds.smr)
    return enviar_tabla(tabla_df, f"gasto_{gran.lower()}_sem{si}-{sf}", contable)


# ============================================================ Seguimiento CECO/División
# 5º modo de granularidad, portado de _vista_seguimiento_entidad (app.py:5243-
# 5276) — no existía en absoluto en Dash. División (12 valores) se muestra
# completa; CECO se acota a los Top 10 con mayor |desviación| absoluta vs
# Plan acumulada en el año (66k+ CECOs en el catálogo -- mostrarlos todos no
# sería legible ni útil), mismo criterio que el original.
_ENTIDAD_SEG = {
    "Division": ("cat_Direccion_Division", "División"),
    "CECO": ("ID_CENTRO_COSTOS", "CECO"),
}


def _local_filt_seguimiento(filters):
    local_filt = {}
    if filters:
        if filters.get("fg_resp"):
            local_filt["cat_Responsable_Grupo"] = filters["fg_resp"]
        if filters.get("fg_gpo"):
            local_filt["cat_Grupo_de_Cuentas"] = filters["fg_gpo"]
        if filters.get("fg_cta"):
            local_filt["cat_Cuentas"] = filters["fg_cta"]
        if filters.get("fg_subtipo"):
            local_filt["cat_Subtipo"] = filters["fg_subtipo"]
        local_filt["canal"] = filters.get("canal")
    return local_filt


def _df_seguimiento(filters, agrupar_por, top_n=10):
    entity_col, _ = _ENTIDAD_SEG[agrupar_por]
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    local_filt = _local_filt_seguimiento(filters)
    # exclude_zero_cols NO sirve aquí -- hace `col != 0` en SQL, pensado para
    # columnas numéricas como ECO_PDV; cat_Direccion_Division/ID_CENTRO_COSTOS
    # son texto/id y DuckDB truena al intentar castear "Division Centro" a
    # INT32. El descarte de "0"/blanco se hace después, en pandas (como en el
    # original: weekly_por_entidad, app.py:1708).
    df = ddb.query_ceco_summary([entity_col, "sem"], si=1, sf=ds.smp, filters=local_filt, tipo_monto=tm)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[df[entity_col].notna() & (df[entity_col].astype(str) != "0")]
    if entity_col == "ID_CENTRO_COSTOS" and not df.empty:
        g = df.groupby(entity_col)[["Real_2026", "Plan_2026"]].sum()
        top = (g["Real_2026"] - g["Plan_2026"]).abs().sort_values(ascending=False).head(top_n).index
        df = df[df[entity_col].isin(top)]
    return df.sort_values([entity_col, "sem"]).reset_index(drop=True)


def _grafica_seguimiento(df, entity_col, entity_label):
    fig = go.Figure()
    for i, (nombre, g) in enumerate(df.groupby(entity_col)):
        g = g.sort_values("sem")
        color = _PALETA_ENTIDADES[i % len(_PALETA_ENTIDADES)]
        fig.add_scatter(
            name=str(nombre), x=g["sem"], y=g["Real_2026"] / 1e6, mode="lines+markers",
            line=dict(color=color, width=2), marker=dict(size=5),
            hovertemplate=f"<b>{entity_label} {nombre}</b><br>Sem %{{x}}: <b>%{{y:,.2f}} MDP</b><extra></extra>"
        )
    fig.update_xaxes(title="Semana", tickangle=-45, tickfont={"size": 10},
                     tickmode="array", tickvals=sorted(df["sem"].unique().tolist()))
    charts.estilo(fig, height=430, ytitle="Real 2026 (MDP)", hovermode="closest",
                  margin=dict(l=60, r=16, t=60, b=60))
    fig.update_layout(title=dict(text=f"Real 2026 semanal por {entity_label}", font=dict(size=14), x=0, y=0.98))
    return fig


def _tabla_seguimiento_df(df, entity_col, entity_label):
    tbl = df.rename(columns={entity_col: entity_label, "sem": "Semana"})
    tbl["Desviacion_vs_Plan"] = tbl["Real_2026"] - tbl["Plan_2026"]
    return tbl[[entity_label, "Semana", "Real_2025", "Plan_2026", "Real_2026", "Desviacion_vs_Plan"]]


def _tabla_seguimiento_html(tbl, entity_label):
    ths = [html.Th(entity_label), html.Th("Semana"), html.Th("Real 2025"),
           html.Th("Plan 2026"), html.Th("Real 2026"), html.Th("Desviación vs Plan")]
    filas = []
    for _, r in tbl.iterrows():
        filas.append(html.Tr([
            html.Td(str(r[entity_label])),
            html.Td(f"Sem {int(r['Semana'])}"),
            html.Td(MC(r["Real_2025"]), style={"textAlign": "right"}),
            html.Td(MC(r["Plan_2026"]), style={"textAlign": "right"}),
            html.Td(MC(r["Real_2026"]), style={"textAlign": "right", "backgroundColor": "#fffbeb"}),
            _celda_var_temp(r["Desviacion_vs_Plan"], MC(r["Desviacion_vs_Plan"])),
        ]))
    return dbc.Table(
        [html.Thead(html.Tr(ths)), html.Tbody(filas)],
        bordered=True, hover=True, size="sm", className="mb-0", style={"fontSize": "0.8rem"}
    )


@callback(
    Output("temp-seg-grafica", "figure"),
    Output("temp-seg-tabla-container", "children"),
    Output("temp-seg-tabla-titulo", "children"),
    Input("global-filter-store", "data"),
    Input("temp-seg-agrupar", "value"),
    Input("radio-temp-gran", "value")
)
def update_seguimiento_entidad(filters, agrupar_por, gran):
    if gran != "Seguimiento":
        return no_update, no_update, no_update
    entity_col, entity_label = _ENTIDAD_SEG[agrupar_por]
    df = _df_seguimiento(filters, agrupar_por)
    titulo = f"Tabla de seguimiento semanal por {entity_label}"
    if df.empty:
        empty_fig = go.Figure()
        empty_fig.update_layout(title="Sin datos para los filtros actuales.", height=430)
        return empty_fig, dbc.Alert("No se encontraron datos para los filtros actuales.", color="warning"), titulo

    fig = _grafica_seguimiento(df, entity_col, entity_label)
    tbl = _tabla_seguimiento_df(df, entity_col, entity_label)
    return fig, _tabla_seguimiento_html(tbl, entity_label), titulo


@callback(
    Output("download-csv-temp-seg", "data"),
    *download_inputs("btn-csv-temp-seg"),
    State("global-filter-store", "data"),
    State("temp-seg-agrupar", "value"),
    prevent_initial_call=True
)
def download_temp_seg_csv(_n_num, _n_conta, filters, agrupar_por):
    contable = modo_descarga()
    if contable is None:
        return no_update
    entity_col, entity_label = _ENTIDAD_SEG[agrupar_por]
    df = _df_seguimiento(filters, agrupar_por)
    if df.empty:
        return no_update
    tbl = _tabla_seguimiento_df(df, entity_col, entity_label)
    return enviar_tabla(tbl, f"seguimiento_{entity_label.lower()}_sem1-{ds.smp}", contable)
