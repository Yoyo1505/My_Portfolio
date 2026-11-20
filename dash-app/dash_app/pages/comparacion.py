# -*- coding: utf-8 -*-
"""
dash_app/pages/comparacion.py
Pestaña Comparación de Versiones — compara el Real 2026 de la versión vigente
contra cualquier semana archivada (snapshot), por Grupo de Cuentas -> Cuenta.
Consulta directa en DuckDB sobre los snapshots crudos r26_semXX.parquet /
p26_semXX.parquet de la carpeta datos/ de ESTE proyecto (copia propia, no se
lee nada del proyecto Streamlit), no sobre `consolidado` ni sobre aggs/.
"""
from dash import html, dcc, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import duckdb
import json
import pandas as pd
from functools import lru_cache
from pathlib import Path
from dash_app.services.data_service import DataService
from dash_app.components.tree_view import create_tree_view_expanded
from dash_app.services.data_service import _normalizar_orden
from dash_app.services.export_service import download_buttons, download_inputs, modo_descarga, enviar_tabla

ds = DataService()
BASE_DIR = Path(__file__).parent.parent.parent.resolve()
_DATOS_ORIG = BASE_DIR / "datos"
_CAT_CUENTAS_XLSX = _DATOS_ORIG / "Catálogo grupo cuentas.xlsx"


def _semanas_disponibles():
    """Semanas con r26_semXX.parquet, de mayor a menor. 2026-09-17: ya NO exige
    que también exista un p26_semXX.parquet con el mismo número — Plan 2026
    solo se re-extrae ~1 vez al mes (Real 2026 cada jueves), así que exigir el
    par exacto dejaba la comparación "atorada" en la última semana en que
    ambas tablas coincidieron (mismo bug ya corregido hoy en el original,
    app.py:_semanas_r26_disponibles, con la misma nota "que no se pasme").
    _p26_snapshot_mas_cercano() resuelve el Plan más cercano para cada semana."""
    if not _DATOS_ORIG.exists():
        return []
    sems = []
    for fp in _DATOS_ORIG.glob("r26_sem*.parquet"):
        try:
            sems.append(int(fp.stem.replace("r26_sem", "")))
        except ValueError:
            continue
    return sorted(sems, reverse=True)


def _p26_snapshot_mas_cercano(sem):
    """Plan 2026 trae el año completo en cada snapshot (el sufijo _semXX solo
    marca cuándo se tomó la copia) — usar el más cercano disponible es seguro:
    exacto si existe; si no, el más reciente <= sem; si tampoco hay ninguno
    anterior, el más reciente que exista en general."""
    exacto = _DATOS_ORIG / f"p26_sem{sem}.parquet"
    if exacto.exists():
        return exacto
    disponibles = []
    for fp in _DATOS_ORIG.glob("p26_sem*.parquet"):
        try:
            disponibles.append((int(fp.stem.replace("p26_sem", "")), fp))
        except ValueError:
            continue
    if not disponibles:
        return None
    anteriores = [t for t in disponibles if t[0] <= sem]
    return max(anteriores or disponibles, key=lambda t: t[0])[1]


def _tiene_monto_local(fp):
    """Los snapshots más viejos solo guardaron Monto Constante, sin columna de
    Monto Local — se detecta por archivo antes de armar la query."""
    con = duckdb.connect(":memory:")
    try:
        cols = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{fp.as_posix()}') LIMIT 0").fetchdf()["column_name"].tolist()
        return "Monto_Local" in cols
    except Exception:
        return False
    finally:
        con.close()


def _catalogo_cuentas():
    # 2026-10-21: pandas en vez de Polars (el tablero ya no usa Polars); mismo resultado que antes.
    cta = pd.read_excel(_CAT_CUENTAS_XLSX, dtype=str).rename(columns={"Cta Mayor": "ID_CONCEPTO_CUENTA_NIV3"})

    def _limpia(s):
        s = s.fillna("").astype(str).str.strip()
        return s.where(s != "", "No Clasificado")

    cta["ID_CONCEPTO_CUENTA_NIV3"] = cta["ID_CONCEPTO_CUENTA_NIV3"].astype(str).str.strip()
    cta["cat_Grupo_de_Cuentas"] = _limpia(cta["Grupo de Cuentas"])
    cta["cat_Cuentas"] = _limpia(cta["Cuentas"])
    cta = cta.drop_duplicates(subset=["ID_CONCEPTO_CUENTA_NIV3"], keep="first")
    return cta[["ID_CONCEPTO_CUENTA_NIV3", "cat_Grupo_de_Cuentas", "cat_Cuentas"]].reset_index(drop=True)


def _in_sql(expr, valores):
    vals = ", ".join("'" + str(v).replace("'", "''") + "'" for v in valores)
    return f"AND {expr} IN ({vals})"


@lru_cache(maxsize=1)
def _cecos_digitales():
    """CECOs digitales (aggs/ceco_digitales.json, lo escribe el build). Los snapshots r26_semNN traen físico + digital;
    el consolidado solo físico, así que sin esto Comparación no cuadraba con el resto (p. ej. Responsable: +614 MDP)."""
    p = Path(__file__).resolve().parent.parent.parent / "aggs" / "ceco_digitales.json"
    return tuple(str(c).strip() for c in json.loads(p.read_text(encoding="utf-8"))) if p.exists() else ()


def _query_snapshot(sem, si, sf, ceco_filtro, tipo_monto="Monto Constante", grupos=None, cuentas=None, canal="Fisico"):
    """Agrega un snapshot archivado por Grupo de Cuentas / Cuenta, en el rango
    de semanas dado y filtrado opcionalmente por CECO. Conexión propia en
    memoria por llamada (no la conexión compartida de DuckDBService) — estos
    snapshots viven fuera de dashboard.duckdb. Devuelve (df, fallback_constante):
    el 2º valor es True si se pidió Monto Local pero el snapshot no lo tiene."""
    r26_fp = _DATOS_ORIG / f"r26_sem{sem}.parquet"
    p26_fp = _p26_snapshot_mas_cercano(sem)
    cols_out = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "Real_2026", "Plan_2026"]
    if not r26_fp.exists() or p26_fp is None:
        return pd.DataFrame(columns=cols_out), False

    quiere_local = tipo_monto == "Monto Local"
    tiene_local = quiere_local and _tiene_monto_local(r26_fp) and _tiene_monto_local(p26_fp)
    fallback_constante = quiere_local and not tiene_local
    monto_col = "Monto_Local" if tiene_local else "SUM_MONTO_MNX_CONS_(SUMA)"
    where_ceco = ""
    if ceco_filtro:
        ceco_list = ", ".join(f"'{str(c)}'" for c in ceco_filtro)
        where_ceco = f"AND CAST(ID_CENTRO_COSTOS AS VARCHAR) IN ({ceco_list})"
    dig = _cecos_digitales()
    if dig and canal in ("Fisico", "Digital"):       # 'Ambos' = sin filtro de canal
        lista = ", ".join(f"'{c}'" for c in dig)
        where_ceco += f" AND TRIM(CAST(ID_CENTRO_COSTOS AS VARCHAR)) {'NOT IN' if canal == 'Fisico' else 'IN'} ({lista})"
    # Filtros globales de Grupo y Cuenta (Responsable ya viene traducido a cuentas en fg_cta).
    # Subtipo no existe en los snapshots archivados, no se puede aplicar aquí.
    if grupos:
        where_ceco += " " + _in_sql("COALESCE(cat.cat_Grupo_de_Cuentas, 'No Clasificado')", grupos)
    if cuentas:
        where_ceco += " " + _in_sql("COALESCE(cat.cat_Cuentas, 'No Clasificado')", cuentas)

    con = duckdb.connect(":memory:")
    try:
        con.register("cat", _catalogo_cuentas())

        def _una(fp, col_out):
            return con.execute(f"""
                SELECT COALESCE(cat.cat_Grupo_de_Cuentas, 'No Clasificado') AS cat_Grupo_de_Cuentas,
                       COALESCE(cat.cat_Cuentas, 'No Clasificado') AS cat_Cuentas,
                       SUM(r."{monto_col}") AS {col_out}
                FROM read_parquet('{fp.as_posix()}') r
                LEFT JOIN cat ON CAST(r.ID_CONCEPTO_CUENTA_NIV3 AS VARCHAR) = cat.ID_CONCEPTO_CUENTA_NIV3
                WHERE r.SEMANA_ARG BETWEEN {int(si)} AND {int(sf)}
                {where_ceco}
                GROUP BY 1, 2
            """).fetch_df()

        real = _una(r26_fp, "Real_2026")
        plan = _una(p26_fp, "Plan_2026")
    finally:
        con.close()

    out = real.merge(plan, on=["cat_Grupo_de_Cuentas", "cat_Cuentas"], how="outer").fillna(0.0)
    return out[cols_out], fallback_constante


_SEMS_DISP = _semanas_disponibles()


def layout():
    if not _SEMS_DISP:
        return dbc.Alert(
            f"No se encontraron snapshots archivados (r26_semXX.parquet) en {_DATOS_ORIG}.", color="warning"
        )
    sem_actual = _SEMS_DISP[0]
    opciones_cmp = [s for s in _SEMS_DISP if s != sem_actual]

    return html.Div(
        children=[
            html.Div(
                className="p-3 bg-white border rounded shadow-sm",
                style={"marginBottom": "20px"},
                children=[
                    html.H5("Comparación de Versiones", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "4px"}),
                    html.Hr(style={"margin": "12px 0"}),
                    dbc.Row(
                        children=[
                            dbc.Col(width=6, children=[
                                html.Label("Versión actual (dashboard)", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                                dcc.Input(value=f"Semana {sem_actual}", disabled=True, className="form-control", style={"backgroundColor": "#f1f3f6"})
                            ]),
                            dbc.Col(width=6, children=[
                                html.Label("Comparar contra versión", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                                dcc.Dropdown(
                                    id="cmp-sem-comparar",
                                    options=[{"label": f"Semana {s}", "value": s} for s in opciones_cmp],
                                    value=opciones_cmp[0] if opciones_cmp else None,
                                    clearable=False
                                )
                            ])
                        ]
                    ),
                    html.Div(style={"marginTop": "14px"}, children=[
                        html.Label("Rango de semanas (acumulado)", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                        dcc.RangeSlider(id="cmp-rango-semanas", min=1, max=53, step=1, value=[1, 53],
                                        marks={1: "1", 13: "Q1", 26: "Q2", 39: "Q3", 53: "53"},
                                        tooltip={"placement": "bottom"})
                    ]),
                    html.Div(style={"marginTop": "14px"}, children=[
                        html.Label("Filtrar por CECO (opcional)", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                        dcc.Dropdown(
                            id="cmp-filtro-ceco",
                            options=[{"label": f"{k} · {v}" if v else f"CECO {k}", "value": str(k)} for k, v in list(ds.ceco_nombres.items())[:3000]],
                            multi=True,
                            placeholder="Todos los CECO"
                        )
                    ])
                ]
            ),
            dcc.Store(id="cmp-sem-actual", data=sem_actual),
            dcc.Download(id="cmp-download"),
            dcc.Loading(type="circle", color="#0071e3", children=html.Div(id="comparacion-tree-container")),
            html.Div(download_buttons("cmp-btn-download"), style={"marginTop": "12px"})
        ]
    )


@callback(
    Output("cmp-rango-semanas", "value"),
    Input("global-filter-store", "data"),
)
def sincronizar_rango_con_global(filters):
    """El rango de semanas del filtro global manda; el slider de esta pestaña queda como ajuste fino."""
    f = filters or {}
    return [int(f.get("si", 1)), int(f.get("sf", 53))]


@callback(
    Output("comparacion-tree-container", "children"),
    Input("global-filter-store", "data"),
    Input("cmp-sem-actual", "data"),
    Input("cmp-sem-comparar", "value"),
    Input("cmp-rango-semanas", "value"),
    Input("cmp-filtro-ceco", "value")
)
def update_comparacion(filters, sem_actual, sem_cmp, rango, ceco_filtro):
    if sem_cmp is None:
        return dbc.Alert("No hay otra versión archivada para comparar todavía.", color="info")

    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    si, sf = rango if rango else (1, 53)
    ceco_tupla = tuple(ceco_filtro) if ceco_filtro else None
    grupos = tuple((filters or {}).get("fg_gpo") or ())
    cuentas = tuple((filters or {}).get("fg_cta") or ())

    canal = (filters or {}).get("canal") or "Fisico"
    d_act, fb_act = _query_snapshot(sem_actual, si, sf, ceco_tupla, tipo_monto=tm, grupos=grupos, cuentas=cuentas, canal=canal)
    d_cmp, fb_cmp = _query_snapshot(sem_cmp, si, sf, ceco_tupla, tipo_monto=tm, grupos=grupos, cuentas=cuentas, canal=canal)

    if d_act.empty and d_cmp.empty:
        return dbc.Alert("Sin datos para esta combinación de versión/semanas/CECO.", color="warning")

    aviso_local = None
    if fb_act or fb_cmp:
        aviso_local = html.P(
            "Aviso: Monto Local no está archivado en snapshot(s) antiguos — se muestra Monto Constante para esa(s) versión(es).",
            style={"fontSize": "0.8rem", "color": "#b45309", "fontWeight": "600"}
        )

    # Alinear ambas versiones por Grupo+Cuenta para que cada árbol tenga la
    # versión contraria en columna "Real 2025" (baseline "vs AA").
    merged = d_act.rename(columns={"Real_2026": "Real_2026_act", "Plan_2026": "Plan_2026_act"}).merge(
        d_cmp.rename(columns={"Real_2026": "Real_2026_cmp", "Plan_2026": "Plan_2026_cmp"}),
        on=["cat_Grupo_de_Cuentas", "cat_Cuentas"], how="outer"
    ).fillna(0.0)
    merged = merged[merged["cat_Grupo_de_Cuentas"].notna() & (merged["cat_Grupo_de_Cuentas"] != "nan")].copy()

    # Árbol 1 — versión vigente: Real_2026=actual, Real_2025=comparada (baseline)
    df_act = merged.rename(columns={
        "Real_2026_cmp": "Real_2025",
        "Plan_2026_act": "Plan_2026",
        "Real_2026_act": "Real_2026"
    })[["cat_Grupo_de_Cuentas", "cat_Cuentas", "Real_2025", "Plan_2026", "Real_2026"]]
    totals_act = {"Real_2025": df_act["Real_2025"].sum(), "Plan_2026": df_act["Plan_2026"].sum(), "Real_2026": df_act["Real_2026"].sum()}

    # Árbol 2 — versión archivada: Real_2026=comparada, Real_2025=actual (baseline)
    df_cmp = merged.rename(columns={
        "Real_2026_act": "Real_2025",
        "Plan_2026_cmp": "Plan_2026",
        "Real_2026_cmp": "Real_2026"
    })[["cat_Grupo_de_Cuentas", "cat_Cuentas", "Real_2025", "Plan_2026", "Real_2026"]]
    totals_cmp = {"Real_2025": df_cmp["Real_2025"].sum(), "Plan_2026": df_cmp["Plan_2026"].sum(), "Real_2026": df_cmp["Real_2026"].sum()}

    children = []
    if aviso_local:
        children.append(aviso_local)

    children.append(html.Div(
        className="p-3 bg-white border rounded shadow-sm",
        style={"marginBottom": "16px"},
        children=[
            html.H6(f"Versión vigente — Semana {sem_actual}", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "2px"}),
            html.P(f"\"Real 2025\" = Semana {sem_cmp} (comparación) · \"Real 2026\" = Semana {sem_actual} (vigente)",
                   style={"fontSize": "0.78rem", "color": "#64748b", "fontStyle": "italic", "marginBottom": "8px"}),
            html.Div(className="vt-cmp-full", children=create_tree_view_expanded(
                "cmp-tree-act", df_act, ["cat_Grupo_de_Cuentas", "cat_Cuentas"], total_dict=totals_act, abierto=False))
        ]
    ))
    children.append(html.Div(
        className="p-3 bg-white border rounded shadow-sm",
        children=[
            html.H6(f"Versión archivada — Semana {sem_cmp}", style={"fontWeight": "700", "color": "#1e2430", "marginBottom": "2px"}),
            html.P(f"\"Real 2025\" = Semana {sem_actual} (vigente) · \"Real 2026\" = Semana {sem_cmp} (archivada)",
                   style={"fontSize": "0.78rem", "color": "#64748b", "fontStyle": "italic", "marginBottom": "8px"}),
            html.Div(className="vt-cmp-full", children=create_tree_view_expanded(
                "cmp-tree-cmp", df_cmp, ["cat_Grupo_de_Cuentas", "cat_Cuentas"], total_dict=totals_cmp, abierto=False))
        ]
    ))
    return html.Div(children)


def _tabla_descarga(d_act, d_cmp, lbl_act, lbl_cmp):
    """Mismo árbol de la pantalla, plano para Excel (paridad con `_comp_tabla_descarga` del
    Streamlit, 2026-09-24): fila de Grupo (Cuenta vacía) seguida de sus Cuentas, Grupos por
    orden de presentación, Cuentas alfabéticas, y TOTAL al final."""
    m = d_act.rename(columns={"Real_2026": "Real_2026_act"}).merge(
        d_cmp.rename(columns={"Real_2026": "Real_2026_cmp"})[["cat_Grupo_de_Cuentas", "cat_Cuentas", "Real_2026_cmp"]],
        on=["cat_Grupo_de_Cuentas", "cat_Cuentas"], how="outer").fillna(0.0)
    c_act, c_cmp = f"Real {lbl_act}", f"Real {lbl_cmp}"

    def _fila(grupo, cuenta, plan, act, cmp_):
        var = act - cmp_
        return {"Grupo de Cuentas": grupo, "Cuenta": cuenta, "Plan 2026": plan, c_act: act, c_cmp: cmp_,
                "Variación": var, "Variación %": (var / abs(cmp_) * 100) if cmp_ else 0.0}

    grupos = m.groupby("cat_Grupo_de_Cuentas", as_index=False).agg(
        Plan_2026=("Plan_2026", "sum"), Real_2026_act=("Real_2026_act", "sum"), Real_2026_cmp=("Real_2026_cmp", "sum"))
    grupos = grupos.reindex(grupos["cat_Grupo_de_Cuentas"].map(ds.orden_gpo_key).sort_values(kind="stable").index)
    filas = []
    for _, g in grupos.iterrows():
        filas.append(_fila(g["cat_Grupo_de_Cuentas"], "", g["Plan_2026"], g["Real_2026_act"], g["Real_2026_cmp"]))
        hijos = m[m["cat_Grupo_de_Cuentas"] == g["cat_Grupo_de_Cuentas"]]
        hijos = hijos.reindex(hijos["cat_Cuentas"].map(_normalizar_orden).sort_values(kind="stable").index)
        for _, h in hijos.iterrows():
            filas.append(_fila(g["cat_Grupo_de_Cuentas"], h["cat_Cuentas"], h["Plan_2026"],
                               h["Real_2026_act"], h["Real_2026_cmp"]))
    filas.append(_fila("TOTAL", "", m["Plan_2026"].sum(), m["Real_2026_act"].sum(), m["Real_2026_cmp"].sum()))
    return pd.DataFrame(filas)


@callback(
    Output("cmp-download", "data"),
    *download_inputs("cmp-btn-download"),
    State("global-filter-store", "data"),
    State("cmp-sem-actual", "data"),
    State("cmp-sem-comparar", "value"),
    State("cmp-rango-semanas", "value"),
    State("cmp-filtro-ceco", "value"),
    prevent_initial_call=True,
)
def download_comparacion(_n_num, _n_conta, filters, sem_actual, sem_cmp, rango, ceco_filtro):
    contable = modo_descarga()
    if contable is None or sem_cmp is None:
        return no_update
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
    si, sf = rango if rango else (1, 53)
    ceco_tupla = tuple(ceco_filtro) if ceco_filtro else None
    d_act, _ = _query_snapshot(sem_actual, si, sf, ceco_tupla, tipo_monto=tm)
    d_cmp, _ = _query_snapshot(sem_cmp, si, sf, ceco_tupla, tipo_monto=tm)
    if d_act.empty and d_cmp.empty:
        return no_update
    extra = f" · sem {si}-{sf}" + (f" · {len(ceco_tupla)} CECO(s)" if ceco_tupla else "")
    df = _tabla_descarga(d_act, d_cmp, f"{sem_actual}{extra}", f"{sem_cmp}{extra}")
    return enviar_tabla(df, f"comparacion_versiones_sem{sem_actual}_vs_sem{sem_cmp}", contable)
