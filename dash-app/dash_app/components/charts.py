# -*- coding: utf-8 -*-
"""
dash_app/components/charts.py
Estilo común de las gráficas Plotly del dash. Colores de COLOR (data_service), copiados de
Spend Legacy; fuente Public Sans; hover limpio; transiciones animadas al cambiar filtros.
"""
import plotly.graph_objects as go

from dash_app.services.data_service import COLOR

FONT = "Public Sans, -apple-system, Segoe UI, sans-serif"
TEXTO = "#1e2430"
GRID = "#eef0f4"
BORDE = "#e2e6ed"

# Misma paleta categórica que "Spend Legacy" (_PALETA_ENTIDADES)
PALETA_ENTIDADES = ["#a93226", "#1f618d", "#145a32", "#b7950b", "#6c3483",
                    "#117864", "#ba4a00", "#5d6d7e", "#873600", "#1a5276",
                    "#7b241c", "#186a3b"]

# 2026-10-19: paleta propia de las gráficas (más contraste entre series). Solo aplica
# aquí: COLOR de data_service sigue igual para PDF/PPT, que deben salir idénticos a Streamlit.
# 2026-10-26: barras con los mismos colores que el tablero Spend Legacy (COLOR de su app.py).
PALETA_GRAF = {"Real_2025": "#ccd1d9", "Real_2026": "#f1948a", "Plan_2026": "#a93226",
               "Forecast": "#8a93a0", "Forecast_Cierre": "#8a93a0"}
PALETA_DONA = ["#d6453a", "#5873bd", "#2b8a9e", "#e0a030", "#6a5acd", "#3f9b6e",
               "#c76d2b", "#5d6d7e", "#8e3b46", "#4a7fb5", "#9b8b3e", "#7a5c99"]

# Para dcc.Graph(config=...) y dcc.Graph(animate=True, animation_options=...)
CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
    "toImageButtonOptions": {"format": "png", "scale": 2},
}
# Alto fijo del contenedor: evita que la gráfica crezca al redimensionarse (bucle de autosize)
ESTILO_GRAFICA = {"height": "440px", "width": "100%"}
ANIMACION = {"frame": {"redraw": False}, "transition": {"duration": 500, "easing": "cubic-in-out"}}

NOMBRE_SERIE = {"Real_2025": "Real 2025", "Real_2026": "Real 2026",
                "Plan_2026": "Plan 2026", "Forecast": "Forecast Finanzas", "Forecast_Cierre": "Forecast Finanzas"}
SERIES_LINEA = {"Plan_2026", "Forecast", "Forecast_Cierre"}


def color_serie(m):
    return PALETA_GRAF["Forecast_Cierre"] if m.startswith("Forecast") else PALETA_GRAF.get(m, COLOR.get(m))


def estilo(fig, height=440, ytitle="Millones de Pesos (MDP)", hovermode="x unified", margin=None):
    """Aplica el estilo común a una figura. Fondo transparente para que se vea el vidrio de la tarjeta;
    leyenda arriba a la izquierda para no chocar con la barra de herramientas (zoom, reset, descarga)."""
    fig.update_layout(
        height=height,
        font=dict(family=FONT, size=12, color=TEXTO),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=margin or dict(l=60, r=16, t=46, b=100),
        hovermode=hovermode,
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=BORDE, font=dict(family=FONT, size=12, color=TEXTO)),
        legend=dict(orientation="h", yanchor="bottom", y=1.1, xanchor="left", x=0, bgcolor="rgba(0,0,0,0)",
                    font=dict(size=12)),
        transition=dict(duration=500, easing="cubic-in-out"),
    )
    fig.update_xaxes(gridcolor="rgba(127,138,155,0.18)", linecolor=BORDE, showspikes=True, spikemode="across",
                     spikethickness=1, spikecolor="#9aa3af", spikedash="dot", automargin=True)
    fig.update_yaxes(gridcolor="rgba(127,138,155,0.22)", zeroline=True, zerolinecolor=BORDE, tickformat=",.0f",
                     title=dict(text=ytitle, font=dict(size=11)), automargin=True)
    return fig


def estilo_basico(fig, height=None):
    """Para figuras con su propio diseño (ranking, histograma): solo fondo transparente, fuente, cuadrícula y
    globo de ayuda del estilo común, sin tocar leyenda, márgenes ni títulos."""
    fig.update_layout(
        font=dict(family=FONT, size=12, color=TEXTO),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=BORDE, font=dict(family=FONT, size=12, color=TEXTO)),
        **({"height": height} if height else {}),
    )
    fig.update_xaxes(gridcolor="rgba(127,138,155,0.18)", linecolor=BORDE, automargin=True)
    fig.update_yaxes(gridcolor="rgba(127,138,155,0.22)", zerolinecolor=BORDE, automargin=True)
    return fig


def barras(df, xcol, series, n=14):
    """Barras agrupadas (Real) + líneas punteadas (Plan/Forecast) en MDP, con hover y animación."""
    d = df.head(n).copy()
    fig = go.Figure()
    for m in series:
        if m not in d.columns:
            continue
        nombre = NOMBRE_SERIE.get(m, m)
        y = d[m] / 1e6
        tpl = f"<b>%{{x}}</b><br>{nombre}: <b>%{{y:,.2f}} MDP</b><extra></extra>"
        if m in SERIES_LINEA:
            # Línea punteada que sigue a todas las barras (como en el original), con marcador en cada categoría.
            c = color_serie(m)
            plan = m == "Plan_2026"
            fig.add_trace(go.Scatter(
                x=d[xcol], y=y, name=nombre, mode="lines+markers",
                line=dict(color=c, width=2.5, dash="dash" if plan else "dot"),
                marker=dict(symbol="circle" if plan else "diamond", size=7 if plan else 9, color=c,
                            line=dict(color="#ffffff", width=1.2)),
                hovertemplate=tpl,
            ))
        else:
            fig.add_trace(go.Bar(
                x=d[xcol], y=y, name=nombre, marker_color=color_serie(m), opacity=0.96,
                hovertemplate=tpl,
            ))
    fig.update_layout(barmode="group", bargap=0.32, bargroupgap=0.08)
    fig.update_traces(marker_cornerradius=4, selector=dict(type="bar"))
    fig.update_xaxes(tickangle=-35, tickfont=dict(size=11))
    return estilo(fig)


def dona(df, label_col, value_col, titulo_centro="Real 2026", height=440):
    """Participación (%) por categoría en dona, con hover y resaltado al pasar el cursor."""
    d = df[df[value_col] > 0].copy()
    fig = go.Figure(go.Pie(
        labels=d[label_col], values=d[value_col] / 1e6, hole=0.58, sort=False,
        marker=dict(colors=PALETA_DONA[:len(d)], line=dict(color="#ffffff", width=2)),
        textinfo="percent", textfont=dict(family=FONT, size=11),
        hovertemplate="<b>%{label}</b><br>%{value:,.1f} MDP<br>%{percent}<extra></extra>",
    ))
    fig.update_layout(
        height=height, font=dict(family=FONT, size=12, color=TEXTO),
        paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=10, r=10, t=10, b=10),
        legend=dict(orientation="v", x=1.02, y=0.5),
        hoverlabel=dict(bgcolor="#ffffff", bordercolor=BORDE, font=dict(family=FONT, size=12, color=TEXTO)),
        transition=dict(duration=500, easing="cubic-in-out"),
        annotations=[dict(text=f"<b>{titulo_centro}</b>", x=0.5, y=0.5, showarrow=False,
                          font=dict(size=13, color=TEXTO))],
    )
    return fig


