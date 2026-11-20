# -*- coding: utf-8 -*-
"""
dash_app/pages/sobregasto_monitoreo.py
Bloque "Variaciones y semanas atípicas" de la pestaña Alertas de Sobregasto.

Mismo cálculo que Cashi (rag/monitoreo/analisis.py): la estadística solo decide qué es atípico; aquí se
muestran las semanas, los CECOs (con nombre, agrupador y división) y las cuentas que explican el movimiento,
separados por canal (Físico / Digital). Filtros con el diseño global (filtros_ui.campo / grid).
"""
import dash
from dash import html, dcc, dash_table, Input, Output, State, callback
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd

from dash_app.components import charts
from dash_app.services.data_service import DataService
from dash_app.services.cache_ligera import memo
from dash_app.services.duckdb_service import DuckDBService
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla
from dash_app.services.filtros_ui import campo, grid
from rag.monitoreo import config as MC
from rag.monitoreo.analisis import analizar
from rag.monitoreo.atipicos import texto_semanas
from rag.monitoreo.features import NOMBRES

ddb = DuckDBService()
ds = DataService()

CANALES = {"Fisico": ("", "Físico"), "Digital": ("", "Digital")}
_COLS_FCST = {"cat_Grupo_de_Cuentas", "cat_Cuentas"}
ROJO, GRIS, TINTA = "#a93226", "#8a93a0", "#1e2430"


def _mdp(v):
    return f"{'−' if v < 0 else ''}${abs(v) / 1e6:,.2f} M"


def _smdp(v):
    return f"{'+' if v >= 0 else '−'}${abs(v) / 1e6:,.2f} M"


def layout():
    semanas = [{"label": f"Sem {i}", "value": i} for i in range(1, ds.smr + 1)]
    return dbc.Card(
        className="mb-4 shadow-sm border-0",
        style={"borderRadius": "12px", "overflow": "hidden"},
        children=[
            dbc.CardHeader(
                className="bg-white border-bottom py-3 d-flex align-items-center justify-content-between",
                children=[html.H6("Variaciones y semanas atípicas", className="fw-bold text-dark mb-0"),
                          html.Span(id="mon-badge")]),
            html.Div(className="px-3 pt-3", children=grid(
                campo("Canal", dcc.Dropdown(
                    id="mon-canal", clearable=True, placeholder="Según el filtro global",
                    options=[{"label": "Físico", "value": "Fisico"}, {"label": "Digital", "value": "Digital"},
                             {"label": "Físico y Digital (separados)", "value": "Ambos"}])),
                campo("Desde la semana", dcc.Dropdown(id="mon-desde", options=semanas, value=1, clearable=False)),
                campo("Hasta la semana", dcc.Dropdown(id="mon-hasta", options=semanas, value=ds.smr, clearable=False)),
                campo("Descargar", html.Div(className="filtros-descarga", children=[
                    download_buttons("btn-download-mon", color="primary", outline=False, className="btn-exec-download"),
                    dcc.Download(id="download-mon")])),
            )),
            dbc.CardBody(className="p-3", children=[dcc.Loading(html.Div(id="mon-contenido"))]),
        ])


# ---------------------------------------------------------------------------------------------- cálculo
def _canales(filters, canal_sel):
    pedido = canal_sel or (filters or {}).get("canal") or "Fisico"
    hay_dig = "consolidado_dig" in ddb._dig_db
    if pedido == "Ambos" and hay_dig:
        return ["Fisico", "Digital"]
    if pedido == "Digital" and hay_dig:
        return ["Digital"]
    return ["Fisico"]


@memo(max_entradas=24)      # caché por combinación de filtros; caduca a la hora y se barre sola (cache_ligera)
def _analizar_canal(canal, filters, filtro_div, filtro_resp, desde, hasta):
    return _calcular_canal(canal, filters, filtro_div, filtro_resp, desde, hasta)


def _calcular_canal(canal, filters, filtro_div, filtro_resp, desde, hasta):
    from dash_app.pages.sobregasto import _filtros_locales
    local = _filtros_locales(filters, filtro_div, filtro_resp)
    tm = (filters or {}).get("tipo_monto", "Monto Constante")
    m_col = "monto_loc" if tm == "Monto Local" else "monto_cons"
    f_col = "FCST_loc" if tm == "Monto Local" else "FCST_cons"
    where = ddb._where_sobregasto(local)
    filt = "".join(f" AND {w}" for w in where)
    solo_cta = all(w.split(" ")[0] in _COLS_FCST for w in where)
    dim = "cuenta" if (local.get("fg_gpo") or local.get("fg_cta") or local.get("fg_resp")) else "grupo"
    # Rollup MÁS CHICO que cubra las columnas de la consulta (mismo criterio que el resto del tablero): los CECO×semana
    # salen de rollup_ceco_general (3.4 M filas) y los grupos/cuentas de rollup_mes_contraloria (126 K), no de `consolidado`.
    cols_filtro = {w.split(" ")[0] for w in where}
    col_dim = "cat_Cuentas" if dim == "cuenta" else "cat_Grupo_de_Cuentas"
    fuente_ceco = ddb.fuente_cons(ddb._tabla_para({"ID_CENTRO_COSTOS"} | cols_filtro), canal)
    fuente_g = ddb.fuente_cons(ddb._tabla_para({col_dim} | cols_filtro), canal)
    tabla_fcst = None
    if solo_cta:
        tabla_fcst = "agg_fcst_srv_cuenta" if canal == "Fisico" else ("dig_fcst_srv_cuenta" if "dig_fcst_srv_cuenta" in ddb._tablas else None)
    fuente_ctx = ddb.fuente_cons("rollup_ceco_general", canal)

    def ejecutar(sql, p):
        return ddb._cursor().execute(sql, p).fetchdf()

    def contexto(ids):
        try:
            df = ejecutar(f"""
                SELECT CAST(ID_CENTRO_COSTOS AS VARCHAR) AS ceco, arg_max(cat_Agrupa1, sem) AS agrupador,
                       arg_max(cat_Direccion_Division, sem) AS division, arg_max(cat_Subdireccion_Territorio, sem) AS territorio,
                       arg_max(cat_Subdireccion_Zona, sem) AS zona, arg_max(cat_Subdireccion_Region, sem) AS region
                FROM {fuente_ctx} WHERE ID_CENTRO_COSTOS IN (SELECT unnest(?)) GROUP BY 1""", [list(ids)])
        except Exception:
            return {}
        return {r.ceco: r._asdict() for r in df.itertuples(index=False)}

    return analizar(
        ejecutar, ini=desde, fin=hasta, dim=dim, fuente_ceco=fuente_ceco, fuente_g=fuente_g, m_col=m_col, filt=filt, prm=[],
        tabla_fcst=tabla_fcst, f_col=f_col, where_fcst=filt if tabla_fcst else "", siempre_var=True,
        contexto=contexto, nombre_ceco=lambda c: ds.ceco_nombres.get(str(c), ""), canal=canal,
        ml_solo_coinciden=bool(local.get("fg_gpo") or local.get("fg_cta") or local.get("fg_resp") or local.get("fg_subtipo")))


# ---------------------------------------------------------------------------------------------- vistas
def _kpi(titulo, valor, sub, color="text-dark"):
    return dbc.Col(width=12, sm=6, lg=3, children=html.Div(className="vt-kpi-card", children=[
        html.Div(titulo, className="vt-kpi-title"), html.Div(valor, className=f"vt-kpi-value {color}"),
        html.Div(sub, className="vt-kpi-sub text-muted")]))


def _grafica(r, desde, hasta):
    s = r["serie"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=s["sem"], y=s["Real"] / 1e6, mode="lines", name="Real 2026",
                             line=dict(color=charts.color_serie("Real_2026"), width=2.5),
                             hovertemplate="Sem %{x}<br>Real: <b>$%{y:,.2f} M</b><extra></extra>"))
    if s["Plan"].abs().sum() > 0:
        fig.add_trace(go.Scatter(x=s["sem"], y=s["Plan"] / 1e6, mode="lines", name="Plan 2026",
                                 line=dict(color=GRIS, width=1.8, dash="dash"),
                                 hovertemplate="Sem %{x}<br>Plan: <b>$%{y:,.2f} M</b><extra></extra>"))
    a = r["tot_atip"]
    if not a.empty:
        fig.add_trace(go.Scatter(x=a["sem"], y=a["monto"] / 1e6, mode="markers", name="Semana atípica",
                                 marker=dict(color=TINTA, size=11, symbol="diamond", line=dict(color=ROJO, width=2)),
                                 customdata=(a["dif"] / 1e6), hovertemplate="Sem %{x}: atípica<br>Real: <b>$%{y:,.2f} M</b>"
                                 "<br>vs mediana: %{customdata:+,.2f} M<extra></extra>"))
    if desde > 1 or hasta < s["sem"].max():
        fig.add_vrect(x0=desde - 0.5, x1=hasta + 0.5, fillcolor=ROJO, opacity=0.06, line_width=0)
    fig.update_layout(template="plotly_white", height=300, margin=dict(l=10, r=10, t=10, b=20),
                      yaxis_title="$ MDP", xaxis_title="Semana", legend=dict(orientation="h", y=1.12, x=0),
                      font=dict(family="Manrope", size=11, color=TINTA),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    charts.estilo_basico(fig)
    return dcc.Graph(figure=fig, config={"displayModeBar": False}, style={"height": "300px"})


def _tabla_var(r, hasta):
    v, col = r["var"], r["col"]
    hay_f = r.get("hay_fcst")
    etq = "Cuenta" if col == "cat_Cuentas" else "Grupo de cuentas"
    cab = [etq, f"Real {hasta}", f"Real {hasta - 1}", "Var. Real", "Var. %", f"Plan {hasta}", "Var. Plan"]
    if hay_f:
        cab += ["FCST PLAN", "Real vs FCST"]
    cab.append("Real vs Plan")
    filas = []
    for x in v.head(MC.TOP_N).itertuples(index=False):
        d = dict(zip(v.columns, x))
        pos = d["Var_Real"] > 0
        celdas = [html.Td([d[col], html.Span(" ●", title="Atípico para esta fila", style={"color": "#a93226"}) if d["atipico"] else ""], className="fw-semibold text-dark"),
                  html.Td(_mdp(d["Real_N"])), html.Td(_mdp(d["Real_N1"]), className="text-muted"),
                  html.Td(_smdp(d["Var_Real"]), className=("fw-bold text-danger" if pos else "fw-bold text-dark")),
                  html.Td(f"{d['Var_Real_Pct']:+.2f}%".replace("-", "−"), className="text-muted"),
                  html.Td(_mdp(d["Plan_N"])), html.Td(_smdp(d["Var_Plan"]), className="text-muted")]
        if hay_f:
            celdas += [html.Td(_mdp(d["FCST_N"])), html.Td(_smdp(d["Real_vs_FCST"]))]
        celdas.append(html.Td(_smdp(d["Real_vs_Plan"]), className="fw-bold"))
        filas.append(html.Tr(celdas))
    return html.Div(className="vt-var-wrap table-responsive", children=html.Table(
        className="table table-hover align-middle mb-0 vt-var",
        children=[html.Thead(html.Tr([html.Th(c) for c in cab])), html.Tbody(filas)]))


def _estilo_tabla(id_, data, cols):
    """DataTable con el diseño del tablero (encabezado en mayúsculas con filete rojo, filas con línea fina)."""
    return dash_table.DataTable(
        id=id_, data=data, columns=cols, sort_action="native", page_size=12, style_as_list_view=True,
        style_table={"overflowX": "auto"},
        style_header={"fontSize": "10px", "fontWeight": "800", "letterSpacing": "0.12em", "textTransform": "uppercase",
                      "color": GRIS, "borderBottom": f"2px solid {ROJO}", "backgroundColor": "white"},
        style_cell={"fontSize": "13px", "fontWeight": "600", "color": TINTA, "padding": "8px 10px", "textAlign": "left",
                    "border": "0", "borderBottom": "1px solid #ccd1d9", "maxWidth": "260px", "overflow": "hidden",
                    "textOverflow": "ellipsis", "fontFamily": "inherit"},
        style_cell_conditional=[{"if": {"column_id": c["id"]}, "textAlign": "right"} for c in cols if c.get("type") == "numeric"],
        style_data_conditional=[{"if": {"state": "active"}, "backgroundColor": "#fbeceb", "border": "0"}],
        tooltip_data=[{c["id"]: {"value": str(r.get(c["id"], "")), "type": "text"} for c in cols if c["id"] in ("Nombre", "Causas")}
                      for r in data])


def _estado_modelo(r, canal):
    """Línea con la versión del modelo, hasta qué semana aprendió y si los datos cambiaron (drift)."""
    ml = r.get("ml") or {}
    st = ml.get("estado")
    if not st:
        return None
    c = (st.get("canales") or {}).get(canal)
    txt = f"Modelo no supervisado {st.get('version', '')} · entrenado hasta la semana {st.get('entrenado_hasta', '—')}"
    if c:
        pila = ", ".join(f"{NOMBRES.get(k, k)} (PSI {v:.2f})" for k, v in list((c.get("psi_top3") or {}).items())[:2])
        txt += f" · cambio en los datos: {c.get('drift', '—')}" + (f" por {pila}" if c.get("drift") != "OK" and pila else "")
        if c.get("sugerir_reentrenar"):
            txt += " · conviene revisar si reentrenar (no se reentrena solo)"
    return html.Div(txt, className="small text-muted mt-2")


def _tabla_ml(r, idx):
    al = r["ml"]["alertas"]
    if not al:
        return None
    d = pd.DataFrame(al)
    d["Rareza"] = (d["Score"] * 100).round(1)
    d["Tambien"] = d["Tambien_Estadistico"].map({True: "Sí", False: "Solo el modelo"})
    d["Monto (MDP)"] = (d["Monto_MXN"] / 1e6).round(2)
    cols = [{"name": n, "id": c, "type": "numeric" if c in ("Rareza", "Monto (MDP)") else "text"}
            for c, n in [("CECO", "CECO"), ("Nombre", "Nombre"), ("Agrupador", "Agrupador"), ("Division", "División"),
                         ("Territorio", "Territorio"), ("Semanas", "Semanas con alerta"), ("Rareza", "Rareza máx. (%)"),
                         ("Monto (MDP)", "Monto (MDP)"), ("Tambien", "¿También estadístico?"), ("Causas", "Qué pesa más (aprox.)")]]
    return html.Div([
        html.Div("Alertas del modelo no supervisado (qué CECOs se ven raros aunque el monto no sea enorme)",
                 className="small fw-bold text-uppercase text-secondary mt-3 mb-2", style={"letterSpacing": "0.1em"}),
        _estilo_tabla(f"mon-ml-{idx}", d[[c["id"] for c in cols]].to_dict("records"), cols)])


def _tabla_ceco(filas, hasta, id_, ml_hay=False):
    if not filas:
        return html.Div("Ningún CECO se salió de lo normal en este rango.", className="p-3 text-muted")
    d = pd.DataFrame(filas)
    d["Impacto (MDP)"] = (d["Impacto_vs_Normal_MXN"] / 1e6).round(2)
    d[f"Sem {hasta} (MDP)"] = (d[f"Monto_Sem_{hasta}_MXN"] / 1e6).round(2)
    d["Tipo"] = d["Tipo"].str.replace("_", " ").str.capitalize()
    cols = [("CECO", "CECO"), ("Nombre", "Nombre"), ("Agrupador", "Agrupador"), ("Division", "División"),
            ("Territorio", "Territorio"), ("Semanas_Atipicas", "Semanas atípicas"), ("Tipo", "Tipo"),
            ("Impacto (MDP)", "Impacto vs normal (MDP)"), (f"Sem {hasta} (MDP)", f"Sem {hasta} (MDP)")]
    if ml_hay:
        d["Modelo ML"] = [("Alerta · " if a else "Alto · ") + f"{s * 100:.1f}%" if s is not None else "—"
                          for a, s in zip(d.get("ML_Alerta", False), d.get("ML_Score"))]
        cols.append(("Modelo ML", "Modelo ML (rareza)"))
    return dash_table.DataTable(
        id=id_, data=d[[c for c, _ in cols]].to_dict("records"),
        columns=[{"name": n, "id": c, "type": "numeric" if "MDP" in c else "text",
                  "format": {"specifier": ",.2f"} if "MDP" in c else None} for c, n in cols],
        sort_action="native", page_size=12, style_as_list_view=True,
        style_table={"overflowX": "auto"},
        style_header={"fontSize": "10px", "fontWeight": "800", "letterSpacing": "0.12em", "textTransform": "uppercase",
                      "color": GRIS, "borderBottom": f"2px solid {ROJO}", "backgroundColor": "white"},
        style_cell={"fontSize": "13px", "fontWeight": "600", "color": TINTA, "padding": "8px 10px", "textAlign": "left",
                    "border": "0", "borderBottom": "1px solid #ccd1d9", "maxWidth": "260px", "overflow": "hidden",
                    "textOverflow": "ellipsis", "fontFamily": "inherit"},
        style_cell_conditional=[{"if": {"column_id": c}, "textAlign": "right"} for c, _ in cols if "MDP" in c],
        style_data_conditional=[
            {"if": {"filter_query": '{Tipo} = "Cambio estructural"', "column_id": "Tipo"}, "color": ROJO, "fontWeight": "800"},
            {"if": {"filter_query": "{Impacto (MDP)} > 0", "column_id": "Impacto (MDP)"}, "color": ROJO},
            {"if": {"filter_query": '{Modelo ML} contains "Alerta"', "column_id": "Modelo ML"}, "color": ROJO, "fontWeight": "800"},
            {"if": {"state": "active"}, "backgroundColor": "#fbeceb", "border": "0"}],
        tooltip_data=[{"Nombre": {"value": str(f.get("Nombre", "")), "type": "text"}} for f in d.to_dict("records")],
    )


def _bloque_canal(canal, r, desde, hasta, titulo_canal, idx):
    nombre = CANALES[canal][1]
    hijos = []
    if titulo_canal:
        hijos.append(html.H6(f"Canal {nombre}", className="fw-bold text-dark mt-2 mb-3",
                             style={"letterSpacing": "0.04em"}))
    if r["vacio"]:
        hijos.append(html.Div(f"No hay gasto Real en el canal {nombre} para estos filtros.", className="p-3 text-muted"))
        return html.Div(hijos, className="mb-4")
    s, a, ce = r["serie"], r["tot_atip"], r["ceco_atip"]
    estruct = sum(1 for f in ce if f["Tipo"] == "CAMBIO_ESTRUCTURAL")
    real_n = float(s.loc[s["sem"] == hasta, "Real"].sum())
    real_n1 = float(s.loc[s["sem"] == hasta - 1, "Real"].sum())
    plan_n = float(s.loc[s["sem"] == hasta, "Plan"].sum())
    hijos.append(dbc.Row(className="g-3 mb-3", children=[
        _kpi("Semanas atípicas (total)", f"{len(a)}", texto_semanas(a["sem"].tolist()) if len(a) else "ninguna en el rango",
             "text-danger" if len(a) else "text-dark"),
        _kpi("CECOs atípicos", f"{len(ce):,}", f"{estruct} con cambio estructural", "text-danger" if len(ce) else "text-dark"),
        _kpi(f"Real sem {hasta} vs {hasta - 1}", _smdp(real_n - real_n1),
             f"{_mdp(real_n)} contra {_mdp(real_n1)}" if hasta > 1 else "—",
             "text-danger" if real_n > real_n1 else "text-dark"),
        _kpi(f"Real vs Plan sem {hasta}", _smdp(real_n - plan_n), f"Real {_mdp(real_n)} · Plan {_mdp(plan_n)}",
             "text-danger" if real_n > plan_n else "text-dark")]))
    hijos.append(_grafica(r, desde, hasta))
    if "var" in r and not r["var"].empty:
        hijos.append(html.Div(f"Semana {hasta} contra semana {hasta - 1}, por "
                              f"{'cuenta' if r['col'] == 'cat_Cuentas' else 'grupo de cuentas'} (de mayor a menor variación del Real)",
                              className="small fw-bold text-uppercase text-secondary mt-3 mb-2", style={"letterSpacing": "0.1em"}))
        hijos.append(_tabla_var(r, hasta))
        if not r.get("hay_fcst"):
            hijos.append(html.Div("FCST PLAN solo se compara con filtros de grupo o cuenta (no existe por CECO, PDV ni territorio).",
                                  className="small text-muted mt-1"))
    ag = r["grupos_atip"]
    if desde < hasta and not ag.empty:
        etq = "Cuentas" if r["col"] == "cat_Cuentas" else "Grupos de cuentas"
        hijos.append(html.Div(f"{etq} atípicos en el rango", className="small fw-bold text-uppercase text-secondary mt-3 mb-2",
                              style={"letterSpacing": "0.1em"}))
        hijos.append(html.Ul(className="mb-0 small", children=[
            html.Li([html.B(x.ent), f" — semanas {texto_semanas(x.sems)}, ", _smdp(x.imp), " contra su nivel normal"])
            for x in ag.head(MC.TOP_N).itertuples()]))
    hijos.append(html.Div("CECOs atípicos (semanas exactas, con su ubicación en la estructura)",
                          className="small fw-bold text-uppercase text-secondary mt-3 mb-2", style={"letterSpacing": "0.1em"}))
    hijos.append(_tabla_ceco(ce, hasta, f"mon-tabla-{idx}", ml_hay=(r.get("ml") or {}).get("hay", False)))
    tml = _tabla_ml(r, idx)
    if tml is not None:
        hijos.append(tml)
    est = _estado_modelo(r, canal)
    if est is not None:
        hijos.append(est)
    conc = r["concentracion"]
    if conc:
        txt = " y en ".join(f"{et} {conc[k][0]} ({conc[k][1]:.0f}% del impacto)"
                            for k, et in (("Agrupador", "el agrupador"), ("Division", "la división")) if k in conc)
        hijos.append(html.Div(f"El impacto se concentra en {txt}.", className="small text-dark mt-2"))
    return html.Div(hijos, className="mb-4")


# ---------------------------------------------------------------------------------------------- callbacks
@callback(
    Output("mon-contenido", "children"),
    Output("mon-badge", "children"),
    Input("global-filter-store", "data"),
    Input("sg-filtro-div", "value"),
    Input("sg-filtro-resp", "value"),
    Input("mon-canal", "value"),
    Input("mon-desde", "value"),
    Input("mon-hasta", "value"),
)
def actualizar_monitoreo(filters, filtro_div, filtro_resp, canal_sel, desde, hasta):
    desde, hasta = int(desde or 1), int(hasta or ds.smr)
    if desde > hasta:
        desde, hasta = hasta, desde
    canales = _canales(filters, canal_sel)
    try:
        bloques = []
        n_atip = 0
        for i, cn in enumerate(canales):
            r = _analizar_canal(cn, filters, filtro_div, filtro_resp, desde, hasta)
            n_atip += len(r.get("ceco_atip", []))
            bloques.append(_bloque_canal(cn, r, desde, hasta, len(canales) > 1, i))
    except Exception as e:
        return html.Div(f"No se pudo calcular el análisis: {e}", className="p-3 text-danger"), ""
    nombre = " y ".join(CANALES[c][1] for c in canales)
    badge = html.Span(f"Canal {nombre} · semanas {desde} a {hasta}",
                      className="badge bg-danger-subtle text-danger border border-danger-subtle fw-semibold px-2 py-1")
    return bloques, badge


@callback(
    Output("download-mon", "data"),
    *download_inputs("btn-download-mon"),
    State("global-filter-store", "data"),
    State("sg-filtro-div", "value"),
    State("sg-filtro-resp", "value"),
    State("mon-canal", "value"),
    State("mon-desde", "value"),
    State("mon-hasta", "value"),
    prevent_initial_call=True,
)
def descargar_monitoreo(_n_num, _n_conta, filters, filtro_div, filtro_resp, canal_sel, desde, hasta):
    contable = modo_descarga()
    if contable is None:
        return dash.no_update
    desde, hasta = int(desde or 1), int(hasta or ds.smr)
    if desde > hasta:
        desde, hasta = hasta, desde
    filas = []
    for cn in _canales(filters, canal_sel):
        r = _analizar_canal(cn, filters, filtro_div, filtro_resp, desde, hasta)
        filas += [{"Canal": CANALES[cn][1], **f} for f in r.get("ceco_atip", [])]
    return enviar_tabla(pd.DataFrame(filas), f"cecos_atipicos_sem{desde}-{hasta}", contable)
