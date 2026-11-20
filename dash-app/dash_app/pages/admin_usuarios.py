# -*- coding: utf-8 -*-
"""
dash_app/pages/admin_usuarios.py
Módulo Ejecutivo de Gestión de Usuarios, Roles y Permisos (RBAC).
Permite crear, editar contraseñas, asignar roles, autorizar pestañas y eliminar usuarios con persistencia directa en datos/usuarios.json.
"""
from dash import html, dcc, callback, Input, Output, State, ALL, ctx
from dash.exceptions import PreventUpdate
import dash_bootstrap_components as dbc
from dash_app.services.auth_service import AuthService, ROLES_PERMISOS
from dash_app.services.cashi_log_service import read_last_queries
from dash_app.services import export_service

auth = AuthService()

TODAS_LAS_SECCIONES = [
    "Resumen", "Sobregasto PDV / CECO", "Detalle Cuenta", "Detalle PDV", "Detalle CECO",
    "Contraloria", "Cierres", "Temporalidad", "Comparación de Versiones", "Tabla Dinámica",
    "Cashi AI Assistant", "Gestión de Usuarios"
]

def _generar_tabla_usuarios():
    users = auth.get_all_users()
    rows = []
    
    total_users = len(users)
    admins = sum(1 for u in users if u["rol"] == "admin")
    supervisores = sum(1 for u in users if u["rol"] == "supervisor")
    analistas = sum(1 for u in users if u["rol"] == "analista")

    for u in users:
        u_name = u["username"]
        es_super = u_name.lower() == "admin1"
        
        rol_label = "Administrador" if u["rol"] == "admin" else ("Supervisor" if u["rol"] == "supervisor" else "Analista")
        
        descarga_badge = html.Span("Permitido", className="al-sem prev") if u["permite_descargar"] else html.Span("Restringido", className="al-sem crit")

        num_sec = len(u["secciones"])
        sec_text = f"Todas ({num_sec})" if num_sec >= len(TODAS_LAS_SECCIONES) else f"{num_sec} autorizadas"

        btn_edit = dbc.Button(
            "Editar",
            id={"type": "admin-btn-edit", "user": u_name},
            size="sm",
            color="secondary",
            outline=True,
            className="me-2 py-0 px-2",
            style={"fontSize": "0.78rem"}
        )
        
        btn_del = dbc.Button(
            "Eliminar",
            id={"type": "admin-btn-del", "user": u_name},
            size="sm",
            color="danger",
            outline=True,
            disabled=es_super,
            className="py-0 px-2",
            style={"fontSize": "0.78rem"}
        )

        rows.append(
            html.Tr([
                html.Td(html.Strong(u_name, style={"color": "#1e293b"}), style={"verticalAlign": "middle"}),
                html.Td(u["nombre_completo"], style={"verticalAlign": "middle", "color": "#334155"}),
                html.Td(html.Span(rol_label, className="al-sem"), style={"verticalAlign": "middle"}),
                html.Td(sec_text, style={"verticalAlign": "middle", "color": "#475569", "fontSize": "0.82rem"}),
                html.Td(descarga_badge, style={"verticalAlign": "middle"}),
                html.Td(html.Div([btn_edit, btn_del], className="d-flex align-items-center"), style={"verticalAlign": "middle"})
            ])
        )

    def _kpi(etiqueta, valor):
        return html.Div(className="ad-kpi", children=[html.Span(etiqueta, className="ad-kpi-lbl"), html.Span(f"{valor}", className="ad-kpi-val")])

    kpis_header = html.Div(className="ad-kpis", children=[
        _kpi("Usuarios", total_users), _kpi("Administradores", admins), _kpi("Supervisores", supervisores), _kpi("Analistas", analistas),
    ])

    th = lambda txt: html.Th(txt)
    table_card = html.Div(
        className="al-card",
        children=[
            html.Div(className="al-card-head", children=[html.H6("Usuarios y permisos", className="al-card-title")]),
            html.Div(className="ad-tabla", children=dbc.Table(
                hover=True, responsive=True, className="mb-0 align-middle",
                children=[
                    html.Thead(html.Tr([th("Usuario"), th("Nombre completo"), th("Rol"), th("Pestañas autorizadas"), th("Descargas"), th("Acciones")])),
                    html.Tbody(rows)
                ]
            ))
        ]
    )
    return html.Div([kpis_header, table_card])

def _leer_descargas(n=200):
    """Últimas `n` descargas (exitosas y fallidas) de datos/bitacora_descargas.jsonl, más recientes primero."""
    import json
    try:
        with open(export_service.AUDIT_FILE, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4_000_000))
            lineas = f.read().decode("utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    out = []
    for ln in reversed(lineas):
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
        if len(out) >= n:
            break
    return out


def _generar_tabla_descargas():
    """Sección de solo lectura: quién descargó qué y cuándo (últimas 200), con el resumen por usuario."""
    registros = _leer_descargas(200)
    if not registros:
        cuerpo = html.Div("Aún no hay descargas registradas.", className="ad-vacio")
        resumen = None
    else:
        conteo = {}
        for r in registros:
            u = r.get("usuario") or "Desconocido"
            u = "Sin identificar" if u == "Desconocido" else u
            conteo[u] = conteo.get(u, 0) + 1
        resumen = html.Div(className="ad-chips", children=[
            html.Span([html.B(u), f" · {c} descargas"], className="al-pill") for u, c in sorted(conteo.items(), key=lambda x: -x[1])
        ])
        rows = []
        for r in registros:
            u = r.get("usuario") or "Desconocido"
            ok = r.get("exitosa", True)
            fecha = str(r.get("timestamp", ""))[:19].replace("T", " ")
            filas = r.get("filas")
            rows.append(html.Tr([
                html.Td(html.Strong("Sin identificar" if u == "Desconocido" else u)),
                html.Td(fecha, className="ad-fecha"),
                html.Td(r.get("archivo", "")),
                html.Td(f"{filas:,}" if isinstance(filas, (int, float)) else "—", className="ad-num"),
                html.Td(html.Span("Descargado" if ok else "Falló", className="al-sem prev" if ok else "al-sem crit")),
            ]))
        cuerpo = html.Div(className="ad-tabla ad-scroll", children=dbc.Table(
            hover=True, responsive=True, size="sm", className="mb-0 align-middle",
            children=[html.Thead(html.Tr([html.Th("Usuario"), html.Th("Fecha y hora"), html.Th("Archivo"), html.Th("Filas"), html.Th("Estado")])),
                      html.Tbody(rows)]))
    return html.Div(className="al-card", children=[
        html.Div(className="al-card-head", children=[
            html.H6("Historial de descargas", className="al-card-title"),
            html.Span(f"Últimas {len(registros)}", className="al-pill al-pill-dark"),
        ]),
        resumen if resumen is not None else html.Div(),
        cuerpo,
    ])


def _generar_tabla_cashi_queries():
    """Sección de solo lectura: últimas consultas que los usuarios hicieron a Cashi."""
    registros = read_last_queries(200)
    if not registros:
        cuerpo = html.Div("Aún no hay consultas registradas de Cashi.", className="ad-vacio")
    else:
        rows = []
        for r in registros:
            ok = r.get("exitosa", True)
            rows.append(html.Tr([
                html.Td(html.Strong(r.get("usuario", ""))),
                html.Td(r.get("timestamp", ""), className="ad-fecha"),
                html.Td(r.get("pregunta", "")),
                html.Td(r.get("intencion_detectada", "")),
                html.Td(html.Span("OK" if ok else "Error", className="al-sem prev" if ok else "al-sem crit")),
            ]))
        cuerpo = html.Div(className="ad-tabla ad-scroll", children=dbc.Table(
            hover=True, responsive=True, size="sm", className="mb-0 align-middle",
            children=[html.Thead(html.Tr([html.Th("Usuario"), html.Th("Fecha y hora"), html.Th("Pregunta"), html.Th("Intención detectada"), html.Th("Estado")])),
                      html.Tbody(rows)]))
    return html.Div(className="al-card", children=[
        html.Div(className="al-card-head", children=[
            html.Div([html.H6("Consultas a Cashi", className="al-card-title"),
                      html.Span("Más recientes primero", className="al-sub")]),
            html.Span(f"Últimas {len(registros)}", className="al-pill al-pill-dark"),
        ]),
        cuerpo,
    ])

def layout():
    return html.Div(
        className="p-3",
        children=[
            dcc.Store(id="admin-reload-trigger", data=0),
            
            # Modal para Crear / Editar Usuario
            dbc.Modal(
                id="admin-modal-usuario",
                is_open=False,
                size="lg",
                centered=True,
                backdrop="static",
                children=[
                    dbc.ModalHeader(
                        dbc.ModalTitle(id="admin-modal-title", children="Nuevo Usuario"),
                        close_button=True
                    ),
                    dbc.ModalBody(
                        children=[
                            html.Div(id="admin-modal-alert", className="mb-3"),
                            dcc.Store(id="admin-modal-is-edit", data=False),
                            dbc.Row(
                                className="g-3",
                                children=[
                                    dbc.Col(
                                        children=[
                                            html.Label("Nombre de Usuario (Login)", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                                            dbc.Input(id="admin-form-username", placeholder="ej. JPEREZ", maxLength=30)
                                        ],
                                        width=6
                                    ),
                                    dbc.Col(
                                        children=[
                                            html.Label("Contraseña", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                                            dbc.Input(id="admin-form-password", type="password", placeholder="Contraseña de acceso")
                                        ],
                                        width=6
                                    ),
                                    dbc.Col(
                                        children=[
                                            html.Label("Nombre Completo", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                                            dbc.Input(id="admin-form-nombre", placeholder="Nombre y Apellidos", maxLength=80)
                                        ],
                                        width=12
                                    ),
                                    dbc.Col(
                                        children=[
                                            html.Label("Rol Institucional", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                                            dcc.Dropdown(
                                                id="admin-form-rol",
                                                options=[
                                                    {"label": "Administrador (Acceso total + Administración)", "value": "admin"},
                                                    {"label": "Supervisor / Dirección (Acceso a reportes y descargas)", "value": "supervisor"},
                                                    {"label": "Analista Financiero (Acceso a vistas analíticas)", "value": "analista"}
                                                ],
                                                value="analista",
                                                clearable=False
                                            )
                                        ],
                                        width=6
                                    ),
                                    dbc.Col(
                                        children=[
                                            html.Label("Permiso de Descarga de Datos (CSV/Excel)", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                                            dbc.Checklist(
                                                id="admin-form-descargar",
                                                options=[{"label": " Habilitar descargas para este usuario", "value": "si"}],
                                                value=["si"],
                                                switch=True,
                                                className="mt-2"
                                            )
                                        ],
                                        width=6
                                    ),
                                    dbc.Col(
                                        children=[
                                            html.Label("Pestañas Autorizadas", style={"fontWeight": "600", "fontSize": "0.85rem"}),
                                            dcc.Dropdown(
                                                id="admin-form-secciones",
                                                options=[{"label": s, "value": s} for s in TODAS_LAS_SECCIONES],
                                                value=ROLES_PERMISOS["analista"]["secciones"],
                                                multi=True
                                            )
                                        ],
                                        width=12
                                    )
                                ]
                            )
                        ]
                    ),
                    dbc.ModalFooter([
                        dbc.Button("Cancelar", id="admin-modal-btn-cancelar", color="light", className="me-2"),
                        dbc.Button("Guardar Usuario", id="admin-modal-btn-guardar", color="primary")
                    ])
                ]
            ),

            # Modal de Confirmación para Eliminar (el Store va fuera: el modal no monta su contenido cerrado)
            dcc.Store(id="admin-delete-target-user"),
            dbc.Modal(
                id="admin-modal-delete",
                is_open=False,
                centered=True,
                children=[
                    dbc.ModalHeader(dbc.ModalTitle("Confirmar Eliminación")),
                    dbc.ModalBody([
                        html.P(id="admin-delete-confirm-text", className="mb-0")
                    ]),
                    dbc.ModalFooter([
                        dbc.Button("Cancelar", id="admin-del-btn-cancelar", color="light", className="me-2"),
                        dbc.Button("Eliminar Definitivamente", id="admin-del-btn-confirmar", color="danger")
                    ])
                ]
            ),

            # Barra Superior de Control
            html.Div(
                className="d-flex justify-content-between align-items-center mb-3 p-3 bg-white border-0 shadow-sm",
                style={"borderRadius": "12px"},
                children=[
                    html.Div([
                        html.H5("Gestión de Usuarios, Roles y Seguridad (RBAC)", style={"fontWeight": "700", "color": "#1e293b", "margin": "0"}),
                        html.P("Administración centralizada de credenciales, privilegios y auditoría de acceso", style={"color": "#64748b", "fontSize": "0.85rem", "margin": "2px 0 0 0"})
                    ]),
                    dbc.Button(
                        "+ Agregar Nuevo Usuario",
                        id="admin-btn-nuevo-usuario",
                        color="primary",
                        style={"backgroundColor": "#1e3a8a", "borderColor": "#1e3a8a", "fontWeight": "600", "borderRadius": "8px"}
                    )
                ]
            ),

            # Banner de Notificaciones de Estado
            html.Div(id="admin-status-banner", className="mb-3"),

            # Contenedor Dinámico de la Tabla
            html.Div(id="admin-users-table-container", children=_generar_tabla_usuarios()),

            # Sección de solo lectura: Consultas a Cashi
            html.Div(id="admin-cashi-queries-container", children=_generar_tabla_cashi_queries()),

            # Sección de solo lectura: Historial de descargas
            html.Div(id="admin-descargas-container", children=_generar_tabla_descargas())
        ]
    )

# -----------------------------------------------------------------------------
# Callbacks para Gestión de Usuarios
# -----------------------------------------------------------------------------
@callback(
    Output("admin-form-secciones", "value"),
    Input("admin-form-rol", "value"),
    State("admin-modal-is-edit", "data"),
    State("admin-form-secciones", "value"),
    prevent_initial_call=True
)
def actualizar_secciones_por_rol(nuevo_rol, es_edicion, current_secciones):
    if es_edicion and current_secciones:
        return current_secciones
    if nuevo_rol in ROLES_PERMISOS:
        return ROLES_PERMISOS[nuevo_rol]["secciones"]
    return TODAS_LAS_SECCIONES

@callback(
    Output("admin-modal-usuario", "is_open"),
    Output("admin-modal-title", "children"),
    Output("admin-form-username", "value"),
    Output("admin-form-username", "disabled"),
    Output("admin-form-password", "value"),
    Output("admin-form-password", "placeholder"),
    Output("admin-form-nombre", "value"),
    Output("admin-form-rol", "value"),
    Output("admin-form-descargar", "value"),
    Output("admin-form-secciones", "value", allow_duplicate=True),
    Output("admin-modal-is-edit", "data"),
    Output("admin-modal-alert", "children"),
    Input("admin-btn-nuevo-usuario", "n_clicks"),
    Input({"type": "admin-btn-edit", "user": ALL}, "n_clicks"),
    Input("admin-modal-btn-cancelar", "n_clicks"),
    State("admin-modal-usuario", "is_open"),
    prevent_initial_call=True
)
def toggle_modal_usuario(n_nuevo, n_edits, n_cancel, is_open):
    try:
        triggered = ctx.triggered_id
    except Exception:
        triggered = None

    if triggered == "admin-modal-btn-cancelar":
        return False, "", "", False, "", "", "", "analista", ["si"], [], False, None

    if triggered == "admin-btn-nuevo-usuario":
        return (
            True, "Nuevo Usuario", "", False, "", "Contraseña de acceso",
            "", "analista", ["si"], ROLES_PERMISOS["analista"]["secciones"], False, None
        )

    if isinstance(triggered, dict) and triggered.get("type") == "admin-btn-edit":
        if not (ctx.triggered and ctx.triggered[0]["value"]):
            raise PreventUpdate   # botón recién pintado, no un clic real
        target_user = triggered.get("user")
        users = auth.get_all_users()
        found = next((u for u in users if u["username"] == target_user), None)
        if found:
            desc_val = ["si"] if found["permite_descargar"] else []
            return (
                True, f"Editar Usuario — {target_user}", target_user, True, "",
                "Dejar en blanco para mantener la contraseña actual",
                found["nombre_completo"], found["rol"], desc_val,
                found["secciones"], True, None
            )

    return is_open, "", "", False, "", "", "", "analista", ["si"], [], False, None

@callback(
    Output("admin-reload-trigger", "data"),
    Output("admin-status-banner", "children"),
    Output("admin-modal-usuario", "is_open", allow_duplicate=True),
    Output("admin-modal-alert", "children", allow_duplicate=True),
    Input("admin-modal-btn-guardar", "n_clicks"),
    State("admin-form-username", "value"),
    State("admin-form-password", "value"),
    State("admin-form-nombre", "value"),
    State("admin-form-rol", "value"),
    State("admin-form-descargar", "value"),
    State("admin-form-secciones", "value"),
    State("admin-modal-is-edit", "data"),
    State("admin-reload-trigger", "data"),
    prevent_initial_call=True
)
def guardar_usuario(n_guardar, username, password, nombre, rol, descargar, secciones, es_edicion, reload_trigger):
    if not n_guardar:
        return reload_trigger, None, False, None

    u_clean = str(username or "").strip()
    if not u_clean:
        alert = dbc.Alert("El nombre de usuario es obligatorio.", color="danger")
        return reload_trigger, None, True, alert

    if not es_edicion and not password:
        alert = dbc.Alert("Debe especificar una contraseña para el nuevo usuario.", color="danger")
        return reload_trigger, None, True, alert

    permite_desc = "si" in (descargar or [])
    sec_list = secciones or ROLES_PERMISOS.get(rol, {}).get("secciones", TODAS_LAS_SECCIONES)

    ok, msg = auth.add_or_update_user(
        username=u_clean,
        password=password if password else None,
        nombre_completo=nombre,
        rol=rol,
        permite_descargar=permite_desc,
        secciones=sec_list
    )

    if ok:
        banner = dbc.Alert(msg, color="success", dismissable=True)
        return (reload_trigger or 0) + 1, banner, False, None
    else:
        alert = dbc.Alert(msg, color="danger")
        return reload_trigger, None, True, alert

@callback(
    Output("admin-modal-delete", "is_open"),
    Output("admin-delete-target-user", "data"),
    Output("admin-delete-confirm-text", "children"),
    Input({"type": "admin-btn-del", "user": ALL}, "n_clicks"),
    Input("admin-del-btn-cancelar", "n_clicks"),
    prevent_initial_call=True
)
def toggle_modal_delete(n_dels, n_cancel):
    # Guard: con inputs ALL, Dash también dispara este callback cuando la tabla se vuelve a pintar
    # (botones nuevos) y el n_clicks viejo de otros botones sigue guardado. Solo se actúa si el
    # botón que DISPARÓ tiene un clic real (valor > 0), no si "algún" botón fue clicado alguna vez.
    try:
        triggered = ctx.triggered_id
        valor = ctx.triggered[0]["value"] if ctx.triggered else None
    except Exception:
        raise PreventUpdate
    if not valor:
        raise PreventUpdate
    if triggered == "admin-del-btn-cancelar":
        return False, None, ""

    if isinstance(triggered, dict) and triggered.get("type") == "admin-btn-del":
        target = triggered.get("user")
        return True, target, f"¿Está seguro de que desea eliminar definitivamente al usuario '{target}'? Esta acción no se puede deshacer."

    return False, None, ""

@callback(
    Output("admin-reload-trigger", "data", allow_duplicate=True),
    Output("admin-status-banner", "children", allow_duplicate=True),
    Output("admin-modal-delete", "is_open", allow_duplicate=True),
    Input("admin-del-btn-confirmar", "n_clicks"),
    State("admin-delete-target-user", "data"),
    State("admin-reload-trigger", "data"),
    prevent_initial_call=True
)
def confirmar_eliminar_usuario(n_confirm, target_user, reload_trigger):
    if not n_confirm or not target_user:
        return reload_trigger, None, False

    ok, msg = auth.delete_user(target_user)
    color = "success" if ok else "danger"
    banner = dbc.Alert(msg, color=color, dismissable=True)
    return (reload_trigger or 0) + 1, banner, False

@callback(
    Output("admin-users-table-container", "children"),
    Input("admin-reload-trigger", "data"),
    prevent_initial_call=False
)
def recargar_tabla_usuarios(reload_trigger):
    return _generar_tabla_usuarios()

