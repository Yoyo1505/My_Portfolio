# -*- coding: utf-8 -*-
"""
dash_app/pages/resumen.py
Pestaña Resumen — Paridad estricta y diseño limpio tipo Streamlit (Sin emojis, sin Treemap, sin colores estrambóticos).
- KPIs Limpios en 4 bloques independientes.
- Tabla Desglose por Grupo de Cuentas (NO por división).
- Gráfica Agrupador Acumulado (División) con Eje X = Semanas (1 a 53): Barras Real 2026 + Línea Punteada Plan 2026.
- 4 Tablas de Variaciones YTD del Bridge Semanal (Ahorros/Sobregastos vs AA y vs Plan).
- Gastos Semanales.
"""
from dash import html, dcc, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import pandas as pd
import numpy as np
import unicodedata
from pathlib import Path
from dash_app.services.cache_ligera import memo
from dash_app.services.data_service import DataService, M, MC, _normalizar_orden
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla
from dash_app.components import charts

ds = DataService()

# ------------------------------------------------------------------
# Homologación Concepto (Bridge Excel, texto libre) -> cat_Cuentas /
# cat_Grupo_de_Cuentas, y filtro por Grupo/Cuenta activo — paridad con
# _normalizar_texto()/_palabras_clave()/_mejor_match_difuso()/
# _homologar_concepto_bridge()/_filtrar_bridge_bloque() del original
# (app.py:1104-1165, 3757-3786). El original filtra el Bridge SOLO por
# Grupo de Cuentas/Cuenta (el resto de filtros del sidebar no tiene con
# qué cruzar en un Excel que solo trae "Concepto" en texto libre).
_STOPWORDS_MATCH_BRIDGE = {"de", "del", "la", "el", "los", "las", "y", "por", "en", "a", "para", "con"}

def _normalizar_texto_bridge(s) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return s.strip().lower()

def _palabras_clave_bridge(s: str) -> frozenset:
    txt = _normalizar_texto_bridge(s).replace(".", " ").replace("/", " ")
    return frozenset(w for w in txt.split() if w and w not in _STOPWORDS_MATCH_BRIDGE)

def _mejor_match_difuso_bridge(palabras: frozenset, candidatos: dict, umbral: float = 0.6):
    mejor, mejor_score = None, 0.0
    for nombre, pal in candidatos.items():
        if not pal:
            continue
        inter = palabras & pal
        if not inter:
            continue
        score = len(inter) / max(len(palabras), len(pal))
        if score > mejor_score:
            mejor, mejor_score = nombre, score
    return mejor if mejor_score >= umbral else None

# Índices de homologación construidos UNA vez (cta_a_gpo de ds ya viene de
# cuentas.parquet, mismo criterio que el original) — no en cada callback.
_CTA_A_GPO_BRIDGE = ds.cta_a_gpo
_CTA_NORM_A_REAL_BRIDGE = {_normalizar_texto_bridge(c): c for c in _CTA_A_GPO_BRIDGE}
_GPOS_UNICOS_BRIDGE = sorted(set(_CTA_A_GPO_BRIDGE.values()))
_CTA_PALABRAS_BRIDGE = {c: _palabras_clave_bridge(c) for c in _CTA_A_GPO_BRIDGE}
_GPO_PALABRAS_BRIDGE = {g: _palabras_clave_bridge(g) for g in _GPOS_UNICOS_BRIDGE}

def _homologar_concepto_bridge(concepto: str):
    exacto = _CTA_NORM_A_REAL_BRIDGE.get(_normalizar_texto_bridge(concepto))
    if exacto:
        return exacto, _CTA_A_GPO_BRIDGE.get(exacto)
    palabras = _palabras_clave_bridge(concepto)
    if not palabras:
        return None, None
    cta = _mejor_match_difuso_bridge(palabras, _CTA_PALABRAS_BRIDGE)
    if cta:
        return cta, _CTA_A_GPO_BRIDGE.get(cta)
    gpo = _mejor_match_difuso_bridge(palabras, _GPO_PALABRAS_BRIDGE)
    if gpo:
        return None, gpo
    return None, None

def _filtrar_bridge_bloque(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    ctas_sel = (filters or {}).get("fg_cta")
    gpos_sel = (filters or {}).get("fg_gpo")
    if not ctas_sel and not gpos_sel:
        return df
    ctas_sel_norm = {_normalizar_texto_bridge(c) for c in ctas_sel} if ctas_sel else None
    gpos_sel_norm = {_normalizar_texto_bridge(g) for g in gpos_sel} if gpos_sel else None

    def _pasa(concepto) -> bool:
        cta_real, gpo_real = _homologar_concepto_bridge(concepto)
        if ctas_sel_norm is not None:
            return cta_real is not None and _normalizar_texto_bridge(cta_real) in ctas_sel_norm
        grupo = _CTA_A_GPO_BRIDGE.get(cta_real) if cta_real else gpo_real
        return grupo is not None and _normalizar_texto_bridge(grupo) in gpos_sel_norm

    return df[df["Concepto"].apply(_pasa)].reset_index(drop=True)

# Formato del Bridge — paridad con _mc_mdp()/PC() del original (app.py:3830,
# 1833): el Excel YA trae los montos en MDP (no en pesos crudos), por eso
# NO se divide entre 1e6 aquí a diferencia de MC()/M() del resto del
# dashboard — dividir de nuevo mostraría todo como ~0.0 (bug ya documentado
# en la BITACORA del original, 2026-09-04, que no debe repetirse aquí).
def _mc_mdp_bridge(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    s = f"{abs(v):,.1f}"
    # Bridge: Monto > 0 = ahorro (sin paréntesis), < 0 = sobregasto (paréntesis). 2026-11-10: igual que Spend Legacy.
    return f"({s})" if v < 0 else s

def _pc_bridge(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"({abs(v):,.1f}%)" if v < 0 else f"{abs(v):,.1f}%"

def _cargar_bloques_bridge(filters):
    try:
        from dash_app.services.gastos_semanales import leer_gastos_semanales_todos
        bloques = leer_gastos_semanales_todos() or {}
        return {k: _filtrar_bridge_bloque(v, filters) for k, v in bloques.items()}
    except Exception:
        return {}

def _pos_neg(bloques, clave):
    tabla = bloques.get(clave)
    if tabla is None or tabla.empty:
        return pd.DataFrame(), pd.DataFrame()
    tabla = tabla.rename(columns={"Concepto": "Cuenta", "Comentarios": "Comentario"})
    t_pos = tabla[tabla["Monto"] > 0].copy()      # ahorro (Bridge: Monto > 0)
    t_pos = t_pos.reindex(t_pos["Monto"].abs().sort_values(ascending=False).index)
    t_neg = tabla[tabla["Monto"] < 0].copy()      # sobregasto
    t_neg = t_neg.reindex(t_neg["Monto"].abs().sort_values(ascending=False).index)
    return t_pos, t_neg

def layout():
    return html.Div(
        children=[
            # 1. Fila de KPIs Limpios (Estilo Streamlit / Steve Jobs Minimalist)
            html.Div(
                children=[
                    html.H5("Gasto Acumulado", style={"fontWeight": "700", "color": "#0f172a", "marginBottom": "12px"}),
                    html.Div(id="resumen-kpis-container", style={"marginBottom": "24px"}),
                ]
            ),

            # 2. Tabla Desglose Oficial por Grupo de Cuentas (Directamente bajo los KPIs)
            html.Div(
                className="vt-kpi-card",
                style={"marginBottom": "24px"},
                children=[
                    html.H6("Desglose por Grupo de Cuentas (Orden Presentacion)", style={"fontWeight": "700", "color": "#0f172a", "marginBottom": "12px"}),
                    html.Div(id="resumen-tabla-container"),
                    html.Div(download_buttons("btn-csv-grupo-cuentas"), style={"marginTop": "12px"}),
                    dcc.Download(id="resumen-download-csv-grupo"),
                ]
            ),

            # 3. Gráficas acumuladas por Grupo de Cuentas y por División — paridad
            # con el original (barras() en app.py): NO es una serie de 53 semanas
            # (eso vive en la pestaña Temporalidad), son 2 gráficas de barras por
            # categoría (máx 14 barras c/u), Real 2025/2026 como barras + Plan
            # (y Forecast en la de Grupo) como línea punteada.
            html.Div(
                className="vt-kpi-card",
                style={"marginBottom": "24px"},
                children=[
                    dbc.Row(
                        className="g-4",
                        children=[
                            dbc.Col(width=12, lg=6, children=[
                                html.H6("Grupo de Cuentas acumulado", style={"fontWeight": "700", "color": "#0f172a", "marginBottom": "2px"}),
                                html.Div("Clic en una barra para filtrar todo el tablero por ese grupo; otro clic lo quita.",
                                         style={"fontSize": "0.75rem", "color": "#64748b", "marginBottom": "8px"}),
                                dcc.Graph(id="resumen-grafica-grupo", config=charts.CONFIG, style=charts.ESTILO_GRAFICA)
                            ]),
                            dbc.Col(width=12, lg=6, children=[
                                html.H6("Agrupador acumulado (División)", style={"fontWeight": "700", "color": "#0f172a", "marginBottom": "2px"}),
                                html.Div("Pasa el cursor para ver las cifras; clic en la leyenda oculta o muestra una serie.",
                                         style={"fontSize": "0.75rem", "color": "#64748b", "marginBottom": "8px"}),
                                dcc.Graph(id="resumen-grafica-division", config=charts.CONFIG, style=charts.ESTILO_GRAFICA)
                            ]),
                        ]
                    )
                ]
            ),

            # 4. Bloque de Variaciones YTD del Bridge Semanal (4 Tablas: Positivas/Negativas vs AA y vs Plan)
            html.Div(
                className="vt-kpi-card",
                style={"marginBottom": "24px"},
                children=[
                    html.H5("Variaciones y Causas Raiz YTD", style={"fontWeight": "700", "color": "#0f172a", "marginBottom": "4px"}),
                    html.Div(id="resumen-variaciones-container")
                ]
            ),

            # 5. Gastos Semanales
            html.Div(
                className="vt-kpi-card",
                style={"marginBottom": "24px"},
                children=[
                    html.H5(f"Gastos Semanales (Semana {ds.smr})", style={"fontWeight": "700", "color": "#0f172a", "marginBottom": "4px"}),
                    html.Div(id="resumen-semanales-container")
                ]
            ),

            # 6. Descarga del Resumen en PDF
            html.Div(id="resumen-pdf-container"),
            dcc.Download(id="resumen-download-pdf"),
        ]
    )

@callback(
    Output("resumen-kpis-container", "children"),
    Output("resumen-tabla-container", "children"),
    Output("resumen-grafica-grupo", "figure"),
    Output("resumen-grafica-division", "figure"),
    Output("resumen-variaciones-container", "children"),
    Output("resumen-semanales-container", "children"),
    Output("resumen-pdf-container", "children"),
    Input("global-filter-store", "data")
)
@memo(max_entradas=24)      # armar tablas y gráficas costaba 0.4 a 0.6 s en cada visita aun con consultas en caché
def update_resumen(filters):
    kpis = ds.compute_summary_kpis(filters)
    df_gpo = ds.compute_by_dimension("grupo", filters)
    df_div = ds.compute_by_dimension("division", filters)

    # 1. KPIs — paridad estructural con kpis_gasto_html()/_kpi()/_senal() del
    # original (app.py:3606-3624): tarjeta = título uppercase + valor grande
    # (SIN mezclar el delta en el mismo número) + una señal aparte debajo
    # (flecha + monto + etiqueta·%). Solo la 1a tarjeta (Real 2026, la cifra
    # que manda) lleva el acento rojo de marca vía CSS (.vt-kpi-card:first-
    # child::before) y no lleva señal propia — es la referencia, no se
    # compara contra sí misma.
    def _chip(delta, pct, texto=None):
        if texto is not None:
            return html.Span(texto, className="cp z")
        if delta is None or pd.isna(delta) or delta == 0:
            return html.Span("=", className="cp z")
        cls, fl = ("up", "+") if delta > 0 else ("dn", "−")
        p = "" if pct is None or pd.isna(pct) else f"{abs(pct):,.1f} %"
        return html.Span(f"{fl}{p}", className=f"cp {cls}")

    def _tarjeta_kpi(titulo, valor_str, senal=None, chip=None, avance=None):
        pie = [senal if senal is not None else html.Div(f"Semana {ds.smr}", className="vt-kpi-sub vt-sig-cero")]
        hijos = [
            html.Div(className="kt", children=[html.Div(titulo, className="vt-kpi-title"), chip or html.Span()]),
            html.Div(valor_str, className="vt-kpi-value"),
            html.Div(className="kf", children=pie),
        ]
        if avance is not None and not pd.isna(avance):
            hijos.append(html.Div(className="prog", children=[html.I(style={"width": f"{max(0, min(100, avance)):.1f}%"}), html.B(style={"left": "100%"})]))
        return dbc.Col(html.Div(className="vt-kpi-card vt-kpi-rich", children=hijos), width=3)

    def _senal(delta, pct, etiqueta):
        if delta is None or pd.isna(delta) or delta == 0:
            cls, flecha = "vt-sig-cero", "="
        elif delta > 0:
            cls, flecha = "vt-sig-alza", "+"
        else:
            cls, flecha = "vt-sig-baja", "−"
        p = "—" if pct is None or pd.isna(pct) else f"{abs(pct):,.1f}%"
        monto_txt = M(abs(delta)) if delta is not None and not pd.isna(delta) else "—"
        return html.Div(
            className=f"vt-kpi-sub {cls}",
            children=[
                html.Span(flecha, className="vt-kpi-arrow"),
                f" {monto_txt} ",
                html.Span([etiqueta, " · ", p], className="vt-kpi-et"),
            ]
        )

    def _av(num, den):
        return (num / den * 100) if den else None

    kpis_row = dbc.Row(
        children=[
            _tarjeta_kpi("Real 2026", M(kpis["Real_2026"]), chip=_chip(None, None, f"Sem {ds.smr}")),
            _tarjeta_kpi("Plan 2026", M(kpis["Plan_2026"]),
                         _senal(kpis["Vs_Plan_Abs"], kpis["Vs_Plan_Pct"], "Real 2026 vs Plan"),
                         chip=_chip(kpis["Vs_Plan_Abs"], kpis["Vs_Plan_Pct"]),
                         avance=_av(kpis["Real_2026"], kpis["Plan_2026"])),
            _tarjeta_kpi("Real 2025", M(kpis["Real_2025"]),
                         _senal(kpis["Vs_AA_Abs"], kpis["Vs_AA_Pct"], "Real 2026 vs AA"),
                         chip=_chip(kpis["Vs_AA_Abs"], kpis["Vs_AA_Pct"])),
            _tarjeta_kpi("Forecast Finanzas", M(kpis["Forecast_Cierre"]),
                         _senal(kpis["Vs_Forecast_Abs"], kpis["Vs_Forecast_Pct"], "Real 2026 vs Forecast Finanzas"),
                         chip=_chip(kpis["Vs_Forecast_Abs"], kpis["Vs_Forecast_Pct"]),
                         avance=_av(kpis["Real_2026"], kpis["Forecast_Cierre"])),
        ],
        className="g-3"
    )

    # 2. Árbol Desglose Oficial por Grupo de Cuentas -> Cuentas (NO por
    # División) — paridad con arbol_perezoso("cuentas",
    # ("cat_Grupo_de_Cuentas","cat_Cuentas"), ..., inc_fc=True) del original:
    # antes era una tabla plana SIN la rama de Cuenta (bug de migración).
    from dash_app.components.tree_view import create_tree_view
    totals_gpo = {
        "Real_2025": df_gpo["Real_2025"].sum(),
        "Plan_2026": df_gpo["Plan_2026"].sum(),
        "Real_2026": df_gpo["Real_2026"].sum(),
        "Forecast": df_gpo["Forecast"].sum(),
    }
    table_gpo = create_tree_view(
        "resumen-gc", df_gpo, total_dict=totals_gpo,
        cols=["cat_Grupo_de_Cuentas", "cat_Cuentas"], inc_fc=True
    )

    # 3. Gráficas acumuladas por categoría (Plotly, estilo común en components/charts.py):
    # Real 2025/2026 como barras, Plan/Forecast como línea punteada, máx 14 categorías.
    fig_grupo = charts.barras(df_gpo, "Concepto", ["Real_2025", "Plan_2026", "Real_2026", "Forecast"])
    fig_division = charts.barras(df_div, "Concepto", ["Real_2025", "Plan_2026", "Real_2026"])

    # 4. Tablas de Variaciones YTD — MISMA fuente que las semanales (Bridge
    # Excel vía leer_gastos_semanales_todos(), claves acumulado_plan/
    # acumulado_aa) — antes usaba get_variaciones_riesgos_oportunidades(), una
    # fuente DISTINTA (riesgos.json) que no es la que muestra el original.

    def _render_tabla_var(df_var, es_positivo):
        if df_var.empty:
            return html.Div("Sin variaciones registradas.", style={"color": "#64748b", "fontSize": "0.83rem", "fontStyle": "italic"})

        bg_badge = "#f0fdf4" if es_positivo else "#fef2f2"
        fg_badge = "#166534" if es_positivo else "#991b1b"

        rows = []
        for _, r in df_var.iterrows():
            rows.append(
                html.Tr([
                    html.Td(str(r.get("Cuenta", "")), style={"fontWeight": "600", "color": fg_badge, "backgroundColor": bg_badge}),
                    html.Td(_mc_mdp_bridge(r.get("Monto")), style={"fontWeight": "700", "color": fg_badge, "backgroundColor": bg_badge}),
                    html.Td(_pc_bridge(r.get("%")), style={"fontWeight": "700", "color": fg_badge, "backgroundColor": bg_badge}),
                    html.Td(str(r.get("Comentario", "")), style={"fontSize": "0.82rem", "lineHeight": "1.35"})
                ])
            )

        return html.Div(
            className="vt-var-wrap",
            children=[
                html.Table(
                    className="vt-var",
                    children=[
                        html.Thead(
                            html.Tr([
                                html.Th("Cuenta", style={"width": "24%"}),
                                html.Th("Monto", style={"width": "12%"}),
                                html.Th("%", style={"width": "10%"}),
                                html.Th("Comentario / Causa Raiz", style={"width": "54%"}),
                            ])
                        ),
                        html.Tbody(rows)
                    ]
                )
            ]
        )

    def _bloque_variaciones(titulo_sec, pos_df, neg_df):
        return html.Div(
            style={"marginBottom": "24px"},
            children=[
                html.H6(titulo_sec, style={"fontWeight": "700", "color": "#0f172a", "borderBottom": "2px solid #e2e8f0", "paddingBottom": "6px", "marginBottom": "12px"}),
                dbc.Row([
                    dbc.Col([
                        html.Div("Variaciones positivas (ahorro)", style={"fontWeight": "700", "color": "#166534", "fontSize": "0.85rem", "marginBottom": "8px"}),
                        _render_tabla_var(pos_df, True)
                    ], width=6),
                    dbc.Col([
                        html.Div("Variaciones negativas (sobregasto)", style={"fontWeight": "700", "color": "#991b1b", "fontSize": "0.85rem", "marginBottom": "8px"}),
                        _render_tabla_var(neg_df, False)
                    ], width=6)
                ])
            ]
        )

    # Las 4 tablas vienen del MISMO origen — Bridge Excel semanal vía
    # gastos_semanales.leer_gastos_semanales_todos() (paridad con
    # tablas_variaciones()/gastos_semanales_seccion() del original: desde
    # 2026-09-07 el análisis automático de causas raíz se reemplazó por
    # estas 4 tablas curadas a mano por Planeación, en este orden fijo:
    # Semanal vs Plan, Semanal vs AA, Acumulado YTD vs Plan, Acumulado YTD
    # vs AA. Antes las 2 últimas usaban get_variaciones_riesgos_oportunidades()
    # (fuente distinta, riesgos.json) — no es la que muestra el original).
    # _cargar_bloques_bridge()/_pos_neg() viven a nivel de módulo (no aquí
    # adentro) para que el callback de descarga del PDF use EXACTAMENTE los
    # mismos datos ya filtrados/formateados que se ven en pantalla.
    _bloques_bridge = _cargar_bloques_bridge(filters)
    sem_plan_pos, sem_plan_neg = _pos_neg(_bloques_bridge, "semanal_plan")
    sem_aa_pos, sem_aa_neg = _pos_neg(_bloques_bridge, "semanal_aa")
    ytd_plan_pos, ytd_plan_neg = _pos_neg(_bloques_bridge, "acumulado_plan")
    ytd_aa_pos, ytd_aa_neg = _pos_neg(_bloques_bridge, "acumulado_aa")

    variaciones_block = html.Div([
        _bloque_variaciones(f"Variaciones Semanales vs Plan (Sem {ds.smr})", sem_plan_pos, sem_plan_neg),
        _bloque_variaciones(f"Variaciones Semanales vs Año Anterior (Sem {ds.smr})", sem_aa_pos, sem_aa_neg),
        _bloque_variaciones(f"Variaciones Acumuladas (YTD) vs Plan (Sem {ds.smr})", ytd_plan_pos, ytd_plan_neg),
        _bloque_variaciones(f"Variaciones Acumuladas (YTD) vs Año Anterior (Sem {ds.smr})", ytd_aa_pos, ytd_aa_neg)
    ])

    # 5. Gastos Semanales
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"

    sem_act = ds.smr
    f_sem = dict(filters or {})
    f_sem.update({"si": sem_act, "sf": sem_act})      # solo la semana de corte
    grouped_sem = ds.agg("cuentas", ["cat_Cuentas"], f_sem, tm, hibrido=False).rename(
        columns={"Real_2026": "R26", "Plan_2026": "P26"})

    if len(grouped_sem) > 0:
        df_sem = grouped_sem
        df_sem["Vs_Plan"] = df_sem["R26"] - df_sem["P26"]
        df_sem["Vs_Plan_Pct"] = (df_sem["Vs_Plan"] / df_sem["P26"].abs() * 100).fillna(0.0)
        df_sem = df_sem.sort_values("R26", ascending=False).head(10)

        sem_rows = []
        for _, r in df_sem.iterrows():
            es_pos = r["Vs_Plan"] < 0
            est = "sem-fav" if es_pos else "sem-unf"
            sem_rows.append(
                html.Tr(className=est, children=[
                    html.Td(str(r["cat_Cuentas"]), className="sem-cuenta"),
                    html.Td(MC(r["R26"]), className="vt-num-gasto"),
                    html.Td(MC(r["P26"]), className="vt-num-gasto"),
                    html.Td(MC(r["Vs_Plan"]), className="sem-vs"),
                    html.Td(html.Span(f"{r['Vs_Plan_Pct']:+.1f}%", className="sem-chip")),
                ])
            )

        semanales_table = html.Div(
            className="vt-var-wrap",
            children=[
                html.Table(
                    className="vt-var",
                    children=[
                        html.Thead(
                            html.Tr([
                                html.Th("Cuenta", style={"width": "35%"}),
                                html.Th("Real 2026", style={"width": "15%"}),
                                html.Th("Plan 2026", style={"width": "15%"}),
                                html.Th("vs Plan", style={"width": "15%"}),
                                html.Th("vs Plan %", style={"width": "20%"}),
                            ])
                        ),
                        html.Tbody(sem_rows)
                    ]
                )
            ]
        )
    else:
        semanales_table = html.Div("Sin datos para la semana de corte.", style={"color": "#64748b", "fontStyle": "italic"})

    pdf_section = html.Div([
        html.Hr(),
        html.H5("Descargar el Resumen", style={"marginTop": "24px"}),
        html.P("Genera y descarga un PDF con KPIs, desglose por Grupo de Cuentas/Cuenta y las 4 tablas de Variaciones.", style={"fontSize": "0.85rem", "color": "#64748b"}),
        dbc.Button("Preparar Resumen para PDF", id="btn-pdf-resumen", color="primary", size="sm"),
        dbc.Spinner(html.Div(id="resumen-pdf-status", style={"fontSize": "0.8rem", "color": "#64748b", "marginTop": "8px"}), size="sm", color="primary"),
        html.H5("Presentaciones PowerPoint", style={"marginTop": "24px"}),
        html.P("Genera las 3 presentaciones de la semana (Físicos, Digitales y Consolidada) y las descarga en un ZIP. Tarda varios minutos.", style={"fontSize": "0.85rem", "color": "#64748b"}),
        dbc.Button("Generar presentaciones (ZIP)", id="btn-ppt-resumen", color="primary", size="sm"),
        dcc.Download(id="resumen-download-ppt"),
        html.Div(id="resumen-ppt-status", style={"fontSize": "0.8rem", "color": "#64748b", "marginTop": "8px"}),
    ])

    return kpis_row, table_gpo, fig_grupo, fig_division, variaciones_block, semanales_table, pdf_section


# ============================================================ PDF del Resumen
# Descarga real en servidor (decisión del usuario 2026-09-17: NO el flujo
# HTML->imprimir del Streamlit original). Reusa exactamente las mismas 4
# tablas de Variaciones ya filtradas/formateadas que se ven en pantalla
# (decisión del usuario: NO el análisis automático viejo que el original
# seguía usando solo dentro del PDF, app.py:4003-4016 — así el PDF nunca
# dice algo distinto a lo que el usuario ve en el tablero).
def _compute_cuentas_pdf(filters):
    """Tabla plana Grupo de Cuentas -> Cuenta para el PDF, mismo orden que
    la tabla en pantalla (orden_gpo_key del grupo, luego |Real_2026| desc
    dentro de cada grupo) — paridad con cta_pdf del original (app.py:4565-4577).
    Incluye %/vs Plan/vs AA/Forecast — mismas columnas que la tabla de Grupo,
    para que el PDF no se vea "recortado" frente a la de pantalla."""
    tm = (filters or {}).get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    grouped = ds.agg("cuentas", ["cat_Grupo_de_Cuentas", "cat_Cuentas"], filters, tm)
    cols_out = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "Real_2025", "Plan_2026", "Real_2026",
                "Pct_r25", "Pct_p26", "Pct_r26", "Vs_Plan_Abs", "Vs_Plan_Pct",
                "Vs_AA_Abs", "Vs_AA_Pct", "Forecast", "Vs_Forecast", "Vs_Forecast_Pct"]
    if grouped.empty:
        return pd.DataFrame(columns=cols_out)
    df = grouped

    tot_r25 = df["Real_2025"].sum()
    tot_p26 = df["Plan_2026"].sum()
    tot_r26 = df["Real_2026"].sum()
    df["Pct_r25"] = (df["Real_2025"] / tot_r25 * 100) if tot_r25 else 0.0
    df["Pct_p26"] = (df["Plan_2026"] / tot_p26 * 100) if tot_p26 else 0.0
    df["Pct_r26"] = (df["Real_2026"] / tot_r26 * 100) if tot_r26 else 0.0
    df["Vs_Plan_Abs"] = df["Real_2026"] - df["Plan_2026"]
    df["Vs_Plan_Pct"] = np.where(df["Plan_2026"] != 0, (df["Vs_Plan_Abs"] / df["Plan_2026"].abs()) * 100, 0.0)
    df["Vs_AA_Abs"] = df["Real_2026"] - df["Real_2025"]
    df["Vs_AA_Pct"] = np.where(df["Real_2025"] != 0, (df["Vs_AA_Abs"] / df["Real_2025"].abs()) * 100, 0.0)

    fc_pl = ds._forecast_cruzado("cuentas", filters, tm, group_col="cat_Cuentas")
    fc_pd = fc_pl if len(fc_pl) else pd.DataFrame(columns=["cat_Cuentas", "Forecast"])
    df = df.merge(fc_pd, on="cat_Cuentas", how="left")
    df["Forecast"] = df["Forecast"].fillna(0.0)
    df["Vs_Forecast"] = df["Real_2026"] - df["Forecast"]
    df["Vs_Forecast_Pct"] = np.where(df["Forecast"] != 0, (df["Vs_Forecast"] / df["Forecast"].abs()) * 100, 0.0)

    # 2026-09-24 (paridad con _ordenar_resultado del Streamlit): Grupo por orden de presentación
    # y Cuentas alfabéticas dentro del Grupo (antes: mayor |Real_2026| primero).
    df["_ord"] = df.apply(
        lambda r: ds.orden_gpo_key(r["cat_Grupo_de_Cuentas"]) + (_normalizar_orden(r["cat_Cuentas"]), -abs(r["Real_2026"])), axis=1
    )
    return df.sort_values("_ord", kind="stable").drop(columns=["_ord"]).reset_index(drop=True)[cols_out]


def _senal_pdf(delta, pct, etiqueta):
    """Misma semántica que _senal() en pantalla (▲ rojo = sobregasto, ▼ verde
    = ahorro, = gris = sin variación) pero como HTML plano para el PDF —
    idéntica clase visual .vt-kpi-sig que usa el HTML de referencia de
    Streamlit (html_resumen_pdf, app.py:3986)."""
    # xhtml2pdf usa Helvetica sin glyphs Unicode (▲/▼ salen en blanco) -- se
    # usan marcadores ASCII en su lugar, solo para el PDF (en pantalla sigue
    # usando ▲/▼ reales, ver _senal() más arriba).
    if delta is None or pd.isna(delta) or delta == 0:
        cls, flecha = "vt-sig-cero", "="
    elif delta > 0:
        cls, flecha = "vt-sig-alza", "^"
    else:
        cls, flecha = "vt-sig-baja", "v"
    p = "—" if pct is None or pd.isna(pct) else f"{abs(pct):,.1f}%"
    monto_txt = MC(abs(delta)) if delta is not None and not pd.isna(delta) else "—"
    return (f"<div class='vt-kpi-sig {cls}'><span class='vt-kpi-arrow'>{flecha}</span> {monto_txt} "
            f"<span class='vt-kpi-et'>{etiqueta} &middot; {p}</span></div>")


def _kpis_html_pdf(kpis):
    tarjetas = [
        ("Real 2026", M(kpis["Real_2026"]), ""),
        ("Plan 2026", M(kpis["Plan_2026"]), _senal_pdf(kpis["Vs_Plan_Abs"], kpis["Vs_Plan_Pct"], "Real 2026 vs Plan")),
        ("Real 2025", M(kpis["Real_2025"]), _senal_pdf(kpis["Vs_AA_Abs"], kpis["Vs_AA_Pct"], "Real 2026 vs AA")),
        ("Forecast Finanzas", M(kpis["Forecast_Cierre"]), _senal_pdf(kpis["Vs_Forecast_Abs"], kpis["Vs_Forecast_Pct"], "Real 2026 vs Forecast")),
    ]
    celdas = [
        f"<div class='kpi{' plan' if i == 1 else ''}'>"
        f"<div class='vt-kpi-tit'>{titulo}</div><div class='vt-kpi-val'>{valor}</div>{senal}</div>"
        for i, (titulo, valor, senal) in enumerate(tarjetas)
    ]
    return f"<div class='kpis'>{''.join(celdas)}</div>"


def _celda_var(delta, texto):
    """Verde si favorable (delta<=0, bajo plan/AA/forecast), rojo oscuro si
    desfavorable (delta>0, sobre plan/AA/forecast) — mismo criterio semántico
    que la tabla Styler del original (T_e7dd7_row*_col7..13)."""
    if delta is None or pd.isna(delta):
        return f"<td style='text-align:right'>{texto}</td>"
    # Color por clase (no inline) para que las filas TOTAL/subtotal lo puedan aclarar en CSS
    cls = "var-up" if delta > 0 else "var-down"
    return f"<td class='{cls}' style='text-align:right;font-weight:600'>{texto}</td>"


def _tabla_html_pdf(df, cols_label):
    """Tabla de Grupo/Cuenta con semaforización: columnas base (Real 2025/
    Plan 2026/Real 2026/Forecast) en rojo marca, Real 2026 con fondo amarillo
    (misma columna "headline" que .vt-r26 en pantalla), vs Plan/vs AA/vs
    Forecast en verde/rojo según favorable/desfavorable. Fila TOTAL al final."""
    ths = "".join(f"<th>{h}</th>" for _, h in cols_label)
    ths += ("<th>Real 2025</th><th>%</th><th>Plan 2026</th><th>%</th>"
            "<th class='r26-head'>Real 2026</th><th>%</th><th>vs Plan</th><th>vs Plan %</th>"
            "<th>vs AA</th><th>vs AA %</th><th>Forecast Finanzas</th><th>vs Forecast</th><th>vs Forecast %</th>")

    def _fila(r, es_total=False):
        tds = "".join(f"<td{' class=\"tot\"' if es_total else ''}>{r[c]}</td>" for c, _ in cols_label)
        cls_base = " class='num tot'" if es_total else " class='num'"
        tds += f"<td{cls_base}>{MC(r['Real_2025'])}</td><td{cls_base}>{r['Pct_r25']:.1f}%</td>"
        tds += f"<td{cls_base}>{MC(r['Plan_2026'])}</td><td{cls_base}>{r['Pct_p26']:.1f}%</td>"
        cls_r26 = " class='num r26 tot'" if es_total else " class='num r26'"
        tds += f"<td{cls_r26}>{MC(r['Real_2026'])}</td><td{cls_r26}>{r['Pct_r26']:.1f}%</td>"
        tds += _celda_var(r["Vs_Plan_Abs"], MC(r["Vs_Plan_Abs"])) + _celda_var(r["Vs_Plan_Abs"], f"{r['Vs_Plan_Pct']:+.1f}%")
        tds += _celda_var(r["Vs_AA_Abs"], MC(r["Vs_AA_Abs"])) + _celda_var(r["Vs_AA_Abs"], f"{r['Vs_AA_Pct']:+.1f}%")
        tds += f"<td{cls_base}>{MC(r['Forecast'])}</td>"
        tds += _celda_var(r["Vs_Forecast"], MC(r["Vs_Forecast"])) + _celda_var(r["Vs_Forecast"], f"{r['Vs_Forecast_Pct']:+.1f}%")
        return f"<tr{' class=\"totrow\"' if es_total else ''}>{tds}</tr>"

    filas = [_fila(r) for _, r in df.iterrows()]
    if not df.empty:
        tot = {
            "Real_2025": df["Real_2025"].sum(), "Plan_2026": df["Plan_2026"].sum(), "Real_2026": df["Real_2026"].sum(),
            "Pct_r25": 100.0, "Pct_p26": 100.0, "Pct_r26": 100.0, "Forecast": df["Forecast"].sum(),
        }
        tot["Vs_Plan_Abs"] = tot["Real_2026"] - tot["Plan_2026"]
        tot["Vs_Plan_Pct"] = (tot["Vs_Plan_Abs"] / abs(tot["Plan_2026"]) * 100) if tot["Plan_2026"] else 0.0
        tot["Vs_AA_Abs"] = tot["Real_2026"] - tot["Real_2025"]
        tot["Vs_AA_Pct"] = (tot["Vs_AA_Abs"] / abs(tot["Real_2025"]) * 100) if tot["Real_2025"] else 0.0
        tot["Vs_Forecast"] = tot["Real_2026"] - tot["Forecast"]
        tot["Vs_Forecast_Pct"] = (tot["Vs_Forecast"] / abs(tot["Forecast"]) * 100) if tot["Forecast"] else 0.0
        tot_row = dict(tot)
        for c, _lbl in cols_label:
            tot_row[c] = "TOTAL" if c == cols_label[0][0] else ""
        filas.append(_fila(tot_row, es_total=True))

    return f"<table class='dat'><thead><tr>{ths}</tr></thead><tbody>{''.join(filas)}</tbody></table>"


def _tabla_var_html_pdf(df_var, es_positivo):
    if df_var.empty:
        return "<p class='nofilt'>Sin variaciones.</p>"
    bg, fg = ("#eaf6ec", "#1e7d43") if es_positivo else ("#fbeeec", "#78281f")
    filas = []
    for _, r in df_var.iterrows():
        filas.append(
            f"<tr style='background-color:{bg}'>"
            f"<td style='color:{fg};font-weight:600'>{r.get('Cuenta', '')}</td>"
            f"<td style='text-align:right;color:{fg};font-weight:600'>{_mc_mdp_bridge(r.get('Monto'))}</td>"
            f"<td style='text-align:right;color:{fg};font-weight:600'>{_pc_bridge(r.get('%'))}</td>"
            f"<td>{r.get('Comentario', '') or '—'}</td></tr>"
        )
    return (f"<table class='var'><thead><tr><th>Cuenta</th><th>Monto (MDP)</th><th>%</th>"
            f"<th>Comentario / Causa Raíz</th></tr></thead><tbody>{''.join(filas)}</tbody></table>")


try:
    # Mismo recorte que el encabezado del dash (components/header.py): solo el isotipo,
    # sin el texto "grupo aurora" de abajo, que a esta altura no se lee.
    import base64 as _b64
    import io as _io
    from PIL import Image as _Img
    _logo = _Img.open(Path(__file__).parent.parent / "assets" / "aurora_logo.png").convert("RGBA")
    _logo = _logo.crop((0, 0, _logo.width, int(_logo.height * 40 / 54)))
    _buf_logo = _io.BytesIO()
    _logo.save(_buf_logo, format="PNG")
    _PDF_LOGO_B64 = _b64.b64encode(_buf_logo.getvalue()).decode("ascii")
except Exception:
    _PDF_LOGO_B64 = ""


from dash_app.services.pdf_style import PDF_CSS as _PDF_CSS


def _generar_html_resumen_pdf(filters):
    kpis = ds.compute_summary_kpis(filters)
    df_gpo = ds.compute_by_dimension("grupo", filters)
    df_cta = _compute_cuentas_pdf(filters)

    kpi_html = _kpis_html_pdf(kpis)
    tab_gpo_html = _tabla_html_pdf(df_gpo, [("Concepto", "Grupo de Cuentas")])
    tab_cta_html = _tabla_html_pdf(
        df_cta.rename(columns={"cat_Grupo_de_Cuentas": "Grupo", "cat_Cuentas": "Cuenta"}),
        [("Grupo", "Grupo de Cuentas"), ("Cuenta", "Cuenta")]
    )

    _bloques_bridge = _cargar_bloques_bridge(filters)
    _secciones_var = (
        ("Variaciones Semanales vs Plan", "semanal_plan"),
        ("Variaciones Semanales vs Año Anterior", "semanal_aa"),
        ("Variaciones Acumuladas (YTD) vs Plan", "acumulado_plan"),
        ("Variaciones Acumuladas (YTD) vs Año Anterior", "acumulado_aa"),
    )
    bloques_var_html = ""
    for titulo, clave in _secciones_var:
        pos_df, neg_df = _pos_neg(_bloques_bridge, clave)
        bloques_var_html += (
            f"<h3>{titulo} (Sem {ds.smr})</h3>"
            f"<p class='sub'>Positivas (ahorro)</p>{_tabla_var_html_pdf(pos_df, True)}"
            f"<p class='sub'>Negativas (sobregasto)</p>{_tabla_var_html_pdf(neg_df, False)}"
        )

    tm = (filters or {}).get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    si = (filters or {}).get("si", 1)
    sf = (filters or {}).get("sf", ds.smr)
    periodo = f"Semanas {si}-{sf} — 2026"
    tipo_monto_txt = "Monto Constante" if tm == "Monto Constante" else "Monto Local"

    filtros_activos = []
    if filters:
        if filters.get("fg_resp"):
            filtros_activos.append(f"Responsable: {len(filters['fg_resp'])} seleccionado(s)")
        if filters.get("fg_gpo"):
            filtros_activos.append(f"Grupo de Cuentas: {len(filters['fg_gpo'])} seleccionado(s)")
        if filters.get("fg_cta"):
            filtros_activos.append(f"Cuenta: {len(filters['fg_cta'])} seleccionado(s)")
        if filters.get("fg_subtipo"):
            filtros_activos.append(f"Subtipo: {len(filters['fg_subtipo'])} seleccionado(s)")
    nofilt_html = (
        f"<div class='nofilt'>Vista filtrada — solo incluye: <b>{' · '.join(filtros_activos)}</b></div>"
        if filtros_activos else
        "<div class='nofilt'>Sin filtros de Responsable/Grupo/Cuenta/Subtipo activos — universo completo.</div>"
    )

    logo_html = f"<img src='data:image/png;base64,{_PDF_LOGO_B64}' alt='Grupo Aurora'>" if _PDF_LOGO_B64 else ""

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><style>{_PDF_CSS}</style></head>
<body>
<div class="enc">
  {logo_html}
  <div class="kicker">GRUPO AURORA &middot; PLANEACIÓN FINANCIERA</div>
  <h1>Gasto Canales Grupo Aurora</h1>
  <div class="sub">Resumen &nbsp;&middot;&nbsp; {periodo} &nbsp;&middot;&nbsp; {tipo_monto_txt} &nbsp;&middot;&nbsp; Cifras en Millones de Pesos (MDP)</div>
</div>
{nofilt_html}
<h2>Gasto</h2>
{kpi_html}
<h2>Detalle por Grupo de Cuentas</h2>
{tab_gpo_html}
<h2 class="pagebreak">Detalle por Cuenta</h2>
{tab_cta_html}
<h2 class="pagebreak">Variaciones y Causas Raíz YTD</h2>
{bloques_var_html}
<div class="pie">Generado el {pd.Timestamp.now():%d/%m/%Y %H:%M} &middot; Dashboard Gasto Canales.</div>
</body></html>"""


@callback(
    Output("resumen-download-pdf", "data"),
    Output("resumen-pdf-status", "children"),
    Input("btn-pdf-resumen", "n_clicks"),
    State("global-filter-store", "data"),
    prevent_initial_call=True,
)
def download_resumen_pdf(n_clicks, filters):
    if not n_clicks:
        return no_update, no_update
    try:
        from dash_app.services.pdf_service import html_a_pdf
        pdf_bytes = html_a_pdf(_generar_html_resumen_pdf(filters))
        return dcc.send_bytes(pdf_bytes, "resumen_gasto_canal_fisico.pdf"), ""
    except Exception as e:
        return no_update, f"Error al generar el PDF: {e}"


def _servicio_presentaciones():
    """Servicio propio de DASH APP: codigo/presentaciones_servicio.py (no depende de Spend Legacy)."""
    import importlib.util
    ruta = Path(__file__).resolve().parents[2] / "codigo" / "presentaciones_servicio.py"
    spec = importlib.util.spec_from_file_location("presentaciones_servicio", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@callback(
    Output("resumen-download-ppt", "data"),
    Output("resumen-ppt-status", "children"),
    Input("btn-ppt-resumen", "n_clicks"),
    prevent_initial_call=True,
)
def download_presentaciones(n_clicks):
    if not n_clicks:
        return no_update, no_update
    try:
        nombre, datos = _servicio_presentaciones().generar_zip()
        return dcc.send_bytes(datos, nombre), ""
    except Exception as e:
        return no_update, f"Error al generar las presentaciones: {e}"


# ---------------------------------------------- Excel de Grupo de Cuentas
# Paridad con el botón de descarga del original (app.py `descargar(gc_, ...)`, llamado en la
# sección Resumen con gc_ = compute("grupo_cuentas", ...) SIN excluir_padres — mismos datos
# que ya se ven en la tabla de pantalla, a diferencia del PDF que sí recalcula
# excluir_padres=cat_Agrupa1). 2026-09-24: Excel con 2 botones (números completos / formato
# contable), ya no CSV.
@callback(
    Output("resumen-download-csv-grupo", "data"),
    *download_inputs("btn-csv-grupo-cuentas"),
    State("global-filter-store", "data"),
    prevent_initial_call=True,
)
def download_grupo_cuentas_csv(_n_num, _n_conta, filters):
    contable = modo_descarga()
    if contable is None:
        return no_update
    # Usar _compute_cuentas_pdf que trae dos-nivel: Grupo de Cuentas -> Cuenta
    df_cta = _compute_cuentas_pdf(filters)
    si = (filters or {}).get("si", 1)
    sf = (filters or {}).get("sf", ds.smr)
    # Mantener el orden de columnas: Grupo, Cuenta, medidas
    cols_orden = ["cat_Grupo_de_Cuentas", "cat_Cuentas"] + [c for c in df_cta.columns
                  if c not in ("cat_Grupo_de_Cuentas", "cat_Cuentas", "_ord")]
    df_exp = df_cta[cols_orden] if all(c in df_cta.columns for c in cols_orden[:2]) else df_cta
    return enviar_tabla(df_exp, f"grupo_cuentas_sem{si}-{sf}", contable)


# ============================================================ Interactividad (Plotly)
@callback(
    Output("sidebar-fg-gpo", "value", allow_duplicate=True),
    Input("resumen-grafica-grupo", "clickData"),
    State("sidebar-fg-gpo", "value"),
    prevent_initial_call=True,
)
def filtrar_por_grupo_con_clic(click, actuales):
    """Clic en una barra de la gráfica de Grupo de Cuentas = filtro global por ese grupo (otro clic lo quita)."""
    if not click or not click.get("points"):
        return no_update
    grupo = click["points"][0].get("x")
    if not grupo:
        return no_update
    return [] if list(actuales or []) == [grupo] else [grupo]
