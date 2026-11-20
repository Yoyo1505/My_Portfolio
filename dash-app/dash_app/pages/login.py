# -*- coding: utf-8 -*-
"""
dash_app/pages/login.py
Pantalla de Autenticación de Usuarios.
Campos siempre vacíos: sin usuario ni contraseña precargados, sin autocompletado del navegador.
"""
import dash
from dash import html, dcc, callback, Input, Output, State, no_update

ROJO_OSCURO = "#8b1a1a"

# Fondo plateado: degradado diagonal con franja de brillo (metal cepillado)
FONDO_PLATEADO = (
    "linear-gradient(115deg, #bcbcc9 0%, #cfcfda 22%, #ececf2 38%, #fafafc 46%, "
    "#e2e2e9 55%, #c3c4cb 75%, #a9aab1 100%)"
)

ESTILO_ETIQUETA = {
    "fontSize": "0.74rem",
    "fontWeight": "700",
    "textTransform": "uppercase",
    "letterSpacing": "0.06em",
    "color": "#64748b",
    "marginBottom": "8px",
    "display": "block",
}

ESTILO_CAMPO = {
    "backgroundColor": "#ffffff",
    "border": "1.5px solid #94a3b8",
    "borderRadius": "10px",
    "color": "#0f172a",
    "fontWeight": "700",
    "fontSize": "1.05rem",
    "lineHeight": "1.4",
    "padding": "14px 16px",
    "height": "48px",
    "width": "100%",
    "colorScheme": "light",
}


@callback(
    Output("login-input-password", "type"),
    Output("login-toggle-pwd", "children"),
    Input("login-toggle-pwd", "n_clicks"),
    State("login-input-password", "type"),
    prevent_initial_call=True,
)
def _toggle_password_visibility(n_clicks, tipo_actual):
    if tipo_actual == "password":
        return "text", "Ocultar"
    return "password", "Mostrar"


@callback(
    Output("login-input-user", "value"),
    Output("login-input-password", "value"),
    Output("login-input-password", "type", allow_duplicate=True),
    Output("login-toggle-pwd", "children", allow_duplicate=True),
    Input("session-user-store", "data"),
    Input("btn-logout", "n_clicks"),
    prevent_initial_call=True,
)
def _limpiar_campos(sesion, logout_clicks):
    """Tras iniciar o cerrar sesión los campos vuelven a quedar vacíos (usuario solo al cerrar sesión)."""
    disparo = dash.callback_context.triggered[0]["prop_id"].split(".")[0] if dash.callback_context.triggered else ""
    usuario = "" if (sesion or disparo == "btn-logout") else no_update
    return usuario, "", "password", "Mostrar"


def layout():
    return html.Div(
        id="login-main-wrapper",
        style={
            "minHeight": "100vh",
            "background": FONDO_PLATEADO,
            "position": "relative",
            "overflow": "hidden",
            "display": "flex",
            "alignItems": "center",
            "justifyContent": "center",
            "padding": "24px"
        },
        children=[
            html.Div(
                style={
                    "width": "100%",
                    "maxWidth": "440px",
                    "backgroundColor": "rgba(255, 255, 255, 0.97)",
                    "border": "1px solid rgba(255, 255, 255, 0.9)",
                    "borderRadius": "20px",
                    "padding": "38px 32px",
                    "boxShadow": "0 24px 55px -12px rgba(30, 41, 59, 0.28), 0 4px 18px rgba(30, 41, 59, 0.10)",
                    "position": "relative",
                    "zIndex": 2,
                    "overflow": "hidden"
                },
                children=[
                    # Franja superior — acento discreto, no llamativo
                    html.Div(
                        style={
                            "position": "absolute",
                            "top": "0",
                            "left": "0",
                            "right": "0",
                            "height": "3px",
                            "background": ROJO_OSCURO
                        }
                    ),
                    # Encabezado de Marca
                    html.Div(
                        style={"textAlign": "center", "marginBottom": "28px"},
                        children=[
                            html.Img(
                                src="/assets/aurora_logo.png",
                                style={"height": "44px", "marginBottom": "14px"},
                            ),
                            html.Br(),
                            html.H3(
                                "Gasto Canales",
                                style={
                                    "fontWeight": "800",
                                    "color": "#1e293b",
                                    "marginBottom": "4px",
                                    "letterSpacing": "-0.03em",
                                    "fontSize": "1.6rem"
                                }
                            ),
                            html.P(
                                "Plataforma Ejecutiva de Control Presupuestal",
                                style={"color": "#64748b", "fontSize": "0.85rem", "marginBottom": "0"}
                            )
                        ]
                    ),

                    # Banner de Alerta de Error
                    html.Div(id="login-error-banner"),

                    # Campo Usuario
                    html.Div(
                        style={"marginBottom": "18px"},
                        children=[
                            html.Label("Usuario", style=ESTILO_ETIQUETA),
                            dcc.Input(
                                id="login-input-user",
                                type="text",
                                value="",
                                placeholder="Escribe tu usuario",
                                autoComplete="off",
                                className="form-control shadow-none login-field-input",
                                autoFocus=True,
                                n_submit=0,
                                style=ESTILO_CAMPO,
                            )
                        ]
                    ),

                    # Campo Contraseña
                    html.Div(
                        style={"marginBottom": "24px"},
                        children=[
                            html.Label("Contraseña", style=ESTILO_ETIQUETA),
                            html.Div(
                                style={"position": "relative"},
                                children=[
                                    dcc.Input(
                                        id="login-input-password",
                                        type="password",
                                        value="",
                                        placeholder="Escribe tu contraseña",
                                        autoComplete="new-password",
                                        className="form-control shadow-none login-field-input",
                                        n_submit=0,
                                        style={**ESTILO_CAMPO, "padding": "14px 60px 14px 16px"},
                                    ),
                                    html.Button(
                                        "Mostrar",
                                        id="login-toggle-pwd",
                                        n_clicks=0,
                                        type="button",
                                        style={
                                            "position": "absolute",
                                            "right": "10px",
                                            "top": "50%",
                                            "transform": "translateY(-50%)",
                                            "background": "transparent",
                                            "border": "none",
                                            "color": "#64748b",
                                            "fontSize": "0.72rem",
                                            "fontWeight": "700",
                                            "cursor": "pointer",
                                            "padding": "4px 6px"
                                        }
                                    )
                                ]
                            )
                        ]
                    ),

                    # Botón de Login
                    html.Button(
                        "Iniciar Sesión →",
                        id="login-btn-submit",
                        className="btn w-100",
                        n_clicks=0,
                        style={
                            "background": ROJO_OSCURO,
                            "borderColor": ROJO_OSCURO,
                            "fontWeight": "700",
                            "fontSize": "0.96rem",
                            "padding": "12px",
                            "borderRadius": "10px",
                            "boxShadow": "0 2px 8px rgba(139, 26, 26, 0.30)",
                            "color": "#ffffff",
                            "cursor": "pointer"
                        }
                    ),
                ]
            )
        ]
    )
