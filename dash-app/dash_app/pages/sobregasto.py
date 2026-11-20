# -*- coding: utf-8 -*-
"""
dash_app/pages/sobregasto.py
Pestaña Alertas: radar de sobregasto por CECO / PDV / División / Cuenta (ranking, tabla con semáforo y variaciones/atípicos).
"""
import dash
from dash import html, dcc, Input, Output, State, callback
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from pathlib import Path

from dash_app.services.duckdb_service import DuckDBService
from dash_app.services.filtros_ui import campo, grid

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
from dash_app.components import charts
from dash_app.services.data_service import DataService
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla
from dash_app.pages import sobregasto_monitoreo


ddb = DuckDBService()
ds = DataService()

# Construir diccionario inverso Cuenta -> Responsable Interno
CTA_A_RESP = {}
if hasattr(ds, "responsables_cta") and ds.responsables_cta:
    for resp, ctas in ds.responsables_cta.items():
        for c in ctas:
            CTA_A_RESP[str(c).strip()] = resp

DIVISIONES_LIST = [
    "Division Norte", "Division Centro", "Division Sur", "Concentrador",
    "Cerrados y Cancelados", "Corporativo", "Expansión", "Fuerza Proactiva", "Otros"
]

def layout():
    return html.Div(
        className="w-100",
        children=[
            # 1. Título y controles de agrupación
            html.Div(
                className="al-top",
                children=[
                    html.Div([
                        html.H5("Alertas de sobregasto", className="al-title"),
                        html.Span("Partidas que gastan más que el Plan y qué tan graves son", className="al-sub"),
                    ]),
                    html.Div(className="al-agrupa", children=[
                        html.Span("Agrupar por", className="al-agrupa-lbl"),
                        html.Div(className="segmented-control al-seg", children=dbc.RadioItems(
                            id="sg-modo-vista",
                            options=[
                                {"label": "CECO", "value": "ceco"},
                                {"label": "PDV", "value": "pdv"},
                                {"label": "División", "value": "division"},
                                {"label": "Cuenta", "value": "cuenta"}
                            ],
                            value="ceco",
                            inline=True
                        )),
                    ]),
                ]
            ),
            # 2. Barra de Filtros Locales Rápidos
            html.Div(
                className="p-3 bg-white border rounded shadow-sm mb-4",
                children=[
                    grid(
                        campo("División", dcc.Dropdown(
                            id="sg-filtro-div",
                            options=[{"label": d, "value": d} for d in DIVISIONES_LIST],
                            multi=True, placeholder="División (todas)")),
                        campo("Responsable interno", dcc.Dropdown(
                            id="sg-filtro-resp",
                            options=[{"label": r, "value": r} for r in (ds.responsables if hasattr(ds, "responsables") else [])],
                            multi=True, placeholder="Responsable interno (todos)")),
                        campo("Descargar", html.Div(className="filtros-descarga", children=[
                            download_buttons("btn-download-sg-csv", color="primary", outline=False,
                                             className="btn-exec-download"),
                            dcc.Download(id="download-sg-csv")])),
                    )
                ]
            ),

            # Variaciones y semanas atípicas (mismo cálculo que Cashi; rag/monitoreo/)
            sobregasto_monitoreo.layout(),

            # 3. Tarjetas KPI Ejecutivas
            dbc.Row(
                id="sg-kpi-cards-row",
                className="g-3 mb-4",
                children=[
                    dbc.Col(width=12, sm=6, lg=3, children=[html.Div(id="sg-kpi-1", className="vt-kpi-card")]),
                    dbc.Col(width=12, sm=6, lg=3, children=[html.Div(id="sg-kpi-2", className="vt-kpi-card")]),
                    dbc.Col(width=12, sm=6, lg=3, children=[html.Div(id="sg-kpi-3", className="vt-kpi-card")]),
                    dbc.Col(width=12, sm=6, lg=3, children=[html.Div(id="sg-kpi-4", className="vt-kpi-card")]),
                ]
            ),

            # 4. Gráfica de ranking de impacto económico (Top 15 en MDP)
            html.Div(
                className="al-card",
                children=[
                    html.Div(
                        className="al-card-head",
                        children=[
                            html.H6("Mayor impacto económico en sobregasto (Real vs Plan 2026)", className="al-card-title"),
                            html.Span("Cifras en $ MDP", className="al-pill")
                        ]
                    ),
                    dcc.Graph(id="sg-chart-top", config={"displayModeBar": False}, style={"height": "380px"})
                ]
            ),

            # 5. Tabla de sobregasto detallada
            html.Div(
                className="al-card al-card-tabla",
                children=[
                    html.Div(
                        className="al-card-head",
                        children=[
                            html.H6("Detalle de desviaciones con semáforo de severidad", className="al-card-title"),
                            html.Span(id="sg-tabla-count-badge", className="al-pill al-pill-dark")
                        ]
                    ),
                    html.Div(id="sg-tabla-container", className="vt-var-wrap table-responsive")
                ]
            )
        ]
    )
@callback(
    Output("sg-kpi-1", "children"),
    Output("sg-kpi-2", "children"),
    Output("sg-kpi-3", "children"),
    Output("sg-kpi-4", "children"),
    Output("sg-chart-top", "figure"),
    Output("sg-tabla-container", "children"),
    Output("sg-tabla-count-badge", "children"),
    Input("global-filter-store", "data"),
    Input("sg-modo-vista", "value"),
    Input("sg-filtro-div", "value"),
    Input("sg-filtro-resp", "value")
)
def update_sobregasto_page(filters, modo, filtro_div, filtro_resp):
    filters = filters or {}
    si = filters.get("si", 1)
    sf = filters.get("sf", ds.smr)
    tm = filters.get("tipo_monto", "Monto Constante")
    modo = modo or "ceco"

    local_filters = _filtros_locales(filters, filtro_div, filtro_resp)

    df_sg = ddb.query_sobregasto(group_by=modo, si=si, sf=sf, filters=local_filters, tipo_monto=tm, top_n=200)

    if df_sg is None or df_sg.empty:
        empty_fig = go.Figure()
        empty_fig.update_layout(
            title="Sin desviaciones de sobregasto registradas para los filtros activos", 
            template="plotly_white",
            height=320
        )
        empty_kpi = [
            html.Div("Sobregasto en Rango", className="vt-kpi-title"),
            html.Div("$0.00 M", className="vt-kpi-value text-success"),
            html.Div("Operación alineada al Plan", className="vt-kpi-sub text-success")
        ]
        return empty_kpi, empty_kpi, empty_kpi, empty_kpi, empty_fig, html.Div("No se detectaron unidades con Real > Plan.", className="p-4 text-center text-muted"), "0 Unidades"

    # 1. Homologación de Nombres Legibles y Responsable
    def resolver_nombre_concepto(row):
        return _etiqueta(modo, row["Concepto_Id"])

    def resolver_responsable(row):
        if modo == "cuenta":
            cta = str(row["Concepto_Id"]).strip()
            return CTA_A_RESP.get(cta, "Sin Asignar")
        elif filtro_resp and filtro_resp != "(Todos)":
            return ", ".join(filtro_resp) if isinstance(filtro_resp, list) else filtro_resp
        else:
            return "Multicuenta / Equipo"

    df_sg["Concepto"] = df_sg.apply(resolver_nombre_concepto, axis=1)
    df_sg["Responsable"] = df_sg.apply(resolver_responsable, axis=1)

    # Convertir a MDP
    df_sg["Real_2025_MDP"] = df_sg["Real_2025"] / 1e6
    df_sg["Plan_2026_MDP"] = df_sg["Plan_2026"] / 1e6
    df_sg["Real_2026_MDP"] = df_sg["Real_2026"] / 1e6
    df_sg["Sobregasto_MDP"] = df_sg["Sobregasto_Abs"] / 1e6

    # 2. Tarjetas KPI
    total_unidades = len(df_sg)
    total_sobregasto_mdp = df_sg["Sobregasto_MDP"].sum()
    max_pct = df_sg["Sobregasto_Pct"].max() if not df_sg["Sobregasto_Pct"].empty else 0
    top_unit = df_sg.iloc[0] if not df_sg.empty else None

    kpi1 = [
        html.Div("Unidades en Sobregasto", className="vt-kpi-title"),
        html.Div(f"{total_unidades:,}", className="vt-kpi-value text-danger"),
        html.Div("Real 2026 > Plan 2026", className="vt-kpi-sub text-danger fw-bold")
    ]
    kpi2 = [
        html.Div("Impacto Económico Total", className="vt-kpi-title"),
        html.Div(f"(${total_sobregasto_mdp:,.2f} M)", className="vt-kpi-value text-danger"),
        html.Div("Desviación neta en P&L", className="vt-kpi-sub text-muted")
    ]
    kpi3 = [
        html.Div("Máxima Desviación %", className="vt-kpi-title"),
        html.Div(f"(+{max_pct:,.1f}%)", className="vt-kpi-value text-danger"),
        html.Div("Mayor exceso porcentual vs Plan", className="vt-kpi-sub text-muted")
    ]
    kpi4 = [
        html.Div("Mayor Exceso Absoluto", className="vt-kpi-title"),
        html.Div(f"(${top_unit['Sobregasto_MDP']:,.2f} M)" if top_unit is not None else "$0 M", className="vt-kpi-value text-dark"),
        html.Div(f"{top_unit['Concepto'][:28]}..." if top_unit is not None else "", className="vt-kpi-sub text-danger fw-semibold")
    ]

    # 3. Gráfica Horizontal de Ranking Top 15 (MDP)
    top15 = df_sg.head(15).iloc[::-1]  # Invertir para que el mayor quede arriba
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=top15["Sobregasto_MDP"],
        y=top15["Concepto"],
        orientation="h",
        marker=dict(color=charts.color_serie("Real_2026"), line=dict(width=0)),
        text=[f"(${v:,.2f} M)" for v in top15["Sobregasto_MDP"]],
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>Sobregasto: <b>(%{x:,.2f} MDP)</b><extra></extra>"
    ))
    fig.update_layout(
        template="plotly_white",
        margin=dict(l=10, r=60, t=10, b=20),
        xaxis_title="Sobregasto Absoluto ($ MDP)",
        yaxis_title=None,
        font=dict(family="Manrope", size=11, color="#1e2430"),
        height=380,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)"
    )
    charts.estilo_basico(fig)

    # 4. Tabla Ejecutiva con Semáforos Visuales
    rows = []
    for _, r in df_sg.head(60).iterrows():
        pct = r["Sobregasto_Pct"]
        monto_sg = r["Sobregasto_MDP"]

        # Regla de semáforo corporativo (punto de color dibujado con CSS):
        if monto_sg >= 1.0 or pct >= 20.0:
            badge = html.Span("Crítico", className="al-sem crit")
        elif monto_sg >= 0.20 or pct >= 5.0:
            badge = html.Span("Moderado", className="al-sem mod")
        else:
            badge = html.Span("Preventivo", className="al-sem prev")
        rows.append(
            html.Tr([
                html.Td(str(r["Concepto"]), className="fw-semibold text-dark text-truncate", style={"maxWidth": "280px"}),
                html.Td(str(r["Division"]), className="text-secondary small"),
                html.Td(str(r["Responsable"]), className="text-muted small"),
                html.Td(f"${r['Real_2025_MDP']:,.2f}", className="text-muted"),
                html.Td(f"${r['Plan_2026_MDP']:,.2f}", className="text-secondary"),
                html.Td(f"${r['Real_2026_MDP']:,.2f}", className="fw-bold text-dark", style={"backgroundColor": "#fffbeb"}),
                html.Td(f"(${monto_sg:,.2f})", className="fw-bold text-danger"),
                html.Td(f"(+{pct:,.1f}%)", className="fw-bold text-danger"),
                html.Td(badge, className="text-center")
            ])
        )

    # Fila de Totales de la selección
    plan_sum = df_sg['Plan_2026_MDP'].sum()
    pct_total = (total_sobregasto_mdp / plan_sum * 100) if plan_sum else 0
    rows.append(
        html.Tr([
            html.Td("TOTAL SELECCIÓN", className="fw-bold text-dark"),
            html.Td("—", className="text-muted"),
            html.Td("—", className="text-muted"),
            html.Td(f"${df_sg['Real_2025_MDP'].sum():,.2f}", className="fw-bold text-dark"),
            html.Td(f"${plan_sum:,.2f}", className="fw-bold text-dark"),
            html.Td(f"${df_sg['Real_2026_MDP'].sum():,.2f}", className="fw-bold text-dark", style={"backgroundColor": "#fffbeb"}),
            html.Td(f"(${total_sobregasto_mdp:,.2f})", className="fw-bold text-danger"),
            html.Td(f"(+{pct_total:,.1f}%)", className="fw-bold text-danger"),
            html.Td("—", className="text-center")
        ], style={"backgroundColor": "#eef0f3", "borderTop": "2px solid #b8bfc8"})
    )

    table_html = html.Table(
        className="table table-hover align-middle mb-0 vt-var",
        children=[
            html.Thead(
                html.Tr([
                    html.Th("Concepto / Código"),
                    html.Th("División"),
                    html.Th("Responsable"),
                    html.Th("Real 2025 ($M)"),
                    html.Th("Plan 2026 ($M)"),
                    html.Th("Real 2026 ($M)"),
                    html.Th("Sobregasto ($M)"),
                    html.Th("Desv %"),
                    html.Th("Semáforo", className="text-center")
                ])
            ),
            html.Tbody(rows)
        ]
    )

    return kpi1, kpi2, kpi3, kpi4, fig, table_html, f"{total_unidades} Unidades Detectadas"

@callback(
    Output("download-sg-csv", "data"),
    *download_inputs("btn-download-sg-csv"),
    State("global-filter-store", "data"),
    State("sg-modo-vista", "value"),
    State("sg-filtro-div", "value"),
    State("sg-filtro-resp", "value"),
    prevent_initial_call=True
)
def download_sg_csv(_n_num, _n_conta, filters, modo, filtro_div, filtro_resp):
    contable = modo_descarga()
    if contable is None:
        return dash.no_update
    filters = filters or {}
    si = filters.get("si", 1)
    sf = filters.get("sf", ds.smr)
    tm = filters.get("tipo_monto", "Monto Constante")
    
    local_filters = _filtros_locales(filters, filtro_div, filtro_resp)

    df_sg = ddb.query_sobregasto(group_by=modo, si=si, sf=sf, filters=local_filters, tipo_monto=tm, top_n=2000)
    return enviar_tabla(df_sg, f"alertas_sobregasto_{modo}_sem{si}-{sf}", contable)


def _filtros_locales(filters, filtro_div, filtro_resp):
    """Filtros globales + barra local (División / Responsable). Compartido por
    la página y la descarga para que filtren igual."""
    local_filters = dict(filters or {})
    if filtro_div:
        if isinstance(filtro_div, list):
            if len(filtro_div) > 0:
                local_filters["filtro_div_local"] = filtro_div
        elif filtro_div != "(Todas)":
            local_filters["filtro_div_local"] = [filtro_div]
    if filtro_resp:
        resps = filtro_resp if isinstance(filtro_resp, list) else ([] if filtro_resp == "(Todos)" else [filtro_resp])
        ctas_del_resp = []
        for r in resps:
            ctas_del_resp.extend(ds.responsables_cta.get(r, []))
        if ctas_del_resp:
            local_filters["cat_Cuentas"] = list(set(ctas_del_resp))
    return local_filters


def _etiqueta(modo, concepto_id):
    """Nombre legible de la unidad según el modo de agrupación."""
    if modo == "pdv":
        try:
            eco = int(float(concepto_id))
        except (TypeError, ValueError):
            return "Sin PDV asignado"
        if eco == 0:
            return "Sin PDV asignado"
        nombre = ds.pdv_nombres.get(str(eco), "")
        return f"PDV {eco} · {nombre}" if nombre else f"PDV {eco}"
    cid = str(concepto_id).strip()
    if modo == "ceco":
        nombre = ds.ceco_nombres.get(cid, "")
        return f"{cid} · {nombre}" if nombre else f"CECO {cid}"
    return cid
