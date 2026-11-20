# -*- coding: utf-8 -*-
"""
dash_app/pages/cashi.py
Pestaña Cashi: asistente financiero conversacional sobre UnifiedCashiRAGEngine (rag/financial_rag.py).
Diseño: chat a la izquierda y panel de preguntas por tema a la derecha. Cada respuesta se arma como tarjeta: título,
texto ejecutivo (el markdown del motor se convierte en tablas, fichas de datos y encabezados; sin emojis), lectura rápida
con hallazgos calculados de la tabla, tabla, descargas y valoración. El motor sigue siendo el mismo (rag/); aquí solo
cambia la presentación. Estilos `.cs-*` en assets/zzz_tema.css.
"""
import sys
import re
import json
import uuid
import threading
import os
from pathlib import Path
from dash import html, dcc, callback, Input, Output, State, ALL, ctx, no_update
import dash_bootstrap_components as dbc
import pandas as pd

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dash_app.services.cashi_log_service import register_query
from dash_app.services.export_service import enviar_tabla

_engine = None
_engine_lock = threading.Lock()

_DATOS_ORIG = BASE_DIR / "datos"
_BITACORA = _DATOS_ORIG / "bitacora.json"

def _uso_dashboard_resumen() -> str:
    """Lee bitacora.json del Streamlit original y devuelve sugerencia proactiva."""
    try:
        if not _BITACORA.exists():
            return ""
        data = json.loads(_BITACORA.read_text(encoding="utf-8-sig"))
        conteo_vistas: dict[str, int] = {}
        conteo_descargas: dict[str, int] = {}
        for e in data.get("eventos", []):
            pestana = e.get("pestaña")
            tipo = e.get("tipo", "")
            if not pestana:
                continue
            if tipo == "NAVEGACION":
                conteo_vistas[pestana] = conteo_vistas.get(pestana, 0) + 1
            elif tipo.startswith("DESCARGA_") and tipo != "DESCARGA_FALLIDA":
                conteo_descargas[pestana] = conteo_descargas.get(pestana, 0) + 1
        top_vistas = sorted(conteo_vistas, key=conteo_vistas.get, reverse=True)[:2]
        top_descargas = sorted(conteo_descargas, key=conteo_descargas.get, reverse=True)[:2]
        if not top_vistas and not top_descargas:
            return ""
        partes = []
        if top_vistas:
            partes.append(f"las vistas más consultadas son **{' y '.join(top_vistas)}**")
        if top_descargas:
            partes.append(f"lo más descargado es **{' y '.join(top_descargas)}**")
        return "En el tablero, " + " · ".join(partes) + "."
    except Exception:
        return ""


def get_cashi_engine():
    """Motor de Cashi, creado una sola vez (tarda ~6 s: por eso se precalienta al arrancar, ver abajo)."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                import rag.financial_rag as fin_rag
                _engine = fin_rag.UnifiedCashiRAGEngine()
    return _engine


def _precalentar():
    try:
        get_cashi_engine()
    except Exception:
        pass


# La primera consulta tardaba ~6 s en crear el motor. Se crea en segundo plano al arrancar el tablero
# (DASH_CASHI_PRECALENTAR=0 lo apaga); si alguien pregunta antes de que termine, espera el mismo candado.
if os.environ.get("DASH_CASHI_PRECALENTAR", "1") != "0":
    threading.Thread(target=_precalentar, name="cashi-precalentar", daemon=True).start()


# Preguntas de ejemplo por tema (panel lateral). Cada una se envía como si se escribiera.
TEMAS = [
    ("Comparar", ["Real 2026 vs Real 2025", "Real 2026 vs Plan 2026", "Variación semana 30 vs 31"]),
    ("Rankings", ["Top 10 CECOs", "Pareto de Gasto", "Top variaciones vs Plan"]),
    ("Alertas", ["Señales de sobregasto", "Alertas de sobregasto", "¿Qué semanas fueron atípicas?"]),
    ("Cuentas", ["Gastos de Personal", "Gastos de Operación"]),
    ("Territorio", ["¿Qué territorios tiene la División Norte?"]),
]

# Qué sabe hacer Cashi (tarjetas de la pantalla inicial; son solo informativas)
CAPACIDADES = [
    ("res", "Compara periodos", "Real 2026 contra Real 2025, Plan o Forecast, por cuenta, CECO o división."),
    ("tmp", "Rankings y Pareto", "Los CECOs, cuentas o territorios que más gastan y cuánto concentran."),
    ("ale", "Señales de sobregasto", "Qué partidas se salen del Plan y qué tan atípica es la semana."),
    ("pdv", "Territorio", "Preguntas por división, región, zona o tienda."),
]

CASHI_BATCH = 15

_KW_FINANCIERO = {"real", "plan", "forecast", "fcst", "monto", "plan_sys", "conta",
                  "ordinario", "comparab", "ppto", "regla", "_abs", "desv", "variacion"}
_KW_PORCENTAJE = {"_pct", "_porcentaje", "_percent", " pct", " %"}
_KW_VARIACION = ("desv", "var", "vs_", "vs ", "pct")

# Pictogramas y emojis (incluye el selector de variación y el unidor): las respuestas se muestran sin ellos.
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\u2300-\u23FF\uFE0F\u200D]")
_NUM_RE = re.compile(r"^[\s\(\-+−$]*\d[\d,\.]*\s*(%|MDP|M)?\)?\s*(MDP|M)?\)?$")
_DATO_RE = re.compile(r"^\s*[-•*]\s+\*\*(.+?)\*\*\s*:?\s*(.*)$")
_TABLA_SEP_RE = re.compile(r"^\s*\|?[\s:\-|]+\|?\s*$")


def _sin_emojis(texto):
    """Quita emojis del texto y limpia los espacios que quedan (sin tocar sangrías ni saltos de línea)."""
    if not isinstance(texto, str):
        return texto
    t = _EMOJI_RE.sub("", texto)
    t = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", t)
    return re.sub(r"[ \t]+$", "", t, flags=re.M)


def _es_financiero(col):
    cl = str(col).lower()
    return any(kw in cl for kw in _KW_FINANCIERO)


def _es_porcentaje(col):
    cl = str(col).lower()
    return any(kw in cl for kw in _KW_PORCENTAJE)


def _es_variacion(col):
    cl = str(col).lower()
    return any(kw in cl for kw in _KW_VARIACION)


def _fmt_val(col_name, val):
    if pd.isna(val):
        return "—"
    if _es_porcentaje(col_name):
        return f"{val:+.1f}%" if val != 0 else "0.0%"
    if _es_financiero(col_name) or abs(val) >= 1e6:
        return f"{'-' if val < 0 else ''}${abs(val)/1e6:,.2f} MDP"
    if isinstance(val, float):
        return f"{val:,.2f}"
    return f"{val:,}"


def _hallazgos(df):
    """Lectura rápida de la tabla (calculada, sin texto inventado): mayor partida, concentración y mayores movimientos.
    Devuelve [(etiqueta, texto, clase)]; vacío si la tabla no se presta (menos de 2 filas o sin columna de texto/número)."""
    out = []
    try:
        if df is None or len(df) < 2:
            return out
        num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        txt = [c for c in df.columns if c not in num]
        if not num or not txt:
            return out
        et = txt[0]
        val = next((c for c in num if _es_financiero(c) and not _es_variacion(c) and not _es_porcentaje(c)), None)
        if val:
            s = df[[et, val]].dropna()
            tot = s[val].abs().sum()
            if len(s) and tot:
                top = s.loc[s[val].abs().idxmax()]
                out.append(("Mayor partida", f"{top[et]} · {_fmt_val(val, top[val])} ({abs(top[val]) / tot * 100:.0f}% del total)", ""))
                if len(s) >= 5:
                    out.append(("Concentración", f"las 3 mayores suman {s[val].abs().nlargest(3).sum() / tot * 100:.0f}%", ""))
        var = next((c for c in num if _es_variacion(c) and not _es_porcentaje(c)), None) or next((c for c in num if _es_variacion(c)), None)
        if var:
            s = df[[et, var]].dropna()
            if len(s) >= 2:
                up, dn = s.loc[s[var].idxmax()], s.loc[s[var].idxmin()]
                if up[var] > 0:
                    out.append(("Mayor alza", f"{up[et]} · {_fmt_val(var, up[var])}", "up"))
                if dn[var] < 0:
                    out.append(("Mayor baja", f"{dn[et]} · {_fmt_val(var, dn[var])}", "dn"))
    except Exception:
        return []
    return out


def _tabla(df_t, rows_shown):
    cols = [html.Th(str(c), className="num" if pd.api.types.is_numeric_dtype(df_t[c]) else "") for c in df_t.columns]
    filas = []
    for _, row in df_t.head(rows_shown).iterrows():
        celdas = []
        for col_name, val in row.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                clase = "num"
                if _es_variacion(col_name) and not pd.isna(val) and val != 0:
                    clase += " dn" if val < 0 else " up"   # en gasto: negativo (ahorro) verde, positivo (sobregasto) rojo
                celdas.append(html.Td(_fmt_val(col_name, val), className=clase))
            else:
                celdas.append(html.Td(_sin_emojis(str(val))))
        filas.append(html.Tr(celdas))
    return html.Div(className="cs-tabla", children=dbc.Table(
        size="sm", className="mb-0", children=[html.Thead(html.Tr(cols)), html.Tbody(filas)]))


def _celdas_md(linea):
    partes = linea.strip().strip("|").split("|")
    return [p.strip().replace("**", "") for p in partes]


def _tabla_md(cabeza, filas):
    """Tabla de markdown (| a | b |) convertida a tabla del tema; las celdas que parecen número van a la derecha."""
    n = len(cabeza)
    num_col = [bool(filas) and all((len(f) > j and (not f[j] or _NUM_RE.match(f[j]))) for f in filas) for j in range(n)]
    ths = [html.Th(c, className="num" if num_col[j] else "") for j, c in enumerate(cabeza)]
    trs = []
    for f in filas:
        f = (f + [""] * n)[:n]
        tds = []
        for j, v in enumerate(f):
            clase = "num" if num_col[j] else ""
            if num_col[j] and _es_variacion(cabeza[j]) and v:
                neg = v.lstrip("(").startswith(("-", "−"))
                clase += " dn" if neg else " up"
            tds.append(html.Td(v, className=clase.strip()))
        trs.append(html.Tr(tds))
    return html.Div(className="cs-tabla", children=dbc.Table(
        size="sm", className="mb-0", children=[html.Thead(html.Tr(ths)), html.Tbody(trs)]))


def _render_markdown(texto):
    """Respuesta del motor (markdown) -> componentes: tablas `| a | b |` como tablas del tema, listas «- **Etiqueta:** valor»
    como fichas, encabezados `###` como subtítulos y el resto como texto. Sin emojis."""
    lineas = _sin_emojis(texto or "").splitlines()
    bloques, buf, datos = [], [], []

    def vuelca_texto():
        if buf and "".join(buf).strip():
            bloques.append(dcc.Markdown("\n".join(buf), className="cs-resp"))
        buf.clear()

    def vuelca_datos():
        if len(datos) >= 2:
            bloques.append(html.Div(className="cs-datos", children=[
                html.Div(className="cs-dato", children=[html.Span(et.rstrip(":"), className="cs-dato-lbl"), html.Span(v.replace("**", ""), className="cs-dato-val")])
                for et, v in datos]))
        else:
            for et, v in datos:                      # una sola línea suelta: queda como texto
                buf.append(f"- **{et}** {v}")
        datos.clear()

    i = 0
    while i < len(lineas):
        ln = lineas[i]
        if ln.lstrip().startswith("|") and i + 1 < len(lineas) and "-" in lineas[i + 1] and _TABLA_SEP_RE.match(lineas[i + 1]):
            vuelca_datos(); vuelca_texto()
            cabeza, j, filas = _celdas_md(ln), i + 2, []
            while j < len(lineas) and lineas[j].lstrip().startswith("|"):
                filas.append(_celdas_md(lineas[j]))
                j += 1
            bloques.append(_tabla_md(cabeza, filas))
            i = j
            continue
        m = _DATO_RE.match(ln)
        if m and m.group(2).strip():
            vuelca_texto()
            datos.append((m.group(1).strip(), m.group(2).strip()))
            i += 1
            continue
        vuelca_datos()
        h = re.match(r"^#{1,6}\s+(.*)", ln)
        if h:
            vuelca_texto()
            bloques.append(html.Div(h.group(1).strip(), className="cs-h"))
        else:
            buf.append(ln)
        i += 1
    vuelca_datos()
    vuelca_texto()
    return bloques


def _adaptar_para_usuario(res, es_admin):
    """Lo técnico (fragmentos de la documentación del motor, SQL y linaje) es solo para administradores:
    el resto de los usuarios ve una respuesta de negocio o una invitación a reformular la pregunta."""
    if es_admin:
        return res
    res = dict(res)
    res.pop("citas_documentales", None)
    res.pop("sql_linaje", None)
    if res.get("tipo_respuesta") == "documental":
        return {
            "tipo_respuesta": "sin_coincidencias",
            "titulo": "No encontré esa respuesta con los datos",
            "respuesta_ejecutiva": (
                "No pude responder esa pregunta con las cifras del tablero. Intenta con un periodo, una cuenta, un CECO o una "
                "división, por ejemplo:\n\n"
                "- Gasto de la semana 30 con la versión de la semana 40\n"
                "- Top 10 CECOs de la División Norte\n"
                "- Real 2026 vs Plan 2026 de Gastos de Personal\n\n"
                "También puedes elegir una pregunta del panel de la derecha."),
            "tabla": [],
        }
    return res


def _render_cashi_card(res, query_text, turn_id="0", rows_shown=CASHI_BATCH, es_admin=False):
    res = _adaptar_para_usuario(res, es_admin)
    titulo = _sin_emojis(res.get("titulo", "Resultado Cashi"))
    resp_ejecutiva = res.get("respuesta_ejecutiva", res.get("respuesta", ""))
    tabla_data = res.get("tabla", [])
    linaje_dict = res.get("sql_linaje", None)

    elements = [
        html.Div(className="cs-card-head", children=[
            html.Span(className="cs-avatar", children=html.Span(className="ico ico-chat")),
            html.H6(titulo, className="cs-card-title"),
        ]),
        *_render_markdown(resp_ejecutiva),
    ]

    if tabla_data:
        df_t = pd.DataFrame(tabla_data)

        hallazgos = _hallazgos(df_t)
        if hallazgos:
            elements.append(html.Div(className="cs-hallazgos", children=[
                html.Div(className=f"cs-hz {clase}".strip(), children=[html.Span(et, className="cs-hz-lbl"), html.Span(_sin_emojis(txt), className="cs-hz-val")])
                for et, txt, clase in hallazgos
            ]))

        elements.append(_tabla(df_t, rows_shown))

        # "Mostrar más" (si hay más filas que las visibles) y descargas con la tabla COMPLETA sin truncar.
        botones_tabla = []
        restantes = len(df_t) - rows_shown
        if restantes > 0:
            botones_tabla.append(
                dbc.Button(
                    f"Mostrar {min(CASHI_BATCH, restantes)} más ({restantes:,} restantes)",
                    id={"type": "cashi-show-more", "turn": turn_id},
                    size="sm", color="light", className="cs-btn",
                )
            )
        botones_tabla.append(
            dbc.Button(
                [html.Span(className="ico ico-download"), " Excel"],
                id={"type": "cashi-download-btn", "turn": turn_id},
                size="sm", color="light", className="cs-btn vt-dl-btns",
            )
        )
        botones_tabla.append(
            dbc.Button(
                [html.Span(className="ico ico-doc"), " Excel contable"],
                id={"type": "cashi-download-conta-btn", "turn": turn_id},
                size="sm", color="light", className="cs-btn vt-dl-btns",
            )
        )
        elements.append(html.Div(botones_tabla, className="cs-acciones"))

    if linaje_dict and es_admin:
        sql_text = linaje_dict.get("sql_ejecutado", "")
        tiempo_ms = linaje_dict.get("tiempo_ms", 0)
        filas = linaje_dict.get("filas_procesadas", 0)
        fuentes = linaje_dict.get("fuentes_parquet", [])

        linaje_str = f"-- Consulta SQL ejecutada en {tiempo_ms}ms ({filas} filas procesadas)\n"
        linaje_str += f"-- Fuentes: {', '.join(fuentes)}\n\n"
        linaje_str += sql_text if sql_text else "-- Sin SQL disponible"

        elements.append(
            html.Details(className="cs-sql", children=[
                html.Summary("Ver consulta SQL y linaje"),
                html.Pre(linaje_str),
            ])
        )

    # "¿Quisiste decir...?": la mejor opción ya se aplicó; se ofrecen otras tres como botones
    alt = res.get("alternativas_entidad") or {}
    if alt.get("opciones"):
        elements.append(html.Div(_sin_emojis(alt.get("mensaje", "¿Quisiste decir otra opción?")), className="cs-alt-msg"))
        elements.append(html.Div(
            [
                dbc.Button(op["etiqueta"], id={"type": "cashi-alt", "query": op["query"]},
                           size="sm", color="light", className="cs-chip")
                for op in alt["opciones"]
            ],
            className="cs-chips"
        ))

    # Valoración de la respuesta (se guarda en datos/cashi_feedback.jsonl)
    elements.append(html.Div(className="cs-fb", children=[
        html.Span("¿Te sirvió?", className="cs-fb-lbl"),
        dbc.Button("Buena respuesta", id={"type": "cashi-fb", "turn": turn_id, "val": 1}, size="sm", color="light", className="cs-btn"),
        dbc.Button("Mala respuesta", id={"type": "cashi-fb", "turn": turn_id, "val": -1}, size="sm", color="light", className="cs-btn"),
    ]))

    return html.Div(className="cs-card", children=elements)


def _render_empty_state():
    return html.Div(className="cs-vacio", children=[
        html.H5("¿Qué quieres revisar hoy?", className="cs-vacio-title"),
        html.P("Pregunta con tus palabras. Cashi consulta los datos del tablero, calcula y te responde con tablas "
               "descargables y los hallazgos más importantes.", className="cs-vacio-sub"),
        html.Div(className="cs-cap-grid", children=[
            html.Div(className="cs-cap", children=[
                html.Span(className="cs-avatar", children=html.Span(className=f"ico ico-{ico}")),
                html.Div([html.Div(tit, className="cs-cap-tit"), html.Div(txt, className="cs-cap-txt")]),
            ])
            for ico, tit, txt in CAPACIDADES
        ]),
        html.Div("Elige una pregunta del panel de la derecha o escribe la tuya abajo.", className="cs-vacio-pie"),
    ])


def _panel_temas():
    grupos = []
    for tema, preguntas in TEMAS:
        grupos.append(html.Div(className="cs-grupo", children=[
            html.Div(tema, className="cs-grupo-tit"),
            html.Div([dbc.Button(q, id={"type": "cashi-chip", "query": q}, size="sm", color="light", className="cs-chip")
                      for q in preguntas], className="cs-chips"),
        ]))
    return html.Div(className="cs-side", children=[
        html.Div("Pregunta por tema", className="cs-side-tit"),
        *grupos,
    ])


def layout():
    return html.Div(
        className="cs-pagina",
        children=[
            dcc.Store(id="cashi-history-store", data=[]),
            dcc.Store(id="cashi-rows-shown", data={}),
            dcc.Download(id="cashi-download-csv"),
            html.Div(id="cashi-fb-status", className="cs-status"),

            html.Div(className="cs-head", children=[
                html.Div(className="cs-head-id", children=[
                    html.Span(className="cs-avatar cs-avatar-lg", children=html.Span(className="ico ico-chat")),
                    html.Div([html.H5("Cashi", className="cs-title"), html.Span("Asistente financiero", className="cs-sub")]),
                ]),
                dbc.Button("Limpiar conversación", id="cashi-btn-clear", size="sm", color="light", className="cs-btn"),
            ]),

            html.Div(className="cs-layout", children=[
                html.Div(className="cs-main", children=[
                    dcc.Loading(
                        id="cashi-loading",
                        type="circle",
                        color="#a93226",
                        children=html.Div(id="cashi-chat-stream", children=_render_empty_state())
                    ),
                    html.Div(className="cs-input", children=[
                        dbc.Input(
                            id="cashi-input-text",
                            placeholder="Escribe tu consulta (ej. “Real 2026 vs 2025”, “Top variaciones vs Plan”)…",
                            className="cs-input-campo",
                        ),
                        dbc.Button("Consultar", id="cashi-btn-send", className="cs-send"),
                    ]),
                ]),
                _panel_temas(),
            ]),
        ]
    )

# -----------------------------------------------------------------------------
# Callback para Procesamiento de Consultas de Cashi
# -----------------------------------------------------------------------------
@callback(
    Output("cashi-history-store", "data"),
    Output("cashi-input-text", "value"),
    Output("cashi-rows-shown", "data", allow_duplicate=True),
    Input("cashi-btn-send", "n_clicks"),
    Input("cashi-input-text", "n_submit"),
    Input({"type": "cashi-chip", "query": ALL}, "n_clicks"),
    Input({"type": "cashi-alt", "query": ALL}, "n_clicks"),
    Input("cashi-btn-clear", "n_clicks"),
    State("cashi-input-text", "value"),
    State("cashi-history-store", "data"),
    State("session-user-store", "data"),
    State("global-filter-store", "data"),
    prevent_initial_call=True
)
def manage_cashi_query(n_clicks, n_submit, n_chips, n_alts, n_clear, input_val, history_data, session_data, global_filters=None):
    try:
        triggered = ctx.triggered_id
    except Exception:
        triggered = "cashi-btn-send"

    if triggered == "cashi-btn-clear":
        return [], "", {}

    target_query = ""
    if isinstance(triggered, dict) and triggered.get("type") in ("cashi-chip", "cashi-alt"):
        target_query = triggered.get("query", "")
    elif triggered in ("cashi-btn-send", "cashi-input-text"):
        target_query = str(input_val or "").strip()

    if not target_query:
        return history_data or [], "", no_update

    engine = get_cashi_engine()

    # Obtener contexto previo del último turno para reutilizar filtros en follow-ups
    contexto_previo = None
    if history_data and len(history_data) > 0:
        ultimo_resultado = history_data[-1].get("result", {})
        contexto_previo = ultimo_resultado.get("filtros_resueltos")

    try:
        res = engine.process_query(target_query, contexto_previo=contexto_previo,
                                   canal=(global_filters or {}).get("canal") or "Fisico")
        exitosa = res.get("tipo_respuesta") not in ("sin_coincidencias", "sugerencia")
        sugerencia = _uso_dashboard_resumen()
        if sugerencia and "respuesta_ejecutiva" in res:
            res["respuesta_ejecutiva"] = res["respuesta_ejecutiva"] + f"\n\n{sugerencia}"
    except Exception as ex:
        res = {
            "titulo": "Error en Consulta",
            "respuesta_ejecutiva": f"Ocurrió un error al procesar la consulta en el motor: {ex}",
            "tabla": [],
            "sql_linaje": {}
        }
        exitosa = False

    # Log ligero y no-bloqueante de la consulta a Cashi (usuario, pregunta, intención)
    try:
        username_log = (session_data or {}).get("username", "Desconocido")
        register_query(
            username=username_log,
            pregunta=target_query,
            intencion_detectada=res.get("intencion_detectada", ""),
            exitosa=exitosa
        )
        # Mismo evento en el registro de uso del Dash (junto con las visitas a pestañas, logins y lentitud).
        from dash_app.services import uso_service
        uso_service.registrar("CASHI_CONSULTA", username_log, pestana="tab-cashi",
                              ms=(res.get("sql_linaje") or {}).get("tiempo_ms"), detalle=target_query,
                              ruta=res.get("intencion_detectada", ""), exitosa=bool(exitosa),
                              filas=len(res.get("tabla") or []))
    except Exception:
        pass

    new_turn = {
        # id estable (no el índice de la lista) para que los botones "Mostrar
        # más"/"Descargar CSV" de un turno sigan apuntando al turno correcto
        # aunque el historial se recorte a los últimos 10 (el índice de lista
        # cambia al recortar, un uuid no).
        "turn_id": uuid.uuid4().hex[:8],
        "query": target_query,
        "result": res
    }

    current_history = list(history_data or [])
    current_history.append(new_turn)

    # Mantener últimos 10 turnos para agilidad de sesión
    return current_history[-10:], "", no_update

@callback(
    Output("cashi-chat-stream", "children"),
    Input("cashi-history-store", "data"),
    Input("cashi-rows-shown", "data"),
    State("session-user-store", "data")
)
def render_cashi_chat(history_data, rows_shown_data, session_data=None):
    if not history_data or len(history_data) == 0:
        return _render_empty_state()

    rows_shown_data = rows_shown_data or {}
    es_admin = (session_data or {}).get("role") == "admin"
    turns = []
    for idx, item in enumerate(history_data):
        q = item.get("query", "")
        res = item.get("result", {})
        turn_id = item.get("turn_id", str(idx))

        # Burbuja del usuario
        user_bubble = html.Div(className="cs-user", children=html.Div(q, className="cs-user-burbuja"))
        # Respuesta de Cashi
        rows_shown = rows_shown_data.get(turn_id, CASHI_BATCH)
        bot_card = _render_cashi_card(res, q, turn_id=turn_id, rows_shown=rows_shown, es_admin=es_admin)
        turns.append(html.Div([user_bubble, bot_card], className="cs-turno"))

    return html.Div(turns)


@callback(
    Output("cashi-fb-status", "children"),
    Input({"type": "cashi-fb", "turn": ALL, "val": ALL}, "n_clicks"),
    State("cashi-history-store", "data"),
    State("session-user-store", "data"),
    prevent_initial_call=True
)
def registrar_feedback_cashi(n_clicks_list, history_data, session_data):
    """Valoración (buena/mala) de una respuesta de Cashi -> datos/cashi_feedback.jsonl"""
    from rag.cashi_ml.feedback import registrar
    disparado = ctx.triggered_id
    if not isinstance(disparado, dict) or not any(n_clicks_list or []):
        return no_update
    turno = disparado.get("turn")
    valor = int(disparado.get("val", 0))
    item = next((h for h in (history_data or []) if h.get("turn_id") == turno), None)
    if item is None:
        return "No encontré la respuesta a valorar (el historial se reinició)."
    usuario = (session_data or {}).get("username", "desconocido")
    ok = registrar(item.get("query", ""), item.get("result", {}), valor, usuario, turno, "dash")
    marca = "buena respuesta" if valor > 0 else "mala respuesta"
    return f"Registrado: {marca}." if ok else "No se pudo registrar la valoración."

@callback(
    Output("cashi-rows-shown", "data"),
    Input({"type": "cashi-show-more", "turn": ALL}, "n_clicks"),
    State("cashi-rows-shown", "data"),
    prevent_initial_call=True
)
def expand_cashi_table(n_clicks_list, rows_shown_data):
    triggered = ctx.triggered_id
    if not triggered or not any(n_clicks_list):
        return no_update
    turn_id = triggered.get("turn")
    rows_shown_data = dict(rows_shown_data or {})
    rows_shown_data[turn_id] = rows_shown_data.get(turn_id, CASHI_BATCH) + CASHI_BATCH
    return rows_shown_data


@callback(
    Output("cashi-download-csv", "data"),
    Input({"type": "cashi-download-btn", "turn": ALL}, "n_clicks"),
    Input({"type": "cashi-download-conta-btn", "turn": ALL}, "n_clicks"),
    State("cashi-history-store", "data"),
    prevent_initial_call=True
)
def download_cashi_excel(n_clicks_list, n_clicks_conta_list, history_data):
    triggered = ctx.triggered_id
    if not triggered or not (ctx.triggered and ctx.triggered[0].get("value")):
        return no_update
    contable = triggered.get("type") == "cashi-download-conta-btn"
    turn_id = triggered.get("turn")
    for item in (history_data or []):
        if item.get("turn_id") == turn_id:
            tabla_data = item.get("result", {}).get("tabla", [])
            if not tabla_data:
                return no_update
            df = pd.DataFrame(tabla_data)
            return enviar_tabla(df, f"cashi_resultado_analitico_{turn_id}", contable)
    return no_update

