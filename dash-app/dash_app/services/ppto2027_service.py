# -*- coding: utf-8 -*-
"""
dash_app/services/ppto2027_service.py

Integra el tablero de "E:\\Desarrollos\\Ppto 2027" (Presupuesto Canales 2026,
Streamlit) dentro de este dashboard Dash, como una página más y como fuente de
datos para Cashi.

IMPORTANTE: no se reinventa la lógica de agregación. Este módulo lee de
`aggs/ppto2027/` — una copia LOCAL, autocontenida, de los parquet ya
generados por el proyecto Ppto 2027 real (`generar_agregados.py` +
`catalogos.py`, aggs/agg_full.parquet + catálogos) — y replica tal cual el
join final de `Ppto 2027/app.py:cargar_agregados()` (líneas 359-394). Así los
números coinciden exactamente con los que ve el usuario en Ppto 2027
(Streamlit), sin depender de esa carpeta en tiempo de ejecución.

La copia se refresca a mano: robocopy "E:\\Desarrollos\\Ppto 2027\\aggs"
"aggs\\ppto2027" /E /XO — Ppto 2027 se actualiza sobre todo los jueves con la
descarga de SAP.
"""
from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pandas as pd

from dash_app.services.cache_ligera import memo

AGGS_DIR = Path(__file__).resolve().parent.parent.parent / "aggs" / "ppto2027"  # copia local

METRICAS_V1 = ["Reglas_2026", "Fcst_Oficial_2026", "Ppto_2026", "Ordinario_2026"]
# Nombres que usa la vista de referencia (Streamlit) para cada métrica.
ETIQUETAS_METRICA = {
    "Reglas_2026": "PLAN",
    "Fcst_Oficial_2026": "FCST",
    "Ppto_2026": "COMPARABILIDAD",
    "Ordinario_2026": "CONTA",
}

_CACHE: dict = {"df": None, "ts": 0.0}
_CACHE_TTL_SEG = 600


def cargar_agregados_ppto(forzar: bool = False) -> pd.DataFrame:
    """Réplica exacta del join de Ppto 2027/app.py:cargar_agregados(), sobre
    la copia local de aggs/, devuelto como pandas."""
    ahora = time.time()
    if not forzar and _CACHE["df"] is not None and (ahora - _CACHE["ts"]) < _CACHE_TTL_SEG:
        return _CACHE["df"]

    # 2026-10-21: los joins van en DuckDB (el tablero ya no usa Polars). Mismo resultado: LEFT JOIN a los 3
    # catálogos, Cuentas = COALESCE(agg, catálogo) y el parche de Luz (PosicionPres 620151 = canal Físicos).
    p = (AGGS_DIR).as_posix()
    con = duckdb.connect(":memory:")
    try:
        df_pd = con.execute(f"""
            SELECT a.* REPLACE (COALESCE(a.Cuentas, c.Cuentas) AS Cuentas),
                   s.SERVICIO, s.DESPACHO, s.RESPONSABLE, s.CONTACTO_DIRECTOR, s.CONTACTO_DE_CARGA,
                   c.Denom_PosPre, c.Agrupador_Reales, c.Orden_Presentacion_Gpo,
                   CASE WHEN a.PosicionPres = 620151 THEN 'Físicos' ELSE g.AGRUPA_CANALES END AS AGRUPA_CANALES
            FROM read_parquet('{p}/agg_full.parquet') a
            LEFT JOIN read_parquet('{p}/cat_servicios.parquet') s ON a.Detalle2 = s.Detalle2
            LEFT JOIN read_parquet('{p}/cat_cuentas.parquet') c ON a.PosicionPres = c.PosicionPres
            LEFT JOIN read_parquet('{p}/cat_agrupa_canales.parquet') g ON a.CECO_N7 = g.CECO_N7
        """).fetchdf()
    finally:
        con.close()
    _CACHE["df"] = df_pd
    _CACHE["ts"] = ahora
    return df_pd


def opciones(col: str) -> list:
    df = cargar_agregados_ppto()
    if col not in df.columns:
        return []
    return sorted(x for x in df[col].dropna().unique().tolist())


def aplicar_filtros(df: pd.DataFrame, meses=None, semana_rango=None, cecos=None, cuentas=None) -> pd.DataFrame:
    """Filtros reales sobre el DataFrame ya cargado — intervalo de tiempo
    (Mes/Semana), centros de costo (CECO_N7/CECO_N8) e ID_CUENTAS (Cuentas)."""
    out = df
    if meses:
        out = out[out["Mes"].isin(meses)]
    if semana_rango:
        smin, smax = semana_rango
        out = out[(out["Semana"] >= smin) & (out["Semana"] <= smax)]
    if cecos:
        out = out[out["CECO_N7"].isin(cecos) | out["CECO_N8"].isin(cecos)]
    if cuentas:
        out = out[out["Cuentas"].isin(cuentas)]
    return out


def kpis_resumen(meses=None, semana_rango=None, cecos=None, cuentas=None) -> dict:
    df = aplicar_filtros(cargar_agregados_ppto(), meses, semana_rango, cecos, cuentas)
    return {m: float(df[m].sum()) if m in df.columns else 0.0 for m in METRICAS_V1}


def _montos_row(r) -> dict:
    return {m: (None if pd.isna(r[m]) else float(r[m])) for m in METRICAS_V1}


@memo(max_entradas=16, ttl_seg=600)      # 10 min: el agregado base de este módulo también se refresca cada 10 min (y todo se vacía cada 15)
def construir_arbol(meses=None, semana_rango=None, cecos=None, cuentas=None) -> dict:
    """Réplica EXACTA (mismas ramas, misma profundidad) de la lógica de
    Ppto 2027/app.py (construir_posiciones_html / construir_despachos_html /
    construir_servicios_html / construir_responsables_html, líneas 947-1099):
    algunos niveles se SALTAN cuando no aportan información (una sola Cuenta
    real, un Despacho con el mismo nombre que la Posición, sin Servicio/
    Responsable capturado, etc.) — no es una jerarquía fija de 6 niveles.
    Devuelve {"nodos": [...], "total": {...}} listo para renderizar."""
    df = aplicar_filtros(cargar_agregados_ppto(), meses, semana_rango, cecos, cuentas)

    def agg(cols):
        if df.empty:
            return pd.DataFrame(columns=[*cols, *METRICAS_V1])
        return df.groupby(cols, dropna=False)[METRICAS_V1].sum().reset_index()

    df_gpo = agg(["GrupoCuenta"])
    df_cta = agg(["GrupoCuenta", "Cuentas"])
    df_pos = agg(["GrupoCuenta", "Cuentas", "PosicionDesc"])
    df_desp = agg(["GrupoCuenta", "Cuentas", "PosicionDesc", "DESPACHO"])
    df_srv = agg(["GrupoCuenta", "Cuentas", "PosicionDesc", "DESPACHO", "SERVICIO"])
    df_resp = agg(["GrupoCuenta", "Cuentas", "PosicionDesc", "DESPACHO", "SERVICIO", "RESPONSABLE"])

    def _o(v):
        return None if pd.isna(v) else v

    dict_cta, dict_pos, dict_desp, dict_srv, dict_resp = {}, {}, {}, {}, {}
    for _, r in df_cta.iterrows():
        dict_cta.setdefault(r["GrupoCuenta"], {})[_o(r["Cuentas"])] = _montos_row(r)
    for _, r in df_pos.iterrows():
        dict_pos.setdefault((r["GrupoCuenta"], _o(r["Cuentas"])), {})[_o(r["PosicionDesc"])] = _montos_row(r)
    for _, r in df_desp.iterrows():
        dict_desp.setdefault((r["GrupoCuenta"], _o(r["Cuentas"]), _o(r["PosicionDesc"])), {})[_o(r["DESPACHO"])] = _montos_row(r)
    for _, r in df_srv.iterrows():
        dict_srv.setdefault((r["GrupoCuenta"], _o(r["Cuentas"]), _o(r["PosicionDesc"]), _o(r["DESPACHO"])), {})[_o(r["SERVICIO"])] = _montos_row(r)
    for _, r in df_resp.iterrows():
        dict_resp.setdefault((r["GrupoCuenta"], _o(r["Cuentas"]), _o(r["PosicionDesc"]), _o(r["DESPACHO"]), _o(r["SERVICIO"])), {})[_o(r["RESPONSABLE"])] = _montos_row(r)

    def _tiene_valor(montos: dict) -> bool:
        return any(v for v in montos.values())

    def _nodo(label, montos, path, hijos):
        return {"label": label, "montos": montos, "path": path, "hijos": hijos or None}

    def responsables(grupo, cuenta, pos, desp, srv, path_base):
        resp_dict = dict_resp.get((grupo, cuenta, pos, desp, srv), {})
        ordenados = sorted(resp_dict.items(), key=lambda x: sum(abs(v or 0) for v in x[1].values()), reverse=True)
        return [
            _nodo(lbl if lbl is not None else "(Sin Responsable Asociado)", val, [*path_base, ("RESPONSABLE", lbl)], None)
            for lbl, val in ordenados if lbl is not None or _tiene_valor(val)
        ]

    def servicios(grupo, cuenta, pos, desp, path_base):
        srv_dict = dict_srv.get((grupo, cuenta, pos, desp), {})
        ordenados = sorted(srv_dict.items(), key=lambda x: sum(abs(v or 0) for v in x[1].values()), reverse=True)
        nodos = []
        for lbl, val in ordenados:
            if lbl is None and not _tiene_valor(val):
                continue
            etiqueta = lbl if lbl is not None else "(Sin Servicio Asociado)"
            path = [*path_base, ("SERVICIO", lbl)]
            resp_dict = dict_resp.get((grupo, cuenta, pos, desp, lbl), {})
            resp_reales = {k: v for k, v in resp_dict.items() if k is not None}
            if resp_reales:
                nodos.append(_nodo(etiqueta, val, path, responsables(grupo, cuenta, pos, desp, lbl, path)))
            else:
                nodos.append(_nodo(etiqueta, val, path, None))
        return nodos

    def despachos(grupo, cuenta, pos, path_base):
        desp_dict = dict_desp.get((grupo, cuenta, pos), {})
        ordenados = sorted(desp_dict.items(), key=lambda x: sum(abs(v or 0) for v in x[1].values()), reverse=True)
        nodos = []
        for lbl, val in ordenados:
            if lbl is None and not _tiene_valor(val):
                continue
            etiqueta = lbl if lbl is not None else "(Sin Despacho Asociado)"
            path = [*path_base, ("DESPACHO", lbl)]
            srv_dict = dict_srv.get((grupo, cuenta, pos, lbl), {})
            srv_reales = {k: v for k, v in srv_dict.items() if k is not None}
            if srv_reales:
                nodos.append(_nodo(etiqueta, val, path, servicios(grupo, cuenta, pos, lbl, path)))
            else:
                nodos.append(_nodo(etiqueta, val, path, None))
        return nodos

    def posiciones(grupo, cuenta, path_base):
        pos_dict = dict_pos.get((grupo, cuenta), {})
        ordenadas = sorted(pos_dict.items(), key=lambda x: sum(abs(v or 0) for v in x[1].values()), reverse=True)
        nodos = []
        for lbl, val in ordenadas:
            if lbl is None:
                continue
            path = [*path_base, ("PosicionDesc", lbl)]
            desp_dict = dict_desp.get((grupo, cuenta, lbl), {})
            desp_reales = {k: v for k, v in desp_dict.items() if k is not None}
            if len(desp_reales) > 1 or (len(desp_reales) == 1 and (None in desp_dict or list(desp_reales.keys())[0] != lbl)):
                nodos.append(_nodo(lbl, val, path, despachos(grupo, cuenta, lbl, path)))
            elif len(desp_dict) == 1 and list(desp_dict.keys())[0] is not None:
                d_nombre = list(desp_dict.keys())[0]
                if d_nombre != lbl:
                    nodos.append(_nodo(lbl, val, path, despachos(grupo, cuenta, lbl, path)))
                else:
                    nodos.append(_nodo(lbl, val, path, None))
            else:
                nodos.append(_nodo(lbl, val, path, None))
        return nodos

    nodos_raiz = []
    for _, row in df_gpo.iterrows():
        grupo = row["GrupoCuenta"]
        montos_grupo = _montos_row(row)
        path = [("GrupoCuenta", grupo)]

        cuentas_dict = dict_cta.get(grupo, {})
        cuentas_reales = {k: v for k, v in cuentas_dict.items() if k is not None}
        monto_sin_cuenta = cuentas_dict.get(None, {m: 0.0 for m in METRICAS_V1})

        hijos = []
        if len(cuentas_reales) > 1:
            for c_lbl, c_val in sorted(cuentas_reales.items(), key=lambda x: str(x[0])):
                path_c = [*path, ("Cuentas", c_lbl)]
                hijos.append(_nodo(c_lbl, c_val, path_c, posiciones(grupo, c_lbl, path_c)))
        else:
            for c_lbl in cuentas_reales.keys():
                hijos.extend(posiciones(grupo, c_lbl, path))

        if _tiene_valor(monto_sin_cuenta):
            hijos.extend(posiciones(grupo, None, path))

        nodos_raiz.append(_nodo(grupo, montos_grupo, path, hijos))

    total = {m: float(df_gpo[m].sum()) if m in df_gpo.columns else 0.0 for m in METRICAS_V1}
    return {"nodos": nodos_raiz, "total": total}
