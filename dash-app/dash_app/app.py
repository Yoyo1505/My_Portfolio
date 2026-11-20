# -*- coding: utf-8 -*-
"""
Aplicación Principal Plotly Dash — Retail Channel Spend Grupo Aurora.
- Autenticación Ejecutiva Universal con Cero Latencia y Captura de Enter.
- Contenedores duales persistentes (Login & Dashboard) con alternancia CSS instantánea (0 ms).
- Pre-calentamiento en segundo plano (<3 ms en Resumen y Sobregasto).
- Compresión Gzip en Flask.
"""
from pathlib import Path
import sys
import threading

BASE_DIR = Path(__file__).parent.parent.resolve()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Cashi (rag/) importa polars de forma perezosa desde un hilo; si otro hilo arma una gráfica mientras tanto,
# plotly/narwhals ve polars a medio importar y truena ("partially initialized module"). Se carga aquí, una vez.
import polars  # noqa: F401
import dash
from dash import html, dcc, Input, Output, State, MATCH
import dash_bootstrap_components as dbc

from dash_app.components.header import create_header
from dash_app.components.top_filter_bar import create_top_filter_bar, NOM_MES
from dash_app.services.data_service import DataService
from dash_app.services.auth_service import AuthService

# Importar páginas
import dash_app.pages.login as page_login
import dash_app.pages.resumen as page_resumen
import dash_app.pages.sobregasto as page_sobregasto
import dash_app.pages.detalle_cuenta as page_detalle_cuenta
import dash_app.pages.detalle_pdv as page_detalle_pdv
import dash_app.pages.detalle_ceco as page_detalle_ceco
import dash_app.pages.contraloria as page_contraloria
import dash_app.pages.cierres as page_cierres
import dash_app.pages.temporalidad as page_temporalidad
import dash_app.pages.comparacion as page_comparacion
import dash_app.pages.tabla_dinamica as page_tabla_dinamica
import dash_app.pages.cashi as page_cashi
import dash_app.pages.admin_usuarios as page_admin_usuarios
import dash_app.pages.plan_vs_conta as page_plan_vs_conta
import dash_app.pages.presupuesto_2027  # noqa: F401 — registra sus @callback; se usa vía plan_vs_conta (sub-tab)

app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.BOOTSTRAP,
    ],
    suppress_callback_exceptions=True,
    title="Gasto Canales · Grupo Aurora",
    update_title=None
)
server = app.server

# Registro de uso (dash_app/services/uso_service.py → datos/uso_dash.jsonl): además de lo que registra cada pestaña,
# deja constancia de cualquier petición del tablero que tarde más de 1 s (qué callback y cuánto), el "log de lentitud".
import time as _time
from flask import request as _rq
from dash_app.services import uso_service


@server.before_request
def _uso_inicio():
    _rq.environ["_uso_t0"] = _time.perf_counter()


@server.after_request
def _uso_lenta(resp):
    try:
        t0 = _rq.environ.get("_uso_t0")
        if t0 is not None and _rq.path == "/_dash-update-component":
            ms = (_time.perf_counter() - t0) * 1000.0
            if ms > 1000.0:
                salida = (_rq.get_json(silent=True) or {}).get("output", "")
                uso_service.registrar("LENTA", None, ms=ms, detalle=str(salida)[:160], bytes=resp.calculate_content_length())
    except Exception:
        pass
    return resp

# -----------------------------------------------------------------------------
# Compresión Gzip (flask-compress con fallback nativo transparente)
# -----------------------------------------------------------------------------
try:
    from flask_compress import Compress
    Compress(server)
except ImportError:
    import gzip
    from flask import request
    @server.after_request
    def compress_response(response):
        accept_encoding = request.headers.get("Accept-Encoding", "")
        if "gzip" not in accept_encoding.lower() or response.status_code < 200 or response.status_code >= 300:
            return response
        if response.direct_passthrough or response.is_streamed:
            return response
        content_type = response.headers.get("Content-Type", "")
        if not any(t in content_type for t in ["text", "json", "javascript", "css"]):
            return response
        response.direct_passthrough = False
        data = response.get_data()
        if len(data) < 500:
            return response
        gzipped_data = gzip.compress(data, compresslevel=6)
        response.set_data(gzipped_data)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = len(gzipped_data)
        return response

ds = DataService()
auth = AuthService()

# Salvaguarda: garantizar alias ds.grupos para top_filter_bar
if not hasattr(ds, "grupos"):
    ds.grupos = getattr(ds, "grupos_cuentas", [])

# -----------------------------------------------------------------------------
# Pre-calentamiento (Cache Warming) para respuesta < 3 ms en Resumen y Sobregasto
# -----------------------------------------------------------------------------
def _warm_all_services():
    try:
        def_f = {
            "tipo_monto": "Monto Constante",
            "modo": "Rango de semanas",
            "si": 1,
            "sf": ds.smr,
            "fg_resp": [],
            "fg_gpo": [],
            "fg_cta": [],
            "fg_subtipo": []
        }
        for f in [None, def_f]:
            ds.compute_summary_kpis(f)
            ds.compute_by_dimension("grupo", f)
            ds.compute_by_dimension("division", f)
            ds.compute_weekly_series(f)
        ds.get_variaciones_riesgos_oportunidades()

        from dash_app.services.duckdb_service import DuckDBService
        ddb = DuckDBService()
        for modo in ["ceco", "pdv", "division"]:
            ddb.query_sobregasto(group_by=modo, si=1, sf=ds.smr, filters=None)
            ddb.query_sobregasto(group_by=modo, si=1, sf=ds.smr, filters=def_f)

        # Pre-calentar Cashi AI Assistant en segundo plano
        from dash_app.pages.cashi import get_cashi_engine
        get_cashi_engine()
    except Exception as e:
        print(f"[WARM-CACHE] Info: {e}")

    # 2026-09-15: se QUITA el pre-calentamiento eager de PLAN vs CONTA (tardaba
    # ~45s leyendo docenas de parquet/Excel de SAP en el mismo hilo) — con el
    # resto de la precarga de arriba, el hilo de fondo competía por el GIL con
    # el hilo principal de Flask durante 1-2 minutos tras arrancar, y la app
    # se sentía "trabada" justo en ese primer minuto (reportado por el
    # usuario: "los filtros generales... llegan a trabar la app"). Ahora esa
    # pestaña calienta su propio caché la primera vez que alguien la abre
    # (ver _obtener_arbol -> _CACHE en plan_vs_conta.py), no al arrancar.

threading.Thread(target=_warm_all_services, daemon=True).start()

TABS_CONFIG = [
    ("tab-resumen", "Resumen"),
    ("tab-detalle-cuenta", "Cuenta"),
    ("tab-detalle-pdv", "PDV"),
    ("tab-detalle-ceco", "CECO"),
    ("tab-contraloria", "Contraloría"),
    ("tab-cierres", "Cierres"),
    ("tab-temporalidad", "Tiempo"),
    ("tab-comparacion", "Comparar"),
    ("tab-tabla-dinamica", "Dinámica"),
    ("tab-plan-conta", "Contable"),
    ("tab-cashi", "Cashi"),
    ("tab-admin", "Usuarios"),
    ("tab-sobregasto", "Alertas"),
]

# -----------------------------------------------------------------------------
# Layout con Contenedores Duales Persistentes (Cero Fricción · Cero Errores)
# -----------------------------------------------------------------------------
app.layout = html.Div(
    id="root-application-layout",
    children=[
        dcc.Location(id="url", refresh=False),
        dcc.Store(id="session-user-store", storage_type="session"),
        dcc.Store(id="theme-store", storage_type="local", data="light"),
        html.Div(id="theme-dummy", style={"display": "none"}),
        html.Div(id="dl-gate-dummy", style={"display": "none"}),
        # Filtro de búsqueda por árbol: un Store POR PÁGINA (no uno compartido)
        # — con un solo Output "tree-search-filters" usado por 4 páginas a la
        # vez, Dash 4.4.1 no rechaza el duplicado al importar pero solo deja
        # activo el último callback registrado, dejando mudos a los demás
        # (causa real de "la búsqueda no filtra" en Cierres/CECO/Cuenta/PDV).
        # Cada página escribe aquí {"tree_key": ..., ...filtros...} y
        # toggle_tree_branch (más abajo) los combina eligiendo por tree_key.
        dcc.Store(id="pdv-search-filters", storage_type="memory", data={}),
        dcc.Store(id="ceco-search-filters", storage_type="memory", data={}),
        dcc.Store(id="cuenta-search-filters", storage_type="memory", data={}),
        dcc.Store(id="cierres-search-filters", storage_type="memory", data={}),
        dcc.Store(
            id="global-filter-store",
            storage_type="session",
            data={
                "canal": "Fisico",
                "tipo_monto": "Monto Constante",
                "modo": "Rango de semanas",
                "si": 1,
                "sf": ds.smr,
                "fg_resp": [],
                "fg_gpo": [],
                "fg_cta": [],
                "fg_subtipo": []
            }
        ),

        # 1. Contenedor de Login (Visible si no hay sesión)
        html.Div(
            id="login-view-container",
            style={"display": "block"},
            children=page_login.layout()
        ),

        # 2. Contenedor Principal de la Aplicación (Oculto inicialmente)
        html.Div(
            id="app-view-container",
            style={"display": "none"},
            children=[
                dbc.Container(
                    fluid=True,
                    className="main-app-container py-3 px-4",
                    children=[
                        # Header Institucional
                        create_header(smr=ds.smr, user_name="admin1"),

                        # Fila de docks: filtros globales (izquierda) y pestañas ejecutivas (derecha, dinámico:
                        # los nombres aparecen al pasar el mouse)
                        html.Div(
                            className="nav-tabs-custom mb-3",
                            children=[
                                create_top_filter_bar(),
                                dbc.Tabs(
                                    id="app-navigation-tabs",
                                    active_tab="tab-resumen",
                                    children=[
                                        dbc.Tab(label=label, tab_id=tid)
                                        for tid, label in TABS_CONFIG
                                    ]
                                ),
                            ]
                        ),

                        # Aviso cuando la vista no tiene detalle del canal elegido
                        html.Div(id="canal-aviso"),

                        # Área de Contenido de Pestaña
                        html.Div(id="page-content-area", children=page_resumen.layout())
                    ]
                )
            ]
        )
    ]
)

# Vistas cuyos agregados (CECO, PDV, cierres, contraloría, SAP...) solo existen para el canal físico.
_TABS_SOLO_FISICO = {
    "tab-comparacion", "tab-plan-conta", "tab-cierres",   # snapshots/Master Cierres sin versión digital
}

_TABS_SIN_SUBTIPO = {"tab-cierres", "tab-comparacion", "tab-plan-conta"}

@app.callback(
    Output("canal-aviso", "children"),
    Input("global-filter-store", "data"),
    Input("app-navigation-tabs", "active_tab"),
)
def aviso_canal(filters, active_tab):
    avisos = []
    canal = ds.canal_de(filters)
    if canal != "Fisico" and active_tab in _TABS_SOLO_FISICO:
        txt = ("Esta vista no tiene detalle del canal Digital: no hay datos que mostrar con este filtro."
               if canal == "Digital" else
               "Esta vista muestra solo el canal Físico: el detalle Digital aún no existe aquí.")
        avisos.append(dbc.Alert(txt, color="secondary", className="py-2 mb-3", style={"fontSize": "0.85rem"}))
    # Pestañas cuyos datos no traen Subtipo: el filtro global no se puede aplicar (cierres_arbol lo triplicaría
    # de tamaño; los snapshots de Comparación y el Ppto 2027 no lo tienen). Se avisa en vez de ignorarlo en silencio.
    if (filters or {}).get("fg_subtipo") and active_tab in _TABS_SIN_SUBTIPO:
        avisos.append(dbc.Alert("El filtro Subtipo no aplica en esta pestaña: sus datos no traen Subtipo.",
                                color="warning", className="py-2 mb-3", style={"fontSize": "0.85rem"}))
    return avisos or None

# Modo claro/oscuro: el botón del encabezado alterna el valor guardado en localStorage
# (theme-store) y data-theme / data-bs-theme en <html> cambian los colores (custom.css).
app.clientside_callback(
    """function(n, actual){
        return actual === 'dark' ? 'light' : 'dark';
    }""",
    Output("theme-store", "data"),
    Input("btn-theme", "n_clicks"),
    State("theme-store", "data"),
    prevent_initial_call=True,
)
app.clientside_callback(
    """function(t){
        var tema = (t === 'dark') ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', tema);
        document.documentElement.setAttribute('data-bs-theme', tema);
        return '';
    }""",
    Output("theme-dummy", "children"),
    Input("theme-store", "data"),
)

# Usuarios sin permiso de descarga: se ocultan todos los botones .vt-dl-btns
# (clase en export_service.download_buttons). Paridad con el gate `permite_descargar` del Streamlit.
app.clientside_callback(
    """function(u){
        var sin = !!(u && u.permite_descargar === false);
        document.body.classList.toggle('vt-sin-descarga', sin);
        document.cookie = 'vt_sin_descarga=' + (sin ? '1' : '0') + '; path=/; SameSite=Strict';
        // Quién descarga: el servidor lo lee de esta cookie para el historial (solo atribución, no es control de acceso).
        document.cookie = 'vt_usuario=' + encodeURIComponent((u && u.username) || '') + '; path=/; SameSite=Strict';
        return '';
    }""",
    Output("dl-gate-dummy", "children"),
    Input("session-user-store", "data"),
)

@app.server.before_request
def _bloquear_descargas_sin_permiso():
    """Gate en servidor: si la cookie `vt_sin_descarga=1` (puesta al iniciar sesión) está activa,
    cualquier callback cuyo Output sea un dcc.Download se rechaza con 403."""
    from flask import request, abort
    if request.path != "/_dash-update-component" or request.cookies.get("vt_sin_descarga") != "1":
        return None
    body = request.get_json(silent=True) or {}
    if "download" in str(body.get("output", "")).lower():
        abort(403)
    return None


# -----------------------------------------------------------------------------
# Precarga de pestañas al iniciar sesión (dash_app/services/precarga.py): en segundo plano y una sola vez por
# proceso, calienta las consultas de cada pestaña en el orden de uso real (datos/uso_pestanas.json).
# Apagable con la variable de entorno DASH_PRECARGA=0.
# -----------------------------------------------------------------------------
def _precargar_pestanas(usuario=""):
    try:
        from dash_app.services import precarga
        layouts = {
            "tab-resumen": page_resumen.layout, "tab-sobregasto": page_sobregasto.layout,
            "tab-detalle-cuenta": page_detalle_cuenta.layout, "tab-detalle-pdv": page_detalle_pdv.layout,
            "tab-detalle-ceco": page_detalle_ceco.layout, "tab-contraloria": page_contraloria.layout,
            "tab-cierres": page_cierres.layout, "tab-temporalidad": page_temporalidad.layout,
            "tab-comparacion": page_comparacion.layout, "tab-tabla-dinamica": page_tabla_dinamica.layout,
            "tab-plan-conta": page_plan_vs_conta.layout,
        }
        filtros = {"canal": "Fisico", "tipo_monto": "Monto Constante", "modo": "Rango de semanas", "si": 1, "sf": ds.smr,
                   "fg_resp": [], "fg_gpo": [], "fg_cta": [], "fg_subtipo": []}
        precarga.iniciar(app, layouts, filtros, usuario)
    except Exception:
        pass        # la precarga es opcional: nunca debe estorbar al login


# -----------------------------------------------------------------------------
# Callback de Autenticación Universal y Gestión de Sesión
# -----------------------------------------------------------------------------
@app.callback(
    Output("session-user-store", "data"),
    Output("login-error-banner", "children"),
    Output("login-view-container", "style"),
    Output("app-view-container", "style"),
    Input("login-btn-submit", "n_clicks"),
    Input("login-input-user", "n_submit"),
    Input("login-input-password", "n_submit"),
    Input("btn-logout", "n_clicks"),
    State("login-input-user", "value"),
    State("login-input-password", "value"),
    State("session-user-store", "data"),
    prevent_initial_call=False
)
def manage_session(login_clicks, user_submit, pass_submit, logout_clicks, user_val, pass_val, current_session):
    ctx = dash.callback_context
    trig_id = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else ""

    # 1. Clic en Cerrar Sesión
    if trig_id == "btn-logout":
        uso_service.registrar("LOGOUT", uso_service.usuario_de(current_session))
        return None, None, {"display": "block"}, {"display": "none"}

    # 2. Si ya hay sesión activa persistida en sessionStorage
    if current_session and current_session.get("username") and trig_id not in ("login-btn-submit", "login-input-user", "login-input-password"):
        return current_session, None, {"display": "none"}, {"display": "block"}

    # 3. Envío de Login (Clic o Enter)
    if trig_id in ("login-btn-submit", "login-input-user", "login-input-password"):
        user_clean = (user_val or "").strip()
        pass_clean = (pass_val or "").strip()
        user_data = auth.authenticate(user_clean, pass_clean) if user_clean and pass_clean else None
        if user_data:
            uso_service.registrar("LOGIN", user_clean)
            _precargar_pestanas(user_clean)
            return user_data, None, {"display": "none"}, {"display": "block"}
        else:
            uso_service.registrar("LOGIN_FALLIDO", user_clean)
            return None, dbc.Alert("Usuario incorrecto.", color="danger", className="py-2 text-center", style={"fontSize": "0.84rem"}), {"display": "block"}, {"display": "none"}

    # 4. Estado inicial al cargar la página
    return None, None, {"display": "block"}, {"display": "none"}

# -----------------------------------------------------------------------------
# Callback para sincronizar filtros superiores al Store global
# -----------------------------------------------------------------------------
@app.callback(
    Output("ctrl-rango-semanas", "style"),
    Output("ctrl-mes", "style"),
    Output("ctrl-trimestre", "style"),
    Output("ctrl-sem-individual", "style"),
    Input("sidebar-modo-filtro", "value")
)
def toggle_controles_periodo(modo):
    oculto = {"display": "none"}
    visible = {}
    return (
        visible if modo == "Rango de semanas" else oculto,
        visible if modo == "Mes" else oculto,
        visible if modo == "Trimestre" else oculto,
        visible if modo == "Semana individual" else oculto
    )

from datetime import date, timedelta
from collections import defaultdict
from functools import lru_cache

# Mismo ANCLA y algoritmo que app.py del Streamlit original (pesos_mes/rango_mes,
# codigo/app.py líneas ~351/401/457) — reimplementado aquí porque ese app.py no
# se puede importar como módulo (ejecuta código Streamlit a nivel de archivo que
# asume una sesión real corriendo). Es lógica de calendario pura, sin estado.
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
    """Primera/última semana ARG que tocan ese mes (mismo cálculo que rango_mes()
    del Streamlit original)."""
    p = _pesos_mes_arg(anio)
    sems = [s for s, d in p.items() if (anio, mes) in d]
    return (min(sems), max(sems)) if sems else (1, 1)

QRANGO_TRIMESTRE = {"Q1": (1, 13), "Q2": (14, 26), "Q3": (27, 39), "Q4": (40, 53)}


def _semanas_del_periodo(modo, sem_rango, meses, trimestres, sem_ind):
    """Semana inicial y final según el modo de periodo elegido en la barra de filtros."""
    if modo == "Semana individual" and sem_ind:
        return sem_ind, sem_ind
    if modo == "Mes" and meses:
        rangos = [_rango_mes_arg(m) for m in meses]
        return min(r[0] for r in rangos), max(r[1] for r in rangos)
    if modo == "Trimestre" and trimestres:
        semanas = sorted({s for q in trimestres for s in range(QRANGO_TRIMESTRE[q][0], QRANGO_TRIMESTRE[q][1] + 1)})
        return min(semanas), max(semanas)
    return tuple(sem_rango) if sem_rango else (1, ds.smr)


@app.callback(
    Output("sidebar-periodo-badge", "children"),
    Output("tfb-periodo-chip", "children"),
    Input("sidebar-modo-filtro", "value"),
    Input("sidebar-slider-semanas", "value"),
    Input("sidebar-mes-multi", "value"),
    Input("sidebar-tri-multi", "value"),
    Input("sidebar-sem-individual", "value"),
)
def mostrar_periodo(modo, sem_rango, meses, trimestres, sem_ind):
    """Texto del periodo elegido, siempre visible junto al ícono de Cuándo (el panel se cierra al elegir)."""
    modo = modo or "Rango de semanas"
    si, sf = _semanas_del_periodo(modo, sem_rango, meses, trimestres, sem_ind)
    sem = f"Sem {si}" if si == sf else f"Sem {si} – {sf}"
    if modo == "Mes" and meses:
        txt = ", ".join(NOM_MES[m][:3] for m in sorted(meses)) + f" · {sem}"
    elif modo == "Trimestre" and trimestres:
        txt = ", ".join(sorted(trimestres)) + f" · {sem}"
    else:
        txt = sem
    return txt, txt


def _semana_valida(v):
    try:
        return min(max(int(v), 1), ds.smp)
    except (TypeError, ValueError):
        return None


@app.callback(
    Output("sidebar-slider-semanas", "value"),
    Output("sidebar-sem-desde", "value"),
    Output("sidebar-sem-hasta", "value"),
    Input("sidebar-slider-semanas", "value"),
    Input("sidebar-sem-desde", "value"),
    Input("sidebar-sem-hasta", "value"),
    prevent_initial_call=True,
)
def sincronizar_rango_semanas(slider, desde, hasta):
    """Barra deslizable y casillas Desde/Hasta (escritas a mano) siempre muestran lo mismo."""
    disparo = dash.callback_context.triggered[0]["prop_id"].split(".")[0] if dash.callback_context.triggered else ""
    if disparo == "sidebar-slider-semanas":
        return dash.no_update, slider[0], slider[1]
    a, b = _semana_valida(desde), _semana_valida(hasta)
    if a is None or b is None:
        return dash.no_update, slider[0], slider[1]      # casilla vacía o inválida: vuelve al valor vigente
    a, b = min(a, b), max(a, b)
    return [a, b], a, b


@app.callback(
    Output("sidebar-sem-individual", "value"),
    Output("sidebar-sem-num", "value"),
    Input("sidebar-sem-individual", "value"),
    Input("sidebar-sem-num", "value"),
    prevent_initial_call=True,
)
def sincronizar_semana_individual(slider, escrita):
    disparo = dash.callback_context.triggered[0]["prop_id"].split(".")[0] if dash.callback_context.triggered else ""
    if disparo == "sidebar-sem-individual":
        return dash.no_update, slider
    s = _semana_valida(escrita)
    if s is None:
        return dash.no_update, slider
    return s, s

@app.callback(
    Output("global-filter-store", "data"),
    Input("sidebar-tipo-monto", "value"),
    Input("sidebar-modo-filtro", "value"),
    Input("sidebar-slider-semanas", "value"),
    Input("sidebar-mes-multi", "value"),
    Input("sidebar-tri-multi", "value"),
    Input("sidebar-sem-individual", "value"),
    Input("sidebar-fg-resp", "value"),
    Input("sidebar-fg-gpo", "value"),
    Input("sidebar-fg-cta", "value"),
    Input("sidebar-fg-subtipo", "value"),
    Input("sidebar-canal", "value"),
    prevent_initial_call=True
)
def sync_global_filters(tipo_monto, modo, sem_rango, meses, trimestres, sem_ind, resp, gpo, cta, subtipo, canal):
    modo = modo or "Rango de semanas"
    canal = canal or "Fisico"

    si, sf = _semanas_del_periodo(modo, sem_rango, meses, trimestres, sem_ind)

    # Igual regla que el Streamlit original: si el usuario no eligió cuentas
    # explícitas pero sí un Responsable (sin Grupo), se filtra por las cuentas
    # EXACTAS de ese responsable — no por el grupo completo (evita traer el
    # gasto completo de grupos compartidos como "Gastos de Operación").
    # 2026-10-21: Responsable no existe como columna en ningún agregado, así que se traduce a
    # cuentas y se CRUZA con Cuenta y Grupo (antes, con Grupo elegido, el Responsable se perdía).
    # Con una sola dimensión (solo Grupo) no hace falta: ese filtro ya aplica por columna.
    # Intersección vacía -> centinela, para que devuelva 0 filas y no "sin filtro".
    # Subtipo NO se traduce a cuentas: no es atributo de la cuenta (877 pares cuenta-subtipo).
    cta_efectiva = list(cta) if cta else []
    if resp or cta_efectiva:
        conjuntos = []
        if cta_efectiva:
            conjuntos.append(set(cta_efectiva))
        if resp:
            conjuntos.append({c for r in resp for c in ds.responsables_cta.get(r, [])})
        if gpo:
            conjuntos.append({c for c, g in ds.cta_a_gpo.items() if g in set(gpo)})
        cta_efectiva = sorted(set.intersection(*conjuntos)) or ["__sin_cuentas__"]

    # global.parquet (KPIs/gráficas de Resumen) NO trae cat_Cuentas, solo
    # ID_CONCEPTO_CUENTA_NIV3 — igual que el sidebar original, se resuelve el
    # filtro de Cuenta a esos IDs para que también aplique ahí (antes se
    # ignoraba en silencio: "filtré por Cuenta y no se aplicó").
    cta_ids = []
    if cta_efectiva:
        try:
            cta_ids = sorted(
                ds.ddb.distintos_agg("cuentas", "ID_CONCEPTO_CUENTA_NIV3", {"canal": canal},
                                     extra={"cat_Cuentas": cta_efectiva})
            )
        except Exception:
            pass

    return {
        "canal": canal,
        "tipo_monto": tipo_monto or "Monto Constante",
        "modo": modo,
        "si": si,
        "sf": sf,
        "fg_resp": resp or [],
        "fg_gpo": gpo or [],
        "fg_cta": cta_efectiva,
        "fg_cta_ids": cta_ids,
        "fg_subtipo": subtipo or [],
        "meses": list(meses or []),
        "trimestres": list(trimestres or []),
    }

# -----------------------------------------------------------------------------
# Subtítulo del encabezado: tipo de Monto (Constante/Local) activo + aviso de
# que las cifras están en Millones de Pesos (MDP) — pedido del usuario, no
# vivía en ningún lado del encabezado. create_header() se renderiza UNA vez
# en el layout estático (antes de que exista ningún filtro), por eso es un
# callback aparte atado al global-filter-store en vez de texto fijo.
# -----------------------------------------------------------------------------
@app.callback(
    Output("header-tipo-monto-badge", "children"),
    Input("global-filter-store", "data"),
)
def update_header_tipo_monto(filters):
    tm = (filters or {}).get("tipo_monto", "Monto Constante")
    etiqueta = "Constante" if tm == "Monto Constante" else "Local"
    return f"{etiqueta} · MDP"

# -----------------------------------------------------------------------------
# Cascada de filtros: Responsable -> opciones de Grupo -> opciones de Cuenta
# (igual al sidebar original: elegir un Responsable acota qué Grupos/Cuentas
# se pueden elegir después, en vez de mostrar siempre el catálogo completo).
# -----------------------------------------------------------------------------
@app.callback(
    Output("sidebar-fg-gpo", "options"),
    Input("sidebar-fg-resp", "value")
)
def cascada_grupos_por_responsable(resp):
    if not resp:
        return [{"label": g, "value": g} for g in ds.grupos]
    gpos_resp = set()
    for r in resp:
        gpos_resp.update(ds.responsables_gpo.get(r, []))
    gpos_filtrados = [g for g in ds.grupos if g in gpos_resp]
    return [{"label": g, "value": g} for g in gpos_filtrados]

@app.callback(
    Output("sidebar-fg-cta", "options"),
    Input("sidebar-fg-resp", "value"),
    Input("sidebar-fg-gpo", "value"),
    Input("sidebar-canal", "value")
)
def cascada_cuentas_por_grupo_o_responsable(resp, gpo, canal):
    ctas = ds.cuentas
    if gpo:
        try:
            ctas = sorted(ds.ddb.distintos_agg("cuentas", "cat_Cuentas", {"canal": canal},
                                               extra={"cat_Grupo_de_Cuentas": gpo}))
        except Exception:
            pass
    elif resp:
        ctas_resp = set()
        for r in resp:
            ctas_resp.update(ds.responsables_cta.get(r, []))
        ctas = sorted(ctas_resp) if ctas_resp else ds.cuentas
    return [{"label": c, "value": c} for c in ctas]

# -----------------------------------------------------------------------------
# Callback para conmutar las páginas
# -----------------------------------------------------------------------------
@app.callback(
    Output("page-content-area", "children"),
    Input("app-navigation-tabs", "active_tab"),
    State("session-user-store", "data"),
)
def render_page_content(active_tab, sesion=None):
    t0 = _time.perf_counter()
    contenido = _armar_pagina(active_tab)
    if active_tab:
        uso_service.registrar("NAVEGACION", uso_service.usuario_de(sesion), pestana=active_tab,
                              ms=(_time.perf_counter() - t0) * 1000.0)
    return contenido


def _armar_pagina(active_tab):
    if active_tab == "tab-resumen":
        return page_resumen.layout()
    elif active_tab == "tab-sobregasto":
        return page_sobregasto.layout()
    elif active_tab == "tab-detalle-cuenta":
        return page_detalle_cuenta.layout()
    elif active_tab == "tab-detalle-pdv":
        return page_detalle_pdv.layout()
    elif active_tab == "tab-detalle-ceco":
        return page_detalle_ceco.layout()
    elif active_tab == "tab-contraloria":
        return page_contraloria.layout()
    elif active_tab == "tab-cierres":
        return page_cierres.layout()
    elif active_tab == "tab-temporalidad":
        return page_temporalidad.layout()
    elif active_tab == "tab-comparacion":
        return page_comparacion.layout()
    elif active_tab == "tab-tabla-dinamica":
        return page_tabla_dinamica.layout()
    elif active_tab == "tab-plan-conta":
        return page_plan_vs_conta.layout()
    elif active_tab == "tab-cashi":
        return page_cashi.layout()
    elif active_tab == "tab-admin":
        return page_admin_usuarios.layout()
    return html.Div("Página en desarrollo.")

def _filtros_efectivos_arbol(filters, tree_search, ancestor_path):
    """Filtros con que se consultan los hijos de un nodo: globales + buscador/filtros locales del árbol + ruta de
    ancestros (cada (col, val) por encima del nodo, además del parent_col/parent_val que recibe la consulta).
    Lo usan 'expandir' y 'Mostrar más' para que ambos den los mismos montos que la raíz."""
    eff = dict(filters) if filters else {}
    if tree_search:
        eff.update({k: v for k, v in tree_search.items() if k != "tree_key"})
    for anc_col, anc_val in (ancestor_path or [])[:-1]:
        if anc_col:
            eff[anc_col] = [anc_val]
    return eff

# -----------------------------------------------------------------------------
# Pattern-Matching Callback para expandir/colapsar ramas del árbol lazy (<5ms con MATCH)
# -----------------------------------------------------------------------------
@app.callback(
    Output({"type": "tree-children", "tree": MATCH, "node": MATCH}, "style"),
    Output({"type": "tree-children", "tree": MATCH, "node": MATCH}, "children"),
    Output({"type": "tree-toggle-btn", "tree": MATCH, "node": MATCH}, "children"),
    Input({"type": "tree-toggle-btn", "tree": MATCH, "node": MATCH}, "n_clicks"),
    State({"type": "tree-children", "tree": MATCH, "node": MATCH}, "style"),
    State({"type": "tree-children", "tree": MATCH, "node": MATCH}, "children"),
    State({"type": "tree-toggle-btn", "tree": MATCH, "node": MATCH}, "id"),
    State("global-filter-store", "data"),
    State("pdv-search-filters", "data"),
    State("ceco-search-filters", "data"),
    State("cuenta-search-filters", "data"),
    State("cierres-search-filters", "data"),
    prevent_initial_call=True
)
def toggle_tree_branch(n_clicks, current_style, current_children, btn_id, filters,
                        search_pdv, search_ceco, search_cuenta, search_cierres):
    from dash_app.components.tree_view import render_tree_node, decode_node_meta

    if not n_clicks:
        return dash.no_update, dash.no_update, "+"

    is_open = (n_clicks % 2 == 1)
    new_style = {"display": "block"} if is_open else {"display": "none"}
    new_btn_label = "-" if is_open else "+"

    if is_open and (not current_children or len(current_children) == 0):
        tree_k = btn_id.get("tree", "tree")
        meta = decode_node_meta(btn_id.get("node", ""))
        node_val = meta.get("val", "")
        lvl = meta.get("lvl", 0) + 1
        pcol = meta.get("pcol", "")
        tcol = meta.get("tcol", "")
        cols = meta.get("cols", [])
        # Ruta completa de ancestros (col, val) desde la raíz hasta este nodo
        # incluido — sin esto, un nivel 3+ (Zona->Región, etc.) solo filtraba
        # por su columna inmediata y mezclaba ramas distintas que comparten el
        # mismo valor (típico con "0"/blanco), disparando el monto por encima
        # del total del padre. Ver nota en tree_view.render_tree_node.
        ancestor_path = meta.get("path", [[pcol, node_val]] if pcol else [])

        si = filters.get("si", 1) if filters else 1
        sf = filters.get("sf", ds.smr) if filters else ds.smr
        tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"

        # Cada página escribe su propio store de búsqueda (ver comentario en
        # app.layout) — se elige aquí el que corresponda a este árbol por
        # tree_key, y se fusiona con los filtros globales.
        tree_search = None
        for s in (search_pdv, search_ceco, search_cuenta, search_cierres):
            if s and s.get("tree_key") == tree_k:
                tree_search = s
                break

        filters_eff = _filtros_efectivos_arbol(filters, tree_search, ancestor_path)

        df_sub = ds.query_tree_children_fast(tree_k, pcol, node_val, tcol, si=si, sf=sf, tipo_monto=tm, filters=filters_eff)

        if df_sub is not None and not df_sub.empty:
            ch_nodes = []
            next_pcol = tcol
            next_tcol = cols[1] if len(cols) > 1 else ""
            is_sub_leaf = (len(cols) <= 1)
            remaining_cols = cols[1:] if len(cols) > 1 else []

            batch_size = 7
            initial_df = df_sub.iloc[:batch_size]

            for _, r_sub in initial_df.iterrows():
                ch_nodes.append(render_tree_node(
                    r_sub,
                    level=lvl,
                    is_leaf=is_sub_leaf,
                    tree_key=tree_k,
                    parent_col=next_pcol,
                    target_col=next_tcol,
                    next_cols=remaining_cols,
                    inc_fc=meta.get("fc", False),
                    inc_fcs=meta.get("fcs", False),
                    ancestor_path=ancestor_path
                ))

            # Contenedor para filas cargadas progresivamente vía "Cargar más"
            more_container = html.Div(
                id={"type": "tree-more-container", "tree": tree_k, "node": btn_id.get("node", "")},
                children=[]
            )

            # Botón "Cargar más" estilo iOS si hay más de 7 elementos
            remaining_count = len(df_sub) - batch_size
            load_more_btn = None
            if remaining_count > 0:
                load_more_btn = html.Div(
                    className="vt-load-more-wrap",
                    style={"paddingLeft": f"{(lvl*20)+24}px"},
                    children=[
                        html.Button(
                            f"Mostrar {batch_size} más ({remaining_count:,} restantes)",
                            id={"type": "tree-load-more-btn", "tree": tree_k, "node": btn_id.get("node", "")},
                            className="vt-load-more-btn",
                            n_clicks=0
                        )
                    ]
                )

            full_branch_children = ch_nodes + [more_container] + ([load_more_btn] if load_more_btn else [])
            return new_style, full_branch_children, new_btn_label
        else:
            return new_style, [html.Div("Sin sub-elementos para este nivel", style={"paddingLeft": f"{(lvl*20)+24}px", "color": "#8e8e93", "fontSize": "0.82rem", "paddingTop": "6px", "paddingBottom": "6px"})], new_btn_label

    return new_style, dash.no_update, new_btn_label

# -----------------------------------------------------------------------------
# Callback para carga progresiva de ramas del árbol ("Cargar más" de 7 en 7)
# -----------------------------------------------------------------------------
@app.callback(
    Output({"type": "tree-more-container", "tree": MATCH, "node": MATCH}, "children"),
    Output({"type": "tree-load-more-btn", "tree": MATCH, "node": MATCH}, "children"),
    Output({"type": "tree-load-more-btn", "tree": MATCH, "node": MATCH}, "style"),
    Input({"type": "tree-load-more-btn", "tree": MATCH, "node": MATCH}, "n_clicks"),
    State({"type": "tree-more-container", "tree": MATCH, "node": MATCH}, "children"),
    State({"type": "tree-load-more-btn", "tree": MATCH, "node": MATCH}, "id"),
    State("global-filter-store", "data"),
    State("pdv-search-filters", "data"),
    State("ceco-search-filters", "data"),
    State("cuenta-search-filters", "data"),
    State("cierres-search-filters", "data"),
    prevent_initial_call=True
)
def load_more_tree_branch(n_clicks, existing_more, btn_id, filters,
                          search_pdv, search_ceco, search_cuenta, search_cierres):
    from dash_app.components.tree_view import render_tree_node, decode_node_meta

    if not n_clicks:
        return dash.no_update, dash.no_update, dash.no_update

    tree_k = btn_id.get("tree", "tree")
    meta = decode_node_meta(btn_id.get("node", ""))
    node_val = meta.get("val", "")
    lvl = meta.get("lvl", 0) + 1
    pcol = meta.get("pcol", "")
    tcol = meta.get("tcol", "")
    cols = meta.get("cols", [])
    ancestor_path = meta.get("path", [[pcol, node_val]] if pcol else [])

    si = filters.get("si", 1) if filters else 1
    sf = filters.get("sf", ds.smr) if filters else ds.smr
    tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"

    tree_search = next((s for s in (search_pdv, search_ceco, search_cuenta, search_cierres)
                        if s and s.get("tree_key") == tree_k), None)
    filters_eff = _filtros_efectivos_arbol(filters, tree_search, ancestor_path)

    svc = ds
    df_sub = svc.query_tree_children_fast(tree_k, pcol, node_val, tcol, si=si, sf=sf, tipo_monto=tm, filters=filters_eff)
    if df_sub is None or df_sub.empty:
        return dash.no_update, dash.no_update, {"display": "none"}

    batch_size = 7
    start_idx = 7 + (n_clicks - 1) * batch_size
    end_idx = start_idx + batch_size

    slice_df = df_sub.iloc[start_idx:end_idx]
    if slice_df.empty:
        return dash.no_update, dash.no_update, {"display": "none"}

    next_pcol = tcol
    next_tcol = cols[1] if len(cols) > 1 else ""
    is_sub_leaf = (len(cols) <= 1)
    remaining_cols = cols[1:] if len(cols) > 1 else []

    new_nodes = []
    for _, r_sub in slice_df.iterrows():
        new_nodes.append(render_tree_node(
            r_sub,
            level=lvl,
            is_leaf=is_sub_leaf,
            tree_key=tree_k,
            parent_col=next_pcol,
            target_col=next_tcol,
            next_cols=remaining_cols,
            inc_fc=meta.get("fc", False),
            inc_fcs=meta.get("fcs", False),
            ancestor_path=ancestor_path
        ))

    combined = (existing_more or []) + new_nodes
    remaining_count = len(df_sub) - end_idx
    if remaining_count > 0:
        btn_label = f"Mostrar {batch_size} más ({remaining_count:,} restantes)"
        btn_style = {"display": "inline-block"}
    else:
        btn_label = ""
        btn_style = {"display": "none"}

    return combined, btn_label, btn_style

if __name__ == "__main__":
    # Producción: servidor WSGI real (waitress) con varios hilos, no el servidor
    # de desarrollo de Flask/Werkzeug (single-threaded, no apto para 50+ usuarios
    # concurrentes). DuckDBService/query_tree_children_fast ya usan conexión
    # segura por hilo (con.cursor() por query), así que el multi-threading real
    # no choca entre usuarios.
    try:
        from waitress import serve
        serve(server, host="0.0.0.0", port=8050, threads=32)
    except ImportError:
        app.run(host="0.0.0.0", port=8050, debug=False, threaded=True)
