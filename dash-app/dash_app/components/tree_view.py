# -*- coding: utf-8 -*-
"""
dash_app/components/tree_view.py
Componente universal de Árbol Jerárquico Drill-Down Lazy-Loading de Alto Rendimiento para Plotly Dash.
- Empaquetado de metadatos URL-safe base64 para IDs consistentes y soporte nativo de MATCH (<5ms).
- Alineación milimétrica con proporción áurea y espaciado ejecutivo.
"""
import json
import base64
from dash import html
from dash_app.services.data_service import MC, DataService
from dash_app.components.top_filter_bar import NOM_MES

_ds = DataService()

def encode_node_meta(meta_dict: dict) -> str:
    """Codifica de forma compacta y segura los metadatos del nodo para el ID de Dash"""
    json_bytes = json.dumps(meta_dict, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(json_bytes).decode("ascii")

def decode_node_meta(meta_str: str) -> dict:
    """Decodifica los metadatos del nodo"""
    try:
        json_bytes = base64.urlsafe_b64decode(meta_str.encode("ascii"))
        return json.loads(json_bytes.decode("utf-8"))
    except Exception:
        return {}

def _clase_fila(base, inc_fc, inc_fcs):
    """Clases de la fila: vt-row-fc (13 col, +Forecast) y vt-row-fcs (16 col, +FCST del servidor)."""
    return base + (" vt-row-fc" if inc_fc else "") + (" vt-row-fcs" if (inc_fc and inc_fcs) else "")

def render_tree_header(inc_fc=False, inc_fcs=False):
    children = [
        html.Span("Concepto"),
        html.Span("Real 2025"),
        html.Span("% Part."),
        html.Span("Plan 2026"),
        html.Span("% Part."),
        html.Span("Real 2026", className="vt-r26-head"),
        html.Span("% Part.", className="vt-r26-head"),
        html.Span("vs Plan"),
        html.Span("vs Plan %"),
        html.Span("vs AA"),
        html.Span("vs AA %"),
    ]
    if inc_fc:
        # Mismos nombres que Spend Legacy: Excel = "Forecast Finanzas", tabla FCST del servidor = "FCST PLAN".
        children += [html.Span("Forecast Finanzas", title="Forecast Finanzas (Excel)"), html.Span("vs Forecast"), html.Span("vs Forecast %")]
        if inc_fcs:
            children += [html.Span("FCST PLAN", title="FCST PLAN (tabla FCST del servidor)"), html.Span("vs FCST"), html.Span("vs FCST %")]
    return html.Div(className=_clase_fila("vt-row vt-head", inc_fc, inc_fcs), children=children)

def render_tree_total_row(totals, inc_fc=False, inc_fcs=False):
    r25 = totals.get("Real_2025", 0.0)
    p26 = totals.get("Plan_2026", 0.0)
    r26 = totals.get("Real_2026", 0.0)
    vs_p = r26 - p26
    pct_p = (vs_p / abs(p26) * 100) if p26 else 0.0
    vs_aa = r26 - r25
    pct_aa = (vs_aa / abs(r25) * 100) if r25 else 0.0

    cls_p = "vt-var-pos" if vs_p > 0 else "vt-var-neg"
    cls_aa = "vt-var-pos" if vs_aa > 0 else "vt-var-neg"

    children = [
        html.Span("TOTAL", style={"fontWeight": "800", "paddingLeft": "8px"}),
        html.Span(MC(r25), className="vt-num-gasto"),
        html.Span("100.0%"),
        html.Span(MC(p26), className="vt-num-gasto"),
        html.Span("100.0%"),
        html.Span(MC(r26), className="vt-r26 vt-num-gasto"),
        html.Span("100.0%", className="vt-r26"),
        html.Span(MC(vs_p), className=cls_p),
        html.Span(f"{pct_p:+.1f}%", className=cls_p),
        html.Span(MC(vs_aa), className=cls_aa),
        html.Span(f"{pct_aa:+.1f}%", className=cls_aa),
    ]
    if inc_fc:
        fc = totals.get("Forecast", p26)
        vs_fc = r26 - fc
        pct_fc = (vs_fc / abs(fc) * 100) if fc else 0.0
        cls_fc = "vt-var-pos" if vs_fc > 0 else "vt-var-neg"
        children += [
            html.Span(MC(fc), className="vt-num-gasto"),
            html.Span(MC(vs_fc), className=cls_fc),
            html.Span(f"{pct_fc:+.1f}%", className=cls_fc),
        ]
        if inc_fcs:
            fcs = totals.get("FCST", 0.0)
            vs_fcs = r26 - fcs
            pct_fcs = (vs_fcs / abs(fcs) * 100) if fcs else 0.0
            cls_fcs = "vt-var-pos" if vs_fcs > 0 else "vt-var-neg"
            children += [
                html.Span(MC(fcs), className="vt-num-gasto"),
                html.Span(MC(vs_fcs), className=cls_fcs),
                html.Span(f"{pct_fcs:+.1f}%", className=cls_fcs),
            ]
    return html.Div(className=_clase_fila("vt-row vt-tot", inc_fc, inc_fcs), children=children)

def render_tree_node(row, level=0, is_leaf=False, tree_key="tree", parent_col="", target_col="", next_cols=None, node_id=None, inc_fc=False, ancestor_path=None, inc_fcs=False, prefijo_ids="tree"):
    """Renderiza un renglón individual del árbol jerárquico con data de carga lazy instantánea.

    `ancestor_path`: lista [[col, val], ...] de TODOS los filtros de los niveles
    ya recorridos por encima de este renglón (no incluye a este renglón). Se
    guarda en el meta del nodo como "path" = ancestor_path + [este renglón],
    la ruta COMPLETA necesaria para consultar los hijos de este nodo. 2026-09-17:
    antes solo se guardaba el par (parent_col, val) de este nivel — al pedir
    hijos de un nivel 3+ (ej. Zona -> Región dentro de un Territorio ya
    filtrado) la consulta perdía todos los filtros de los ancestros y agrupaba
    TODAS las filas con ese valor en cualquier rama del árbol (grave con
    valores no únicos como "0"/blanco: mezclaba territorios distintos bajo la
    misma etiqueta "0" y el monto se disparaba por encima del total del padre
    — reportado por el usuario como "los 0 están mal clasificados... el monto
    se dispara")."""
    if parent_col == "MES":
        # iterrows() sube la fila a float ("202601.0"): se normaliza a AAAAMM para la
        # etiqueta y para el valor de drill-down de los hijos.
        row = row.astype(object)
        row["Concepto"] = str(int(float(row["Concepto"])))
    own_leg = [parent_col, str(row["Concepto"])]
    full_path = (list(ancestor_path) if ancestor_path else []) + [own_leg]
    if not node_id:
        meta = {
            "val": str(row["Concepto"]),
            "lvl": level,
            "pcol": parent_col or "",
            "tcol": target_col or "",
            "cols": next_cols or [],
            "fc": bool(inc_fc),
            "fcs": bool(inc_fcs),
            "path": full_path,
        }
        node_id = encode_node_meta(meta)

    pad = level * 20 + 8
    vs_p = row.get("Vs_Plan_Abs", row["Real_2026"] - row["Plan_2026"])
    pct_p = row.get("Vs_Plan_Pct", (vs_p / abs(row["Plan_2026"]) * 100) if row["Plan_2026"] else 0.0)
    vs_aa = row.get("Vs_AA_Abs", row["Real_2026"] - row["Real_2025"])
    pct_aa = row.get("Vs_AA_Pct", (vs_aa / abs(row["Real_2025"]) * 100) if row["Real_2025"] else 0.0)

    cls_p = "vt-var-pos" if vs_p > 0 else "vt-var-neg"
    cls_aa = "vt-var-pos" if vs_aa > 0 else "vt-var-neg"

    btn = None
    if not is_leaf:
        btn = html.Button(
            "+",
            id={"type": f"{prefijo_ids}-toggle-btn", "tree": tree_key, "node": node_id},
            className="vt-tree-btn",
            n_clicks=0
        )

    raw_concept = str(row["Concepto"])
    _es_id_numerico = raw_concept.replace(".", "", 1).replace("-", "", 1).isdigit()
    if parent_col == "MES" and len(raw_concept) == 6 and raw_concept.isdigit():
        # AAAAMM -> nombre del mes; el valor interno (para drill-down) no cambia
        concept_text = NOM_MES.get(int(raw_concept[-2:]), raw_concept)
    elif _es_id_numerico:
        clean_id = raw_concept.split(".")[0].strip()
        nombre = None
        if parent_col == "ECO_PDV" or "pdv" in tree_key:
            nombre = _ds.pdv_nombres.get(clean_id)
        elif parent_col == "ID_CENTRO_COSTOS" or "ceco" in tree_key:
            nombre = _ds.ceco_nombres.get(clean_id)
        if not nombre:
            nombre = _ds.pdv_nombres.get(clean_id) or _ds.ceco_nombres.get(clean_id)
        concept_text = f"{clean_id} - {nombre}" if nombre else clean_id
    else:
        concept_text = raw_concept

    row_cls = _clase_fila(f"vt-row vt-nvl{level}", inc_fc, inc_fcs)
    row_content = html.Div(
        className=row_cls,
        children=[
            html.Span(
                children=[
                    btn if btn else html.Span(style={"display": "inline-block", "width": "24px"}),
                    html.Span(
                        concept_text,
                        style={
                            "fontWeight": "750" if level == 0 else "500",
                            "overflow": "hidden",
                            "textOverflow": "ellipsis",
                            "whiteSpace": "nowrap"
                        },
                        title=concept_text
                    )
                ],
                style={"paddingLeft": f"{pad}px", "display": "flex", "alignItems": "center", "overflow": "hidden"}
            ),
            html.Span(MC(row["Real_2025"]), className="vt-num-gasto"),
            html.Span(f"{row.get('Pct_r25', 0):.1f}%"),
            html.Span(MC(row["Plan_2026"]), className="vt-num-gasto"),
            html.Span(f"{row.get('Pct_p26', 0):.1f}%"),
            html.Span(MC(row["Real_2026"]), className="vt-r26 vt-num-gasto"),
            html.Span(f"{row.get('Pct_r26', 0):.1f}%", className="vt-r26"),
            html.Span(MC(vs_p), className=cls_p),
            html.Span(f"{pct_p:+.1f}%", className=cls_p),
            html.Span(MC(vs_aa), className=cls_aa),
            html.Span(f"{pct_aa:+.1f}%", className=cls_aa),
        ]
    )
    if inc_fc:
        fc = row.get("Forecast", row["Plan_2026"])
        vs_fc = row.get("Vs_Forecast", row["Real_2026"] - fc)
        pct_fc = row.get("Vs_Forecast_Pct", (vs_fc / abs(fc) * 100) if fc else 0.0)
        cls_fc = "vt-var-pos" if vs_fc > 0 else "vt-var-neg"
        row_content.children += [
            html.Span(MC(fc), className="vt-num-gasto"),
            html.Span(MC(vs_fc), className=cls_fc),
            html.Span(f"{pct_fc:+.1f}%", className=cls_fc),
        ]
        if inc_fcs:
            fcs = row.get("FCST", 0.0)
            vs_fcs = row.get("Vs_FCST", row["Real_2026"] - fcs)
            pct_fcs = row.get("Vs_FCST_Pct", (vs_fcs / abs(fcs) * 100) if fcs else 0.0)
            cls_fcs = "vt-var-pos" if vs_fcs > 0 else "vt-var-neg"
            row_content.children += [
                html.Span(MC(fcs), className="vt-num-gasto"),
                html.Span(MC(vs_fcs), className=cls_fcs),
                html.Span(f"{pct_fcs:+.1f}%", className=cls_fcs),
            ]

    children_container = html.Div(
        id={"type": f"{prefijo_ids}-children", "tree": tree_key, "node": node_id},
        style={"display": "none"},
        children=[]
    )

    return html.Div([row_content, children_container], id=f"wrap-{tree_key}-{node_id[:20]}")

def create_tree_view(tree_key, root_df, total_dict=None, source_info=None, cols=None, inc_fc=False):
    """Contenedor completo del árbol con header, nodos raíz y totales"""
    nodes = []
    inc_fcs = bool(inc_fc and "FCST" in root_df.columns)   # columna FCST del servidor (solo si el df la trae)
    pcol = cols[0] if cols and len(cols) > 0 else ""
    tcol = cols[1] if cols and len(cols) > 1 else ""
    is_leaf = (cols is not None and len(cols) <= 1)
    next_cols = cols[1:] if cols and len(cols) > 1 else []

    # Limitar a primeros 50 nodos para rendimiento
    max_initial_nodes = 50
    df_display = root_df.head(max_initial_nodes)
    has_more = len(root_df) > max_initial_nodes

    for _, r in df_display.iterrows():
        nodes.append(render_tree_node(
            r,
            level=0,
            is_leaf=is_leaf,
            tree_key=tree_key,
            parent_col=pcol,
            target_col=tcol,
            next_cols=next_cols,
            inc_fc=inc_fc,
            inc_fcs=inc_fcs
        ))

    # Agregar "Cargar más" si hay más nodos
    if has_more:
        nodes.append(
            html.Div(
                className="vt-load-more-wrap",
                children=[
                    html.Button(
                        f"Cargar {len(root_df) - max_initial_nodes} más...",
                        className="vt-load-more-btn",
                        style={"cursor": "pointer"}
                    )
                ]
            )
        )

    totals = total_dict or {
        "Real_2025": root_df["Real_2025"].sum() if "Real_2025" in root_df else 0.0,
        "Plan_2026": root_df["Plan_2026"].sum() if "Plan_2026" in root_df else 0.0,
        "Real_2026": root_df["Real_2026"].sum() if "Real_2026" in root_df else 0.0
    }
    if inc_fc and "Forecast" not in totals:
        totals["Forecast"] = root_df["Forecast"].sum() if "Forecast" in root_df else totals["Plan_2026"]
    if inc_fcs and "FCST" not in totals:
        totals["FCST"] = root_df["FCST"].sum()

    return html.Div(
        # El árbol de Resumen (pocos grupos) crece completo, sin scroll interno.
        className="vt-tree-wrap vt-tree-wrap--libre" if tree_key == "resumen-gc" else "vt-tree-wrap",
        children=[
            html.Div(
                className="vt-tree vt-tree-fc" if inc_fc else "vt-tree",
                children=[
                    render_tree_header(inc_fc=inc_fc, inc_fcs=inc_fcs),
                    html.Div(nodes),
                    render_tree_total_row(totals, inc_fc=inc_fc, inc_fcs=inc_fcs)
                ]
            )
        ]
    )


def _agregar_por_col(df, col):
    """Agrupa un DataFrame crudo por una columna, sumando Real_2025/Plan_2026/Real_2026"""
    g = df.groupby(col, as_index=False).agg({
        "Real_2025": "sum", "Plan_2026": "sum", "Real_2026": "sum"
    })
    g = g.sort_values("Real_2026", ascending=False).reset_index(drop=True)
    if col in ("cat_Grupo_de_Cuentas", "cat_Cuentas"):
        # 2026-09-24 (paridad con el Streamlit): Grupos por orden de presentación,
        # Cuentas alfabéticas dentro de su Grupo, no por |Real|.
        g = DataService().ordenar_hijos(g.rename(columns={col: "Concepto"}), col).rename(columns={"Concepto": col})
    return g


def _render_rama_expandida(df_nivel, cols, level, tree_key, max_ramas=200, ancestor_path=None, abierto=True):
    """Renderiza recursivamente un nivel del árbol YA EXPANDIDO (sin esperar clic),
    usado cuando hay una búsqueda activa: el usuario espera ver de una vez el camino
    completo hasta los nodos que buscó, no un árbol colapsado nivel por nivel."""
    col = cols[0]
    is_leaf = len(cols) <= 1
    agg = _agregar_por_col(df_nivel, col).head(max_ramas)

    nodes = []
    for _, r in agg.iterrows():
        row_for_render = r.copy()
        row_for_render["Concepto"] = r[col]
        pct_base = agg["Real_2026"].sum() or 1.0
        row_for_render["Pct_r25"] = (r["Real_2025"] / (agg["Real_2025"].sum() or 1.0)) * 100
        row_for_render["Pct_p26"] = (r["Plan_2026"] / (agg["Plan_2026"].sum() or 1.0)) * 100
        row_for_render["Pct_r26"] = (r["Real_2026"] / pct_base) * 100

        node_html = render_tree_node(
            row_for_render, level=level, is_leaf=is_leaf, tree_key=tree_key,
            parent_col=col, target_col=(cols[1] if len(cols) > 1 else ""),
            next_cols=cols[1:] if len(cols) > 1 else [],
            ancestor_path=ancestor_path
        )

        if not is_leaf:
            df_hijo = df_nivel[df_nivel[col] == r[col]]
            path_hijo = (list(ancestor_path) if ancestor_path else []) + [[col, str(r[col])]]
            hijos = _render_rama_expandida(df_hijo, cols[1:], level + 1, tree_key, max_ramas, ancestor_path=path_hijo, abierto=abierto)
            # node_html = [row_content, children_container_vacio] -> se reemplaza
            # el contenedor de hijos por uno ya visible con los hijos calculados.
            row_content = node_html.children[0]
            children_container = node_html.children[1]
            children_container.children = hijos
            # con abierto=False los hijos quedan precargados pero ocultos: el primer clic solo los muestra
            if abierto:
                children_container.style = {"display": "block"}
                # el botón "+" debe reflejar estado abierto ("-")
                try:
                    btn_span = row_content.children[0].children[0]
                    if hasattr(btn_span, "children") and btn_span.children == "+":
                        btn_span.children = "-"
                except Exception:
                    pass
            nodes.append(node_html)
        else:
            nodes.append(node_html)

    return nodes


def create_tree_view_expanded(tree_key, raw_df, cols, total_dict=None, abierto=True):
    """Árbol con TODAS las ramas relevantes ya expandidas de una vez — para usar
    cuando hay una búsqueda/selección activa de PDV o CECO: el usuario espera ver
    directo el camino completo hasta lo que buscó, no tener que ir abriendo nivel
    por nivel un árbol colapsado."""
    nodes = _render_rama_expandida(raw_df, cols, 0, tree_key, abierto=abierto)

    totals = total_dict or {
        "Real_2025": raw_df["Real_2025"].sum() if "Real_2025" in raw_df else 0.0,
        "Plan_2026": raw_df["Plan_2026"].sum() if "Plan_2026" in raw_df else 0.0,
        "Real_2026": raw_df["Real_2026"].sum() if "Real_2026" in raw_df else 0.0
    }

    return html.Div(
        className="vt-tree-wrap",
        children=[
            html.Div(
                className="vt-tree",
                children=[
                    render_tree_header(),
                    html.Div(nodes),
                    render_tree_total_row(totals)
                ]
            )
        ]
    )


