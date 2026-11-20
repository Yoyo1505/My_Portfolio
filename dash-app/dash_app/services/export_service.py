# -*- coding: utf-8 -*-
"""
dash_app/services/export_service.py
Descargas a Excel — paridad con `descargar()`/`_excel_bytes()` del Streamlit (2026-09-24).

Cada tabla ofrece 2 botones: "números completos" (`#,##0.00`) y "formato contable"
(gasto positivo entre paréntesis, a favor sin signo, como formato de celda — el valor
sigue siendo un número que se puede sumar/filtrar en Excel). Más de 1,048,575 filas no
caben en una hoja de Excel -> cae a CSV.

Uso en una página:
    layout:    download_buttons("mi-btn"), dcc.Download(id="mi-download")
    callback:  @callback(Output("mi-download","data"), *download_inputs("mi-btn"), State(...))
               def f(n_num, n_conta, ...):
                   contable = modo_descarga()
                   if contable is None: return no_update
                   return enviar_tabla(df, "mi_archivo", contable)
"""
import io
import logging
import threading
import time
from pathlib import Path
from datetime import datetime

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, ctx, dcc, html

EXCEL_MAX_FILAS = 1_048_575
# Latencia medida: xlsxwriter ~0.07 ms/fila (13 columnas) vs CSV ~0.012 ms/fila. Sobre ~100k filas el
# Excel tarda > 7 s (200k = 14 s, 1M = 70 s) y bloquea un hilo de waitress; el CSV tarda 6x menos.
EXCEL_UMBRAL_LATENCIA = 100_000
SUFIJO_CONTABLE = "-contable"

logger = logging.getLogger(__name__)
_audit_lock = threading.Lock()

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
AUDIT_FILE = BASE_DIR / "datos" / "bitacora_descargas.jsonl"


def _col_es_pct(c) -> bool:
    return "%" in str(c) or "pct" in str(c).lower()


def _quien(usuario: str) -> str:
    """Si el llamador no dijo quién descarga, se toma el usuario de la sesión (cookie `vt_usuario`)."""
    if usuario and usuario != "Desconocido":
        return usuario
    from dash_app.services import uso_service
    return uso_service.usuario_actual()


def _registrar_descarga_fallida(nombre_archivo: str, error: str, usuario: str = "Desconocido"):
    """Thread-safe logging de descargas fallidas a bitacora_descargas.jsonl"""
    import json
    evento = {
        "timestamp": datetime.now().isoformat(),
        "usuario": usuario,
        "archivo": nombre_archivo,
        "exitosa": False,
        "error": error
    }
    try:
        with _audit_lock:
            with open(AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(evento) + "\n")
    except Exception as e:
        logger.error(f"No se pudo registrar descarga fallida: {e}")


def _registrar_descarga(nombre_archivo: str, usuario: str = "Desconocido", filas=None, seg=None):
    """Descarga exitosa en bitacora_descargas.jsonl (misma estructura que la fallida)."""
    import json
    try:
        with _audit_lock:
            with open(AUDIT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"timestamp": datetime.now().isoformat(), "usuario": usuario,
                                    "archivo": nombre_archivo, "exitosa": True,
                                    "filas": filas, "seg": None if seg is None else round(seg, 2)}) + "\n")
    except Exception as e:
        logger.error(f"No se pudo registrar descarga: {e}")
    try:
        from dash_app.services import uso_service
        uso_service.registrar("DESCARGA", usuario, detalle=nombre_archivo, ms=None if seg is None else seg * 1000.0, filas=filas)
    except Exception:
        pass


_TITULOS = {
    "alertas_sobregasto": "Alertas de sobregasto", "comparacion_versiones": "Comparación de versiones",
    "cashi_resultado": "Resultado de Cashi", "tabla_dinamica": "Tabla dinámica", "resumen": "Resumen",
    "detalle_cuenta": "Jerarquía contable y agrupadores", "detalle_pdv": "Análisis por puntos de venta",
    "detalle_ceco": "Detalle por CECO", "contraloria": "Desglose por cuentas y contraloría",
    "cierres": "Cierres", "temporalidad": "Temporalidad",
}
_ROJO, _ROJO_T, _TINTA, _GRIS, _LINEA = "#a93226", "#fbeceb", "#1e2430", "#6b7280", "#e6e9ee"


def _titulo_de(nombre: str) -> str:
    base = nombre.lower()
    for k, v in _TITULOS.items():
        if base.startswith(k):
            return v
    return nombre.replace("_", " ").strip().capitalize()


def _filtros_de_peticion() -> list:
    """Filtros globales vigentes al momento de la descarga, leídos de la petición de Dash (el callback de descarga
    recibe `global-filter-store`). Devuelve líneas «Etiqueta: valor»; vacío si no se pueden leer."""
    try:
        from flask import has_request_context, request
        if not has_request_context():
            return []
        body = request.get_json(silent=True) or {}
        gf = None
        for it in list(body.get("state") or []) + list(body.get("inputs") or []):
            for x in (it if isinstance(it, list) else [it]):
                if isinstance(x, dict) and x.get("id") == "global-filter-store":
                    gf = x.get("value")
        if not isinstance(gf, dict):
            return []
        out = []
        if gf.get("si") is not None and gf.get("sf") is not None:
            out.append(f"Semanas: {gf['si']} a {gf['sf']}")
        if gf.get("canal"):
            out.append(f"Canal: {gf['canal']}")
        if gf.get("tipo_monto"):
            out.append(f"Monto: {str(gf['tipo_monto']).replace('Monto ', '')}")
        for k, et in (("fg_resp", "Responsable"), ("fg_gpo", "Grupo de cuentas"), ("fg_subtipo", "Subtipo"), ("fg_cta", "Cuenta")):
            v = gf.get(k)
            if v:
                v = v if isinstance(v, list) else [v]
                out.append(f"{et}: " + ", ".join(str(a) for a in v[:6]) + (f" y {len(v) - 6} más" if len(v) > 6 else ""))
        return out
    except Exception:
        return []


def _es_variacion_col(c) -> bool:
    cl = str(c).lower()
    return any(k in cl for k in ("vs_", "vs ", "var", "desv", "cambio", "dif"))


def excel_bytes(df: pd.DataFrame, contable: bool, titulo: str = None, usuario: str = None, filtros: list = None) -> bytes:
    """Excel con cifras numéricas reales (sin redondear ni pasar a millones), en tres hojas:
    Resumen (qué es, filtros, quién y cuándo), Datos (la tabla, con filtro automático y paneles fijos) y Notas.
    contable=False: `#,##0.00`. contable=True: mismo criterio que MC()/PC() en pantalla (gasto entre paréntesis).
    Hasta 50,000 filas lleva formato fino (líneas, filas alternas, variaciones en color); arriba de eso baja a un
    formato ligero con `constant_memory` para no bloquear un hilo del servidor."""
    try:
        import xlsxwriter
    except ImportError as e:
        raise ImportError("xlsxwriter no está instalado") from e

    try:
        n = len(df)
        fino = n <= 50_000
        buf = io.BytesIO()
        wb = xlsxwriter.Workbook(buf, {"in_memory": True, "constant_memory": not fino})
        base = {"font_name": "Calibri", "font_size": 10, "font_color": _TINTA, "valign": "vcenter"}

        def F(**kw):
            return wb.add_format({**base, **kw})

        ws_r = wb.add_worksheet("Resumen")
        ws = wb.add_worksheet("Datos")
        ws_n = wb.add_worksheet("Notas")

        # ---------------- Resumen ----------------
        ws_r.hide_gridlines(2)
        ws_r.set_column("A:A", 3)
        ws_r.set_column("B:B", 24)
        ws_r.set_column("C:C", 70)
        logo = BASE_DIR / "dash_app" / "assets" / "aurora_logo.png"
        if logo.exists():
            ws_r.insert_image("B2", str(logo), {"x_scale": 0.12, "y_scale": 0.12, "object_position": 2})
        ws_r.set_row(1, 22)
        ws_r.write("C2", "GRUPO AURORA · DIRECCIÓN DE FINANZAS", F(bold=True, font_size=8, font_color=_ROJO))
        ws_r.set_row(2, 28)
        ws_r.write("C3", titulo or "Descarga del tablero", F(bold=True, font_size=18))
        ws_r.set_row(4, 6)
        filas_info = [
            ("Generado", datetime.now().strftime("%Y-%m-%d %H:%M")),
            ("Usuario", usuario or "—"),
            ("Filas en Datos", f"{n:,}"),
            ("Formato de cifras", "Contable: el gasto va entre paréntesis" if contable else "Números completos"),
        ]
        for fl in (filtros or []):
            et, _, val = fl.partition(": ")
            filas_info.append((et, val))
        f_et, f_val = F(bold=True, font_color=_GRIS, bg_color=_ROJO_T, border=1, border_color="#ffffff", indent=1), F(border=1, border_color=_LINEA, indent=1)
        for k, (et, val) in enumerate(filas_info):
            ws_r.set_row(5 + k, 20)
            ws_r.write(5 + k, 1, et, f_et)
            ws_r.write(5 + k, 2, val, f_val)
        ws_r.set_landscape()
        ws_r.fit_to_pages(1, 0)

        # ---------------- Datos ----------------
        f_head = F(bold=True, font_color=_ROJO, bg_color=_ROJO_T, bottom=2, bottom_color=_ROJO, text_wrap=True, align="center")
        f_head_txt = F(bold=True, font_color=_ROJO, bg_color=_ROJO_T, bottom=2, bottom_color=_ROJO, text_wrap=True, align="left")
        borde = {"bottom": 1, "bottom_color": _LINEA}
        nf_money = r'\(#,##0.00\);#,##0.00;#,##0.00' if contable else "#,##0.00"
        nf_pct = r'\(#,##0.0"%"\);#,##0.0"%";#,##0.0"%"' if contable else '#,##0.00"%"'
        cache = {}

        def fmt_cuerpo(tipo, alterna):
            k = (tipo, alterna)
            if k not in cache:
                kw = dict(borde)
                if alterna:
                    kw["bg_color"] = "#fafbfc"
                if tipo == "money":
                    kw["num_format"] = nf_money
                elif tipo == "pct":
                    kw["num_format"] = nf_pct
                elif tipo == "int":
                    kw["num_format"] = "#,##0"
                cache[k] = F(**kw)
            return cache[k]

        tipos, anchos = [], []
        for j, c in enumerate(df.columns):
            s = df[c]
            if pd.api.types.is_float_dtype(s):
                t = "pct" if _col_es_pct(c) else "money"
            elif pd.api.types.is_integer_dtype(s) and not pd.api.types.is_bool_dtype(s):
                t = "int"
            else:
                t = "text"
            tipos.append(t)
            if t == "text":
                largo = s.astype(str).str.len().head(2000).max() if n else 10
                anchos.append(min(max(largo, len(str(c))) + 3, 60))
            else:
                anchos.append(max(16, len(str(c)) + 3))

        ws.set_row(0, 30)
        for j, c in enumerate(df.columns):
            ws.write(0, j, str(c), f_head_txt if tipos[j] == "text" else f_head)
            ws.set_column(j, j, anchos[j], None if fino else fmt_cuerpo(tipos[j], False))
        ws.freeze_panes(1, 1 if (tipos and tipos[0] == "text") else 0)
        if n:
            ws.autofilter(0, 0, n, max(len(df.columns) - 1, 0))

        datos = df.astype(object).where(df.notna(), None)
        for c in df.columns:                      # fechas -> texto: write() no formatea Timestamp sin formato de fecha
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                datos[c] = df[c].dt.strftime("%Y-%m-%d").where(df[c].notna(), None)
        vals = datos.to_numpy().tolist()
        if fino:
            for i, fila in enumerate(vals, start=1):
                alt = bool(i % 2 == 0)
                for j, v in enumerate(fila):
                    ws.write(i, j, v, fmt_cuerpo(tipos[j], alt))
            # variaciones: positivo (más gasto que la referencia) en rojo, negativo (ahorro) en verde
            for j, c in enumerate(df.columns):
                if tipos[j] in ("money", "pct") and _es_variacion_col(c) and n:
                    ws.conditional_format(1, j, n, j, {"type": "cell", "criteria": ">", "value": 0, "format": wb.add_format({"font_color": "#b3261e"})})
                    ws.conditional_format(1, j, n, j, {"type": "cell", "criteria": "<", "value": 0, "format": wb.add_format({"font_color": "#1e7d43"})})
        else:
            # write_row: ~20 % menos latencia que celda por celda (medido 2026-10-02: 50k filas 4.7 -> 3.7 s).
            # El formato numérico sale del set_column de arriba; None queda como celda vacía.
            for i, fila in enumerate(vals, start=1):
                ws.write_row(i, 0, fila)
        ws.set_landscape()
        ws.set_paper(9)
        ws.fit_to_pages(1, 0)
        ws.repeat_rows(0)
        ws.set_footer("&L&8Grupo Aurora · Dirección de Finanzas&C&8&A&R&8Página &P de &N")

        # ---------------- Notas ----------------
        ws_n.hide_gridlines(2)
        ws_n.set_column("A:A", 3)
        ws_n.set_column("B:B", 110)
        ws_n.write("B2", "Notas", F(bold=True, font_size=14))
        notas = [
            "Las cifras son números reales (se pueden sumar, filtrar y graficar); no están redondeadas ni pasadas a millones.",
            "Contable: el gasto aparece entre paréntesis y lo que es a favor, sin signo. Números completos: tal como se calcula." if contable
            else "Números completos: tal como se calculan. Hay otra descarga en formato contable (gasto entre paréntesis).",
            "Variaciones: positivo = más gasto que la referencia (rojo); negativo = ahorro (verde).",
            "Los filtros aplicados al momento de descargar están en la hoja Resumen.",
            "La hoja Datos tiene filtro automático y la primera fila fija; al imprimir se ajusta a una hoja de ancho y repite el encabezado.",
        ]
        if not fino:
            notas.append(f"Esta tabla tiene {n:,} filas: se generó con formato ligero (sin líneas, filas alternas ni colores) para no tardar de más.")
        for k, t in enumerate(notas):
            ws_n.write(3 + k, 1, "•  " + t, F(text_wrap=True, valign="top"))
            ws_n.set_row(3 + k, 30)

        ws_r.activate()
        wb.close()
        return buf.getvalue()
    except Exception as e:
        logger.error(f"Error generando Excel: {e}")
        raise


def _contable_txt(v, pct: bool) -> str:
    if pd.isna(v):
        return ""
    txt = f"{abs(v):,.1f}%" if pct else f"{abs(v):,.2f}"
    return f"({txt})" if v > 0 else txt


def enviar_tabla(df: pd.DataFrame, nombre: str, contable: bool = False, usuario: str = "Desconocido", titulo: str = None, filtros: list = None):
    """Envía tabla Excel/CSV con error handling. Remueve Nuevo Plan columns."""
    try:
        t0 = time.perf_counter()
        d = df.copy()
        n = len(d)

        if n > EXCEL_UMBRAL_LATENCIA:
            if contable:
                for c in d.columns:
                    if pd.api.types.is_float_dtype(d[c]):
                        d[c] = d[c].map(lambda v, p=_col_es_pct(c): _contable_txt(v, p))
                _registrar_descarga(f"{nombre}_contable.csv", usuario, n, time.perf_counter() - t0)
                return dcc.send_data_frame(d.to_csv, f"{nombre}_contable.csv", index=False, encoding="utf-8-sig")
            _registrar_descarga(f"{nombre}.csv", usuario, n, time.perf_counter() - t0)
            return dcc.send_data_frame(d.to_csv, f"{nombre}.csv", index=False, float_format="%.2f", encoding="utf-8-sig")

        sufijo = "_contable" if contable else ""
        usuario = _quien(usuario)
        datos = excel_bytes(d, contable, titulo or _titulo_de(nombre), usuario, _filtros_de_peticion() if filtros is None else filtros)
        _registrar_descarga(f"{nombre}{sufijo}.xlsx", usuario, n, time.perf_counter() - t0)
        return dcc.send_bytes(datos, f"{nombre}{sufijo}.xlsx")
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error descargando {nombre}: {error_msg}")
        _registrar_descarga_fallida(nombre, error_msg, usuario)
        raise


def tabla_descarga(df: pd.DataFrame, gcols: list) -> pd.DataFrame:
    """Tabla plana para descarga, mismas columnas que el Excel del Streamlit (`_agregar_df`):
    agrega Real_2025/Plan_2026/Real_2026 por `gcols` (el df puede venir a nivel semana/CECO) y
    calcula % del total, vs Plan y vs AA. Orden: Grupo de Cuentas por "Orden presentación Gpo. Cta"
    con Cuentas alfabéticas dentro del Grupo; si no hay Grupo/Cuenta, mayor |Real_2026| primero."""
    import numpy as np
    from dash_app.services.data_service import DataService, _normalizar_orden

    ds = DataService()
    meds = ["Real_2025", "Plan_2026", "Real_2026"]
    for m in meds:
        if m not in df.columns:
            df[m] = 0.0
    con_fc = "Forecast" in df.columns
    if con_fc:
        meds = meds + ["Forecast"]

    d = df.copy()

    g = d.groupby(gcols, as_index=False, dropna=False)[meds].sum()
    g = g[g[meds].abs().sum(axis=1) > 0.5].copy()
    tot = {m: g[m].sum() for m in meds}
    for m, p in (("Real_2025", "Pct_r25"), ("Plan_2026", "Pct_p26"), ("Real_2026", "Pct_r26")):
        g[p] = (g[m] / tot[m] * 100) if tot[m] else 0.0
    g["Vs_Plan_Abs"] = g["Real_2026"] - g["Plan_2026"]
    g["Vs_Plan_Pct"] = np.where(g["Plan_2026"] != 0, g["Vs_Plan_Abs"] / g["Plan_2026"].abs() * 100, 0.0)
    g["Vs_AA_Abs"] = g["Real_2026"] - g["Real_2025"]
    g["Vs_AA_Pct"] = np.where(g["Real_2025"] != 0, g["Vs_AA_Abs"] / g["Real_2025"].abs() * 100, 0.0)
    if con_fc:
        g["Vs_Forecast"] = g["Real_2026"] - g["Forecast"]
        g["Vs_Forecast_Pct"] = np.where(g["Forecast"] != 0, g["Vs_Forecast"] / g["Forecast"].abs() * 100, 0.0)

    if "cat_Grupo_de_Cuentas" in gcols:
        clave = g.apply(lambda r: ds.orden_gpo_key(r["cat_Grupo_de_Cuentas"]) + (
            _normalizar_orden(r["cat_Cuentas"]) if "cat_Cuentas" in gcols else "", -abs(r["Real_2026"])), axis=1)
        g = g.reindex(clave.sort_values(kind="stable").index)
    elif gcols[0] == "cat_Cuentas":
        clave = g["cat_Cuentas"].map(ds.orden_cuenta_por_gpo_key)
        g = g.reindex(clave.sort_values(kind="stable").index)
    else:
        g = g.reindex(g["Real_2026"].abs().sort_values(ascending=False).index)
    return g.reset_index(drop=True)


def download_buttons(btn_id: str, **btn_kwargs):
    """Par de botones (números completos / formato contable). El id del primero es `btn_id`,
    el del segundo `btn_id + '-contable'`. `btn_kwargs` (size, color, outline, className,
    style...) se aplican a ambos."""
    kw = {"color": "secondary", "outline": True, "size": "sm"}
    kw.update(btn_kwargs)
    estilo = dict(kw.pop("style", None) or {})
    return html.Span(
        className="vt-dl-btns",
        style={"display": "inline-flex", "gap": "6px", **estilo},
        children=[
            dbc.Button([html.Span(className="ico ico-download"), " Excel"], id=btn_id, title="Descargar Excel · números completos", **kw),
            dbc.Button([html.Span(className="ico ico-doc"), " Excel"], id=btn_id + SUFIJO_CONTABLE, title="Descargar Excel · formato contable", **kw),
        ],
    )


def download_inputs(btn_id: str):
    """Los 2 `Input(..., 'n_clicks')` a splatear en el decorador del callback."""
    return [Input(btn_id, "n_clicks"), Input(btn_id + SUFIJO_CONTABLE, "n_clicks")]


def modo_descarga():
    """None si el callback no se disparó por un clic real (creación del componente),
    False = números completos, True = formato contable."""
    trig = ctx.triggered_id
    if trig is None:
        return None
    if not ctx.triggered or not ctx.triggered[0].get("value"):
        return None
    return str(trig).endswith(SUFIJO_CONTABLE)
