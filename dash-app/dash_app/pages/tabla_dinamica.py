# -*- coding: utf-8 -*-
"""
dash_app/pages/tabla_dinamica.py
Pestaña Tabla Dinámica — BI Ad-Hoc Multidimensional.
Paridad funcional 100% con Streamlit (Selección y reordenamiento de dimensiones dinámicas con lazy tree multinivel sobre DuckDB).
"""
from dash import html, dcc, callback, ctx, Input, Output, State, ALL, MATCH, no_update
import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
from pathlib import Path
from dash_app.services.duckdb_service import DuckDBService
from dash_app.services.data_service import DataService, MC
from dash_app.components.tree_view import render_tree_node, render_tree_header, render_tree_total_row, decode_node_meta
from dash_app.components.top_filter_bar import NOM_MES
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla, tabla_descarga

ddb = DuckDBService()
ds = DataService()

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
AGGS_DIR = BASE_DIR / "aggs"

# Dimensiones para las que forecast_cuenta.parquet tiene la columna de agrupación
_FC_DIMS_SOPORTADAS = {"cat_Grupo_de_Cuentas", "cat_Cuentas"}


def _mes_fiscal(raw: int, anio: int) -> int:
    """Mes 1-12 de un AAAAMM dentro de `anio`: lo anterior cae en enero y lo posterior en diciembre
    (mismos bordes que _peso_expr en Retail Channel Spend)."""
    if raw // 100 == anio:
        return min(12, max(1, raw - anio * 100))
    return 1 if raw < anio * 100 + 1 else 12


def _unificar_mes_fiscal_td(df: pd.DataFrame) -> pd.DataFrame:
    """Port de `_unificar_mes_fiscal` (Retail Channel Spend, 2026-10-05). Agrupando por la columna
    cruda MES (AAAAMM), el Real 2025 cae en filas 2025xx y Real/Plan 2026 en 2026xx. Aquí cada
    fila manda Real 2025 a su mes de 2025 y Plan/Real 2026 a su mes de 2026; todo queda con
    AAAAMM 2026xx y ordenado cronológicamente."""
    if df.empty or "MES" not in df.columns:
        return df
    medidas = [m for m in ("Real_2025", "Plan_2026", "Real_2026") if m in df.columns]
    otras = [c for c in df.columns if c not in medidas and c != "MES"]
    # MES nulo (filas digitales sin mes) cae en enero: no se pierde el monto y no truena el int()
    m25 = df["MES"].map(lambda r: 202600 + _mes_fiscal(int(r) if pd.notna(r) else 0, 2025))
    m26 = df["MES"].map(lambda r: 202600 + _mes_fiscal(int(r) if pd.notna(r) else 0, 2026))
    a = df.copy()
    a["MES"] = m25
    a[[m for m in medidas if m != "Real_2025"]] = 0.0
    b = df.copy()
    b["MES"] = m26
    if "Real_2025" in medidas:
        b["Real_2025"] = 0.0
    g = pd.concat([a, b], ignore_index=True)
    return g.groupby(otras + ["MES"], as_index=False, dropna=False)[medidas].sum().sort_values(otras + ["MES"], ignore_index=True)


def _nombre_mes_td(v) -> str:
    """AAAAMM 2026xx -> nombre del mes para mostrar (el valor interno sigue siendo AAAAMM)."""
    try:
        return NOM_MES[int(v) % 100]
    except (KeyError, TypeError, ValueError):
        return str(v)


DIMENSIONES_OPTS = [
    {"label": "División", "value": "cat_Direccion_Division"},
    {"label": "Territorio", "value": "cat_Subdireccion_Territorio"},
    {"label": "Zona", "value": "cat_Subdireccion_Zona"},
    {"label": "Región", "value": "cat_Subdireccion_Region"},
    {"label": "Agrupador de Reales", "value": "cat_Agrupador_Reales"},
    {"label": "PDV", "value": "ECO_PDV"},
    {"label": "Grupo de Cuentas", "value": "cat_Grupo_de_Cuentas"},
    {"label": "Cuenta", "value": "cat_Cuentas"},
    {"label": "Contraloría", "value": "cat_Contraloria"},
    {"label": "Contraloría 6", "value": "cat_Contraloria_6"},
    {"label": "Mes", "value": "MES"},
    {"label": "Subtipo", "value": "cat_Subtipo"},
    {"label": "Responsable", "value": "cat_Responsable_Grupo"},
    {"label": "CECO", "value": "ID_CENTRO_COSTOS"}
]
_ETIQ_DIM = {o["value"]: o["label"] for o in DIMENSIONES_OPTS}
DIMS_DEFECTO = ["cat_Direccion_Division", "cat_Grupo_de_Cuentas"]

# Responsable NO es columna del consolidado ni de sus rollups: se deriva de la cuenta (cada cuenta tiene un
# solo responsable en responsables_cta.json), así que se consulta por cuenta y se traduce al terminar.
_RESP = "cat_Responsable_Grupo"
_MEDS = ["Real_2025", "Plan_2026", "Real_2026"]
_SIN_DATOS = "__sin_datos__"   # centinela: filtro con intersección vacía devuelve 0 filas, no "sin filtro"
MAX_HIJOS = 300                # filas máximas por nivel en pantalla (Excel y PDF llevan todo)

ORDENES_FILAS = [
    {"label": "Predeterminado", "value": "defecto"},
    {"label": "Mayor gasto primero", "value": "gasto_desc"},
    {"label": "Menor gasto primero", "value": "gasto_asc"},
    {"label": "Mayor desviación vs Plan", "value": "desv_desc"},
    {"label": "Nombre A-Z", "value": "nombre"},
]

_cta_resp_cache = {}


def _cta_a_resp():
    """cuenta -> responsable (invierte responsables_cta.json: responsable -> [cuentas])."""
    if not _cta_resp_cache:
        for resp, ctas in (ds.responsables_cta or {}).items():
            for c in ctas:
                _cta_resp_cache[c] = resp
    return _cta_resp_cache


def _etiqueta_dim(col, v):
    """Texto con el que se muestra un valor de dimensión (igual que el árbol): mes por nombre, PDV/CECO con nombre."""
    v = str(v)
    if col == "MES":
        return _nombre_mes_td(v)
    if col in ("ECO_PDV", "ID_CENTRO_COSTOS") and v.replace(".", "", 1).replace("-", "", 1).isdigit():
        limpio = v.split(".")[0].strip()
        nombre = (ds.pdv_nombres if col == "ECO_PDV" else ds.ceco_nombres).get(limpio)
        return f"{limpio} - {nombre}" if nombre else limpio
    return v


def _inter(a, b):
    """Intersección de dos listas de filtro (None = sin filtro). Vacía -> centinela."""
    if a is None:
        r = list(b)
    elif b is None:
        r = list(a)
    else:
        sb = set(b)
        r = [x for x in a if x in sb]
    return r or [_SIN_DATOS]


def _filtros_sql(filters, filtro_valores):
    """Filtros para query_ceco_summary + extras que SQL no cubre.
    Devuelve (loc, meses, loc_fc, fuera_fc): `loc` va a SQL; `meses` = {1..12} elegidos o None (se filtra
    después de unificar el mes fiscal); `loc_fc` = filtros LOCALES sobre Grupo/Cuenta (el Forecast se parte por
    esas columnas); `fuera_fc` = hay un filtro local que el Forecast no puede aplicar."""
    f = filters or {}
    loc = {}
    for k_g, col in (("fg_gpo", "cat_Grupo_de_Cuentas"), ("fg_cta", "cat_Cuentas"), ("fg_subtipo", "cat_Subtipo")):
        if f.get(k_g):
            loc[col] = [str(x) for x in f[k_g]]
    loc["canal"] = f.get("canal")
    meses, loc_fc, fuera_fc = None, {}, False
    for col, vals in (filtro_valores or {}).items():
        if not vals:
            continue
        if col == "MES":
            meses = {int(v) for v in vals}
            fuera_fc = True
        elif col == _RESP:
            ctas = sorted({c for r in vals for c in (ds.responsables_cta or {}).get(r, [])})
            loc["cat_Cuentas"] = _inter(loc.get("cat_Cuentas"), ctas)
            loc_fc["cat_Cuentas"] = loc["cat_Cuentas"]
        else:
            loc[col] = _inter(loc.get(col), [str(x) for x in vals])
            if col in _FC_DIMS_SOPORTADAS:
                loc_fc[col] = loc[col]
            else:
                fuera_fc = True
    return loc, meses, loc_fc, fuera_fc


def _frame_td(filters, dims, filtro_valores):
    """Tabla plana de hojas (una fila por combinación de `dims`) con Real_2025, Plan_2026, Real_2026 y, si aplica,
    Forecast. Es la única fuente de la pantalla, el Excel y el PDF: los tres siempre cuadran.
    Devuelve (df, inc_fc, si, sf). Las dimensiones salen como texto (Mes como AAAAMM 2026xx)."""
    f = filters or {}
    si = f.get("si", 1)
    sf = f.get("sf", ds.smr)
    tm = f.get("tipo_monto", "Monto Constante")
    dims = list(dims)
    loc, meses, loc_fc, fuera_fc = _filtros_sql(f, filtro_valores)

    sql_dims = list(dict.fromkeys("cat_Cuentas" if d == _RESP else d for d in dims))
    if meses and "MES" not in sql_dims:
        sql_dims.append("MES")
    df = ddb.query_ceco_summary(sql_dims, si=si, sf=sf, filters=loc, tipo_monto=tm)
    if df.empty:
        return df, False, si, sf
    if "MES" in sql_dims:
        df = _unificar_mes_fiscal_td(df)
        if meses:
            df = df[(df["MES"] % 100).isin(meses)]
        df = df.assign(MES=df["MES"].astype(int).astype(str))
    if _RESP in dims:
        df = df.assign(**{_RESP: df["cat_Cuentas"].map(_cta_a_resp()).fillna("Sin responsable")})
    for d in dims:
        if d != "MES":
            df[d] = df[d].fillna("(Sin dato)").astype(str)
    df = df.groupby(dims, as_index=False, dropna=False)[_MEDS].sum()   # quita cuenta/mes auxiliares

    inc_fc = False
    if (not fuera_fc and not meses and set(dims) <= _FC_DIMS_SOPORTADAS and set(loc_fc) <= set(dims)
            and ds.ddb.tiene_agg("cuentas")):
        fc = ds._forecast_cruzado("cuentas", f, tm, group_col=dims)
        fc = fc if len(fc) else pd.DataFrame(columns=dims + ["Forecast"])
        # outer: una cuenta con Forecast pero sin Real/Plan no tiene fila en df; con "left" su Forecast se perdía
        df = df.merge(fc, on=dims, how="outer")
        for c in _MEDS + ["Forecast"]:
            df[c] = df[c].fillna(0.0)
        for col, vals in loc_fc.items():
            df = df[df[col].isin(vals)]
        inc_fc = True
    return df, inc_fc, si, sf


def _buscar_td(df, dims, q_buscar):
    """Búsqueda en cualquier nivel (sin acentos ni mayúsculas, sobre el texto que se muestra): se quedan las hojas
    que coinciden en alguna dimensión, y con ellas sus ramas."""
    q = _norm_td(q_buscar)
    if not q:
        return df
    mask = pd.Series(False, index=df.index)
    for d in dims:
        textos = {u: _norm_td(_etiqueta_dim(d, u)) for u in df[d].unique()}
        mask |= df[d].map(textos).str.contains(q, regex=False)
    return df[mask]


def _ordenar_td(g, col, orden):
    """Ordena las filas de un nivel. 'defecto' = Grupo/Cuenta por catálogo, Mes por calendario, resto por Real 2026."""
    if orden in ("gasto_desc", "gasto_asc"):
        return g.reindex(g["Real_2026"].abs().sort_values(ascending=(orden == "gasto_asc"), kind="stable").index).reset_index(drop=True)
    if orden == "desv_desc":
        return g.reindex(g["Vs_Plan_Abs"].abs().sort_values(ascending=False, kind="stable").index).reset_index(drop=True)
    if orden == "nombre":
        clave = g["Concepto"].map(lambda v: _norm_td(_etiqueta_dim(col, v)))
        return g.reindex(clave.sort_values(kind="stable").index).reset_index(drop=True)
    if col in ("cat_Grupo_de_Cuentas", "cat_Cuentas"):
        return ds.ordenar_hijos(g, col)
    if col == "MES":
        return g.sort_values("Concepto").reset_index(drop=True)
    return g.sort_values("Real_2026", ascending=False).reset_index(drop=True)


def _nivel_td(df, dims, nivel, inc_fc, orden):
    """Filas del nivel `nivel` (agrupa por dims[nivel]) con % sobre sus hermanos y variaciones, ya ordenadas."""
    col = dims[nivel]
    sumar = _MEDS + (["Forecast"] if inc_fc else [])
    g = df.groupby(col, as_index=False)[sumar].sum()
    tot = {m: g[m].sum() for m in sumar}
    for p, m in (("Pct_r25", "Real_2025"), ("Pct_p26", "Plan_2026"), ("Pct_r26", "Real_2026")):
        g[p] = (g[m] / tot[m] * 100) if tot[m] else 0.0
    g["Vs_Plan_Abs"] = g["Real_2026"] - g["Plan_2026"]
    g["Vs_Plan_Pct"] = np.where(g["Plan_2026"] != 0, g["Vs_Plan_Abs"] / g["Plan_2026"].abs() * 100, 0.0)
    g["Vs_AA_Abs"] = g["Real_2026"] - g["Real_2025"]
    g["Vs_AA_Pct"] = np.where(g["Real_2025"] != 0, g["Vs_AA_Abs"] / g["Real_2025"].abs() * 100, 0.0)
    if inc_fc:
        g["Vs_Forecast"] = g["Real_2026"] - g["Forecast"]
        g["Vs_Forecast_Pct"] = np.where(g["Forecast"] != 0, g["Vs_Forecast"] / g["Forecast"].abs() * 100, 0.0)
    return _ordenar_td(g.rename(columns={col: "Concepto"}), col, orden)


def _nodos_td(df, dims, nivel, path, inc_fc, orden):
    """Nodos del árbol para un nivel; los hijos se piden al abrir cada rama (toggle_rama_td)."""
    col = dims[nivel]
    es_hoja = nivel == len(dims) - 1
    g = _nivel_td(df, dims, nivel, inc_fc, orden)
    nodos = [
        render_tree_node(
            r, level=nivel, is_leaf=es_hoja, tree_key="td-tree", parent_col=col,
            target_col="" if es_hoja else dims[nivel + 1], next_cols=dims[nivel + 1:],
            inc_fc=inc_fc, ancestor_path=path, prefijo_ids="td",
        )
        for _, r in g.head(MAX_HIJOS).iterrows()
    ]
    if len(g) > MAX_HIJOS:
        nodos.append(html.Div(
            f"Se muestran {MAX_HIJOS:,} de {len(g):,} filas de este nivel. Usa filtros o la búsqueda para acotar.",
            className="td-aviso", style={"paddingLeft": f"{nivel * 20 + 32}px"}))
    return nodos


def _arbol_td(df, dims, inc_fc, orden):
    tot = {m: df[m].sum() for m in _MEDS + (["Forecast"] if inc_fc else [])}
    return html.Div(className="vt-tree-wrap", children=[html.Div(
        className="vt-tree vt-tree-fc" if inc_fc else "vt-tree",
        children=[render_tree_header(inc_fc=inc_fc),
                  html.Div(_nodos_td(df, dims, 0, [], inc_fc, orden)),
                  render_tree_total_row(tot, inc_fc=inc_fc)])])


def _chips_td(orden):
    """Dimensiones elegidas en su orden jerárquico, cada una con subir/bajar/quitar."""
    if not orden:
        return html.Span("Elige al menos una dimensión.", className="td-vacio")
    chips = []
    for i, d in enumerate(orden):
        def _btn(accion, txt, titulo, off=False):
            return html.Button(txt, id={"type": "td-mover", "dim": d, "dir": accion}, className="td-chip-b",
                               title=titulo, disabled=off, n_clicks=0)
        chips.append(html.Div(className="td-chip", children=[
            html.Span(str(i + 1), className="td-chip-n"),
            html.Span(_ETIQ_DIM.get(d, d), className="td-chip-t"),
            _btn("up", "‹", "Mover antes (más general)", i == 0),
            _btn("down", "›", "Mover después (más detallado)", i == len(orden) - 1),
            _btn("del", "×", "Quitar"),
        ]))
    return chips


def layout():
    return html.Div(
        children=[
            dcc.Download(id="td-download-csv"),
            dcc.Download(id="td-download-pdf"),
            dcc.Store(id="td-store-order", data=list(DIMS_DEFECTO)),
            dcc.Store(id="td-filtro-valores-store", data={}),
            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                style={"marginBottom": "16px"},
                children=[
                    dbc.Row(
                        children=[
                            dbc.Col(
                                children=[html.H5("Tabla Dinámica — Exploración Multidimensional BI Ad-Hoc",
                                                  style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "0"})],
                                width=8
                            ),
                            dbc.Col(
                                html.Div([download_buttons("td-btn-download"),
                                          dbc.Button([html.Span(className="ico ico-doc"), " PDF"], id="td-btn-pdf", color="secondary", outline=True, size="sm", className="vt-dl-btns ms-2"),
                                          html.Div(id="td-pdf-status", style={"fontSize": "0.78rem", "color": "#64748b"})], className="float-end text-end"),
                                width=4,
                                className="d-flex align-items-center justify-content-end"
                            )
                        ]
                    ),
                    html.Hr(style={"margin": "12px 0"}),
                    dbc.Row([
                        dbc.Col([
                            html.Label("Dimensiones", className="td-lbl"),
                            dcc.Dropdown(id="td-select-dimensiones", options=DIMENSIONES_OPTS, value=list(DIMS_DEFECTO),
                                         multi=True, placeholder="Agrega dimensiones..."),
                        ], md=4),
                        dbc.Col([
                            html.Label("Orden de la jerarquía (de lo general a lo detallado)", className="td-lbl"),
                            html.Div(id="td-chips", className="td-chips", children=_chips_td(DIMS_DEFECTO)),
                        ], md=8),
                    ], className="g-3"),
                    html.Div(style={"marginTop": "14px"}, children=[
                        html.Label("Filtros (opcional)", className="td-lbl"),
                        dcc.Dropdown(id="td-filtro-columnas", options=DIMENSIONES_OPTS, multi=True,
                                     placeholder="Elige las columnas por las que quieres filtrar...",
                                     style={"marginBottom": "8px"}),
                        html.Div(id="td-filtro-valores-container", className="td-filtros-grid"),
                    ]),
                ]
            ),
            html.Div(
                className="td-barra",
                children=[
                    dcc.Input(id="td-buscar", type="search", placeholder="Buscar en la tabla (cualquier nivel) y Enter",
                              debounce=True, className="form-control", style={"maxWidth": "360px"}),
                    html.Div([html.Span("Ordenar filas", className="td-lbl me-2"),
                              dcc.Dropdown(id="td-orden-filas", options=ORDENES_FILAS, value="defecto", clearable=False,
                                           style={"width": "230px"})],
                             className="d-flex align-items-center"),
                ],
            ),
            html.Div(id="tabla-dinamica-tree-container")
        ]
    )

def _norm_td(txt):
    """Minúsculas y sin acentos, para comparar texto de búsqueda."""
    import unicodedata
    s = unicodedata.normalize("NFD", str(txt or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").strip()


def _opciones_valores_td(col):
    """Opciones del filtro de valores de una columna (con etiqueta legible; Responsable y Mes no vienen del consolidado)."""
    if col == _RESP:
        return [{"label": r, "value": r} for r in sorted(ds.responsables_cta or {})]
    if col == "MES":
        return [{"label": NOM_MES[m], "value": m} for m in range(1, 13)]
    vals = ddb.get_unique_values(col, 5000 if col in ("ECO_PDV", "ID_CENTRO_COSTOS") else 1000)
    vals = sorted((v for v in vals if v is not None and str(v) not in ("(Sin dato)", "Sin dato", "nan", "")), key=str)
    return [{"label": _etiqueta_dim(col, v), "value": v} for v in vals]


@callback(
    Output("td-store-order", "data"),
    Output("td-select-dimensiones", "value"),
    Input("td-select-dimensiones", "value"),
    Input({"type": "td-mover", "dim": ALL, "dir": ALL}, "n_clicks"),
    State("td-store-order", "data"),
)
def gestionar_dimensiones(seleccion, _clicks, orden_prev):
    """Mantiene el ORDEN de las dimensiones: el menú decide cuáles hay; los botones de cada una las reordenan o quitan."""
    validas = {o["value"] for o in DIMENSIONES_OPTS}
    prev = [d for d in (orden_prev or []) if d in validas]
    orden = list(prev)
    disparo = ctx.triggered_id
    seleccion_out = no_update
    if isinstance(disparo, dict):
        # los botones recién dibujados también disparan el callback (sin clic real): se ignoran
        if any(t.get("value") for t in ctx.triggered) and disparo["dim"] in orden:
            i = orden.index(disparo["dim"])
            if disparo["dir"] == "up" and i > 0:
                orden[i - 1], orden[i] = orden[i], orden[i - 1]
            elif disparo["dir"] == "down" and i < len(orden) - 1:
                orden[i + 1], orden[i] = orden[i], orden[i + 1]
            elif disparo["dir"] == "del":
                orden.pop(i)
                seleccion_out = list(orden)
    else:
        sel = [d for d in (seleccion or []) if d in validas]
        orden = [d for d in prev if d in sel] + [d for d in sel if d not in prev]
    if orden == prev and disparo is not None:
        return no_update, no_update
    return orden, seleccion_out


@callback(
    Output("td-chips", "children"),
    Input("td-store-order", "data"),
)
def dibujar_chips(orden):
    return _chips_td(orden or [])


@callback(
    Output("td-filtro-valores-container", "children"),
    Input("td-filtro-columnas", "value"),
    State("td-filtro-valores-store", "data"),
)
def render_filtros_valores(columnas, previos):
    previos = previos or {}
    children = []
    for col in columnas or []:
        children.append(html.Div(children=[
            html.Span(f"{_ETIQ_DIM.get(col, col)} =", className="td-lbl"),
            dcc.Dropdown(
                id={"type": "td-filtro-valor", "col": col},
                options=_opciones_valores_td(col),
                value=previos.get(col) or None,
                multi=True,
                placeholder="Todos",
                className="form-select-sm shadow-none",
            ),
        ]))
    return children

@callback(
    Output("td-filtro-valores-store", "data"),
    Input({"type": "td-filtro-valor", "col": ALL}, "value"),
    Input({"type": "td-filtro-valor", "col": ALL}, "id"),
    Input("td-filtro-columnas", "value"),
)
def sync_filtro_valores(valores, ids, columnas):
    """Solo cuentan los filtros de columnas que siguen elegidas (al quitar una, su valor sale al instante)."""
    activas = set(columnas or [])
    return {i["col"]: v for v, i in zip(valores, ids) if v and i["col"] in activas}

@callback(
    Output("tabla-dinamica-tree-container", "children"),
    Input("global-filter-store", "data"),
    Input("td-store-order", "data"),
    Input("td-filtro-valores-store", "data"),
    Input("td-buscar", "value"),
    Input("td-orden-filas", "value"),
)
def update_tabla_dinamica(filters, dimensiones, filtro_valores, q_buscar=None, orden="defecto"):
    if not dimensiones:
        return dbc.Alert("Selecciona al menos una dimensión para agrupar.", color="info")
    df, inc_fc, _si, _sf = _frame_td(filters, dimensiones, filtro_valores)
    if df.empty:
        return dbc.Alert("Sin resultados para la combinación de dimensiones y filtros seleccionados.", color="warning")
    df = _buscar_td(df, dimensiones, q_buscar)
    if df.empty:
        return dbc.Alert(f"Ninguna fila coincide con «{(q_buscar or '').strip()}».", color="warning")
    return _arbol_td(df, list(dimensiones), inc_fc, orden or "defecto")


@callback(
    Output({"type": "td-children", "tree": "td-tree", "node": MATCH}, "style"),
    Output({"type": "td-children", "tree": "td-tree", "node": MATCH}, "children"),
    Output({"type": "td-toggle-btn", "tree": "td-tree", "node": MATCH}, "children"),
    Input({"type": "td-toggle-btn", "tree": "td-tree", "node": MATCH}, "n_clicks"),
    State({"type": "td-children", "tree": "td-tree", "node": MATCH}, "children"),
    State({"type": "td-toggle-btn", "tree": "td-tree", "node": MATCH}, "id"),
    State("global-filter-store", "data"),
    State("td-store-order", "data"),
    State("td-filtro-valores-store", "data"),
    State("td-buscar", "value"),
    State("td-orden-filas", "value"),
    prevent_initial_call=True,
)
def toggle_rama_td(n_clicks, hijos_actuales, btn_id, filters, dimensiones, filtro_valores, q_buscar, orden):
    """Abre/cierra una rama. Los hijos salen de la MISMA tabla (mismos filtros, búsqueda y orden) que la raíz,
    filtrada por la ruta de ancestros del nodo; la consulta SQL está en caché, así que abrir es inmediato."""
    if not n_clicks:
        return no_update, no_update, "+"
    abierto = n_clicks % 2 == 1
    estilo = {"display": "block"} if abierto else {"display": "none"}
    etiqueta = "-" if abierto else "+"
    if not abierto or hijos_actuales:
        return estilo, no_update, etiqueta

    meta = decode_node_meta(btn_id.get("node", ""))
    path = meta.get("path", [])
    dims = list(dimensiones or [])
    if not path or len(path) >= len(dims):
        return estilo, [], etiqueta
    df, inc_fc, _si, _sf = _frame_td(filters, dims, filtro_valores)
    df = _buscar_td(df, dims, q_buscar)
    for col, val in path:
        df = df[df[col] == val]
    if df.empty:
        return estilo, [html.Div("Sin sub-elementos para este nivel", className="td-aviso",
                                 style={"paddingLeft": f"{len(path) * 20 + 32}px"})], etiqueta
    return estilo, _nodos_td(df, dims, len(path), path, inc_fc, orden or "defecto"), etiqueta

_TD_PDF_UMBRAL_SEG = 300
_TD_PDF_MAX_FILAS = 20000


def _filtros_locales(filters, filtro_valores=None):
    loc = {}
    if filters:
        if filters.get("fg_resp"): loc["cat_Responsable_Grupo"] = filters["fg_resp"]
        if filters.get("fg_gpo"): loc["cat_Grupo_de_Cuentas"] = filters["fg_gpo"]
        if filters.get("fg_cta"): loc["cat_Cuentas"] = filters["fg_cta"]
        if filters.get("fg_subtipo"): loc["cat_Subtipo"] = filters["fg_subtipo"]
        loc["canal"] = filters.get("canal")
    if filtro_valores:
        loc.update(filtro_valores)
    return loc


def _df_plano_td(filters, dimensiones, filtro_valores):
    """Tabla plana de Tabla Dinámica (compartida por Excel y PDF): dimensiones elegidas en su
    orden + montos + variaciones. Forecast solo si todas las dimensiones son Grupo de Cuentas y/o
    Cuenta (única forma de cruzarlo sin repetirlo por fila). Vacía si no hay datos."""
    dims = list(dimensiones)
    df_sql, _inc_fc, si, sf = _frame_td(filters, dims, filtro_valores)   # misma fuente que la pantalla
    if df_sql.empty:
        return df_sql, si, sf
    return tabla_descarga(df_sql, dims), si, sf


@callback(
    Output("td-download-csv", "data"),
    *download_inputs("td-btn-download"),
    State("global-filter-store", "data"),
    State("td-store-order", "data"),
    State("td-filtro-valores-store", "data"),
    prevent_initial_call=True
)
def download_tabla_dinamica_excel(_n_num, _n_conta, filters, dimensiones, filtro_valores):
    contable = modo_descarga()
    if contable is None or not dimensiones:
        return no_update
    tabla, si, sf = _df_plano_td(filters, dimensiones, filtro_valores)
    if tabla.empty:
        return no_update
    return enviar_tabla(tabla, f"tabla_dinamica_sem{si}-{sf}", contable)


# ------------------------------------------------------------------ PDF
def _fila_td_html(vals, r, inc_fc, total=False, subtotal=False):
    from dash_app.pages.resumen import _celda_var
    cb = " class='num tot'" if total else " class='num'"
    c26 = " class='num r26 tot'" if total else " class='num r26'"
    t = "".join(f"<td>{v}</td>" for v in vals)
    t += f"<td{cb}>{MC(r['Real_2025'])}</td><td{cb}>{r['Pct_r25']:.1f}%</td>"
    t += f"<td{cb}>{MC(r['Plan_2026'])}</td><td{cb}>{r['Pct_p26']:.1f}%</td>"
    t += f"<td{c26}>{MC(r['Real_2026'])}</td><td{c26}>{r['Pct_r26']:.1f}%</td>"
    t += _celda_var(r["Vs_Plan_Abs"], MC(r["Vs_Plan_Abs"])) + _celda_var(r["Vs_Plan_Abs"], f"{r['Vs_Plan_Pct']:+.1f}%")
    t += _celda_var(r["Vs_AA_Abs"], MC(r["Vs_AA_Abs"])) + _celda_var(r["Vs_AA_Abs"], f"{r['Vs_AA_Pct']:+.1f}%")
    if inc_fc:
        t += f"<td{cb}>{MC(r['Forecast'])}</td>"
        t += _celda_var(r["Vs_Forecast"], MC(r["Vs_Forecast"])) + _celda_var(r["Vs_Forecast"], f"{r['Vs_Forecast_Pct']:+.1f}%")
    clase = " class='subtot'" if subtotal else (" class='totrow'" if total else "")
    return f"<tr{clase}>{t}</tr>"


def _sumas_td(d, gran, inc_fc):
    """Fila de totales de `d`; los % son sobre el total general `gran`."""
    r = {m: d[m].sum() for m in ("Real_2025", "Plan_2026", "Real_2026")}
    if inc_fc:
        r["Forecast"] = d["Forecast"].sum()
    for p, m in (("Pct_r25", "Real_2025"), ("Pct_p26", "Plan_2026"), ("Pct_r26", "Real_2026")):
        r[p] = r[m] / gran[m] * 100 if gran[m] else 0.0
    r["Vs_Plan_Abs"] = r["Real_2026"] - r["Plan_2026"]
    r["Vs_Plan_Pct"] = r["Vs_Plan_Abs"] / abs(r["Plan_2026"]) * 100 if r["Plan_2026"] else 0.0
    r["Vs_AA_Abs"] = r["Real_2026"] - r["Real_2025"]
    r["Vs_AA_Pct"] = r["Vs_AA_Abs"] / abs(r["Real_2025"]) * 100 if r["Real_2025"] else 0.0
    if inc_fc:
        r["Vs_Forecast"] = r["Real_2026"] - r["Forecast"]
        r["Vs_Forecast_Pct"] = r["Vs_Forecast"] / abs(r["Forecast"]) * 100 if r["Forecast"] else 0.0
    return r


def _tabla_td_html(d, dims, etiq, gran, inc_fc, subtotales):
    ths = "".join(f"<th>{etiq[c]}</th>" for c in dims)
    ths += ("<th>Real 2025</th><th>%</th><th>Plan 2026</th><th>%</th><th class='r26-head'>Real 2026</th><th>%</th>"
            "<th>vs Plan</th><th>vs Plan %</th><th>vs AA</th><th>vs AA %</th>")
    if inc_fc:
        ths += "<th>Forecast Finanzas</th><th>vs Forecast</th><th>vs Forecast %</th>"
    filas = []
    if subtotales and len(dims) > 1:
        for k, sub in d.groupby(dims[0], sort=False, dropna=False):
            filas += [_fila_td_html([r[c] for c in dims], r, inc_fc) for _, r in sub.iterrows()]
            filas.append(_fila_td_html([f"Subtotal: {k}"] + [""] * (len(dims) - 1), _sumas_td(sub, gran, inc_fc), inc_fc, True, subtotal=True))
    else:
        filas = [_fila_td_html([r[c] for c in dims], r, inc_fc) for _, r in d.iterrows()]
    filas.append(_fila_td_html(["TOTAL"] + [""] * (len(dims) - 1), _sumas_td(d, gran, inc_fc), inc_fc, True))
    return f"<table class='dat'><thead><tr>{ths}</tr></thead><tbody>{''.join(filas)}</tbody></table>"


def _periodo_txt(filters, si, sf):
    """Etiqueta del periodo según el modo del filtro global (semana, mes, trimestre o rango)."""
    f = filters or {}
    modo = f.get("modo", "Rango de semanas")
    if modo == "Mes" and f.get("meses"):
        return "Meses: " + ", ".join(NOM_MES[m] for m in f["meses"])
    if modo == "Trimestre" and f.get("trimestres"):
        return "Trimestres: " + ", ".join(f["trimestres"]) + f" (semanas {si}-{sf})"
    if modo == "Semana individual":
        return f"Semana {si}"
    return f"Semanas {si}-{sf}"


def _html_td_pdf(tabla, dims, filters, filtro_valores, si, sf):
    from dash_app.pages.resumen import _PDF_CSS, _PDF_LOGO_B64, _kpis_html_pdf
    etiq = {o["value"]: o["label"] for o in DIMENSIONES_OPTS}
    inc_fc = "Forecast" in tabla.columns
    base = {m: tabla[m].sum() for m in ("Real_2025", "Plan_2026", "Real_2026")}
    gran = _sumas_td(tabla, base, inc_fc)
    kpis = {"Real_2026": gran["Real_2026"], "Plan_2026": gran["Plan_2026"], "Real_2025": gran["Real_2025"],
            "Vs_Plan_Abs": gran["Vs_Plan_Abs"], "Vs_Plan_Pct": gran["Vs_Plan_Pct"],
            "Vs_AA_Abs": gran["Vs_AA_Abs"], "Vs_AA_Pct": gran["Vs_AA_Pct"],
            "Forecast_Cierre": gran.get("Forecast", float("nan")),
            "Vs_Forecast_Abs": gran.get("Vs_Forecast", float("nan")),
            "Vs_Forecast_Pct": gran.get("Vs_Forecast_Pct", float("nan"))}
    if "MES" in dims:
        tabla = tabla.assign(MES=tabla["MES"].map(_nombre_mes_td))
    d1 = dims[0]
    segmentado = len(tabla) > _TD_PDF_UMBRAL_SEG and len(dims) > 1
    if segmentado:
        filas_res = []
        for k, sub in tabla.groupby(d1, sort=False, dropna=False):
            filas_res.append({d1: k, **_sumas_td(sub, gran, inc_fc)})
        res = pd.DataFrame(filas_res)
        cuerpo = f"<h2>Resumen por {etiq[d1]}</h2>" + _tabla_td_html(res, [d1], etiq, gran, inc_fc, False)
        cuerpo += f"<h2 class='pagebreak'>Detalle por {etiq[d1]}</h2>"
        for k, sub in tabla.groupby(d1, sort=False, dropna=False):
            cuerpo += f"<h3>{etiq[d1]}: {k}</h3>" + _tabla_td_html(sub, dims, etiq, gran, inc_fc, False)
    else:
        cuerpo = "<h2>Detalle</h2>" + _tabla_td_html(tabla, dims, etiq, gran, inc_fc, True)

    tm = (filters or {}).get("tipo_monto", "Monto Constante")
    filtros = [f"{etiq.get(k, k)}: {', '.join(_nombre_mes_td(x) if k == 'MES' else str(x) for x in v) if len(v) <= 3 else str(len(v)) + ' seleccionados'}"
               for k, v in _filtros_locales(filters, filtro_valores).items() if k != "canal"]
    nota = (f"<div class='nofilt'>Dimensiones: <b>{' &gt; '.join(etiq[c] for c in dims)}</b>"
            + (f" · Filtros: <b>{' · '.join(filtros)}</b>" if filtros else "") + "</div>")
    logo = f"<img src='data:image/png;base64,{_PDF_LOGO_B64}' alt='Grupo Aurora'>" if _PDF_LOGO_B64 else ""
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><style>{_PDF_CSS}</style></head>
<body>
<div class="enc">{logo}
  <div class="kicker">GRUPO AURORA &middot; PLANEACIÓN FINANCIERA</div>
  <h1>Gasto Canales Grupo Aurora</h1>
  <div class="sub">Reporte Territorial &nbsp;&middot;&nbsp; {_periodo_txt(filters, si, sf)} — 2026 &nbsp;&middot;&nbsp; {tm} &nbsp;&middot;&nbsp; Cifras en Millones de Pesos (MDP)</div>
</div>
{nota}
<h2>Gasto</h2>
{_kpis_html_pdf(kpis)}
{cuerpo}
<div class="pie">Generado el {pd.Timestamp.now():%d/%m/%Y %H:%M} &middot; Dashboard Gasto Canales.</div>
</body></html>"""


def generar_reporte_pdf(filters, dimensiones, filtro_valores):
    """Reporte PDF único (Tabla Dinámica y Resumen). Usa las dimensiones elegidas en Tabla Dinámica
    y los filtros globales (semana/mes/trimestre, grupo, cuenta...). Devuelve (bytes|None, nombre, mensaje)."""
    from dash_app.services.export_service import _registrar_descarga, _registrar_descarga_fallida
    from dash_app.services.pdf_service import html_a_pdf
    nombre = "reporte_territorial.pdf"
    try:
        tabla, si, sf = _df_plano_td(filters, dimensiones, filtro_valores)
        if tabla.empty:
            return None, nombre, "Sin datos para esta combinación de dimensiones/filtros."
        if len(tabla) > _TD_PDF_MAX_FILAS:
            return None, nombre, f"Demasiadas filas para PDF ({len(tabla):,}; máx. {_TD_PDF_MAX_FILAS:,}). Agrega filtros o usa Excel."
        nombre = f"reporte_territorial_sem{si}-{sf}.pdf"
        pdf_bytes = html_a_pdf(_html_td_pdf(tabla, list(dimensiones), filters, filtro_valores, si, sf))
        _registrar_descarga(nombre)
        return pdf_bytes, nombre, f"PDF listo: {len(tabla):,} filas."
    except Exception as e:
        _registrar_descarga_fallida(nombre, str(e))
        return None, nombre, f"Error al generar el PDF: {e}"


@callback(
    Output("td-download-pdf", "data"),
    Output("td-pdf-status", "children"),
    Input("td-btn-pdf", "n_clicks"),
    State("global-filter-store", "data"),
    State("td-store-order", "data"),
    State("td-filtro-valores-store", "data"),
    prevent_initial_call=True,
    running=[(Output("td-btn-pdf", "disabled"), True, False),
             (Output("td-btn-pdf", "children"), "Preparando…", [html.Span(className="ico ico-doc"), " PDF"])],
)
def download_tabla_dinamica_pdf(n_clicks, filters, dimensiones, filtro_valores):
    if not n_clicks or not dimensiones:
        return no_update, no_update
    data, nombre, msg = generar_reporte_pdf(filters, dimensiones, filtro_valores)
    if data is None:
        return no_update, msg
    return dcc.send_bytes(data, nombre), msg

