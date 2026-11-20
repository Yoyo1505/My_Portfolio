# -*- coding: utf-8 -*-
"""
Prueba de consistencia de los árboles del tablero, sin levantarlo (solo lectura de dashboard.duckdb):

    python codigo\\pruebas_arbol.py          (código de salida 1 si hay descuadres)

1. En cada rama, el padre = suma de sus hijos, con los MISMOS filtros que arma la pantalla, en todos los niveles
   (se recorren los 3 nodos mayores de cada nivel).
2. Cruce entre pestañas: con los mismos filtros, el total de Resumen, Detalle CECO, Contraloría, Cierres y
   Detalle Cuenta debe ser igual (PDV excluye ECO_PDV=0 por diseño, por eso no entra al cruce).

Árboles: Resumen, Detalle CECO (General y Detalle), Detalle PDV (General y Por Cuenta), Contraloría (2 direcciones),
Cierres, Detalle Cuenta (Agrupadores Reales). Los filtros de búsqueda de cada pantalla se reconstruyen igual que sus
callbacks (sync_*_search_to_tree) y la composición de filtros es la de app._filtros_efectivos_arbol.
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.chdir(BASE)

from dash_app.services.data_service import DataService  # noqa: E402

ds = DataService()
ddb = ds.ddb
MED = ("Real_2025", "Plan_2026", "Real_2026")
TOL = 0.05e6        # 0.05 MDP
TOP = 3             # nodos por nivel que se expanden
fallas = []
n_ramas = 0


def base(**kw):
    f = {"canal": "Fisico", "tipo_monto": "Monto Constante", "si": 1, "sf": ds.smr,
         "fg_resp": [], "fg_gpo": [], "fg_cta": [], "fg_cta_ids": [], "fg_subtipo": []}
    f.update(kw)
    return f


def efectivos(g, search, ruta):
    """Igual que app._filtros_efectivos_arbol: globales + búsqueda del árbol + ancestros (ruta sin el propio nodo)."""
    eff = dict(g)
    eff.update({k: v for k, v in (search or {}).items() if k != "tree_key"})
    for col, val in ruta[:-1]:
        if col:
            eff[col] = [val]
    return eff


def locales(g):
    lf = {"canal": g.get("canal")}
    for k, c in (("fg_gpo", "cat_Grupo_de_Cuentas"), ("fg_cta", "cat_Cuentas"), ("fg_subtipo", "cat_Subtipo")):
        if g.get(k):
            lf[c] = g[k]
    return lf


def recorrer(caso, arbol, tree_key, cols, raiz, g, search, tipo="agg"):
    """Expande los TOP nodos mayores de cada nivel y compara padre vs suma de hijos."""
    global n_ramas
    raiz = raiz.dropna(subset=[cols[0]])
    pg = raiz.groupby(cols[0])[list(MED)].sum()
    pg = pg.reindex(pg["Real_2026"].abs().sort_values(ascending=False).index)

    def nivel(i, nodos, ruta):
        global n_ramas
        if i + 1 >= len(cols):
            return
        for nombre, r in nodos.head(TOP).iterrows():
            ruta_n = ruta + [[cols[i], str(nombre)]]
            h = ds.query_tree_children_fast(tree_key, cols[i], str(nombre), cols[i + 1], si=g["si"], sf=g["sf"],
                                            tipo_monto=g["tipo_monto"], filters=efectivos(g, search, ruta_n))
            n_ramas += 1
            if h is None or h.empty:
                if max(abs(r[k]) for k in MED) > TOL:
                    fallas.append(f"[{arbol}|{caso}|nivel {i}] {nombre}: padre con monto y SIN hijos")
                continue
            if any(abs(r[k] - h[k].sum()) > TOL for k in MED):
                fallas.append(f"[{arbol}|{caso}|nivel {i}] {nombre}: padre {[round(float(r[k]) / 1e6, 1) for k in MED]} "
                              f"hijos {[round(float(h[k].sum()) / 1e6, 1) for k in MED]}")
                continue
            hh = h.rename(columns={"Concepto": "Concepto"}).set_index("Concepto")[list(MED)]
            hh = hh.reindex(hh["Real_2026"].abs().sort_values(ascending=False).index)
            nivel(i + 1, hh, ruta_n)

    nivel(0, pg, [])


divs = ddb.get_unique_values("cat_Direccion_Division")
casos = {
    "sin filtro": base(),
    "sem 1-10": base(sf=10),
    "año completo 1-53": base(sf=53),     # pasa del corte: Real 2026 = Real hasta el corte + Plan después
    "Ambos": base(canal="Ambos"),
    "grupo Publicidad": base(fg_gpo=["Gastos de Publicidad"]),
}

# 1) Resumen (Grupo -> Cuenta)
for n, g in casos.items():
    p = ds.compute_by_dimension("grupo", g).rename(columns={"Concepto": "cat_Grupo_de_Cuentas"})
    recorrer(n, "resumen", "resumen-gc", ["cat_Grupo_de_Cuentas", "cat_Cuentas"], p, g, None)

# 2) Detalle CECO (con y sin búsqueda de división)
for vista, cols in (("General", ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                                 "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ID_CENTRO_COSTOS"]),
                    ("Detalle", ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "ID_CENTRO_COSTOS"])):
    for n, g in casos.items():
        for et, search in (("", {}), (" + división", {"tree_key": "ceco-tree", "cat_Direccion_Division": [divs[0]]})):
            lf = {**locales(g), **{k: v for k, v in search.items() if k != "tree_key"}}
            p = ddb.query_ceco_summary(cols, si=g["si"], sf=g["sf"], filters=lf, tipo_monto=g["tipo_monto"])
            recorrer(n + et, f"ceco-{vista}", "ceco-tree", cols, p, g, search)

# 3) Detalle PDV: la raíz siempre excluye ECO_PDV=0, y el store del árbol debe llevarlo siempre
search_pdv = {"tree_key": "pdv-tree", "eco_pdv_exclude_zero": True}     # lo que devuelve sync_pdv_search_to_tree sin búsqueda
for vista, cols in (("General", ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                                 "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ECO_PDV"]),
                    ("Por Cuenta", ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "ECO_PDV"])):
    for n, g in casos.items():
        p = ddb.query_ceco_summary(cols, si=g["si"], sf=g["sf"], filters=locales(g), tipo_monto=g["tipo_monto"],
                                   exclude_zero_cols=["ECO_PDV"])
        recorrer(n, f"pdv-{vista}", "pdv-tree", cols, p, g, search_pdv)

# 4) Contraloría (2 direcciones), Cierres y Detalle Cuenta (aggs)
if ddb.tiene_agg("contraloria"):
    for cols in (["cat_Contraloria_6", "cat_Contraloria", "cat_Grupo_de_Cuentas", "cat_Cuentas"],
                 ["cat_Cuentas", "cat_Grupo_de_Cuentas", "cat_Contraloria", "cat_Contraloria_6"]):
        for n, g in casos.items():
            recorrer(n, "contraloria", "ctrl-tree", cols, ds.agg("contraloria", [cols[0]], g, g["tipo_monto"]).rename(
                columns={}), g, None)
if ddb.tiene_agg("cierres_arbol"):
    cols = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "cat_PDC"]
    for n, g in casos.items():
        recorrer(n, "cierres", "cierres-tree", cols, ds.agg("cierres_arbol", [cols[0]], g, g["tipo_monto"]), g, None)
if ddb.tiene_agg("agrupadores_reales"):
    cols = ["cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3", "cat_Agrupador_Reales"]
    for n, g in casos.items():
        recorrer(n, "detalle-cuenta", "agrup-real-tree", cols,
                 ds.agg("agrupadores_reales", [cols[0]], g, g["tipo_monto"]), g, None)

# 5) Cruce entre pestañas: mismo filtro -> mismo total (Real 2025, Plan 2026, Real 2026)
for n, g in casos.items():
    tot = {}
    k = ds.compute_summary_kpis(g)
    tot["resumen"] = (k["Real_2025"], k["Plan_2026"], k["Real_2026"])
    d = ddb.query_ceco_summary(["cat_Agrupa1"], si=g["si"], sf=g["sf"], filters=locales(g), tipo_monto=g["tipo_monto"])
    tot["ceco"] = tuple(float(d[m].sum()) for m in MED)
    for nombre, agg in (("contraloria", "contraloria"), ("cierres", "cierres_arbol"), ("detalle-cuenta", "agrupadores_reales")):
        if ddb.tiene_agg(agg):
            a = ds.agg(agg, [], g, g["tipo_monto"])
            tot[nombre] = tuple(float(a[m].iloc[0]) for m in MED)
    ref = tot["resumen"]
    for nombre, t in tot.items():
        if nombre == "resumen":
            continue
        # CECO/consolidado lee Serie R26 crudo; Resumen usa Real hasta la semana de corte (iguales mientras sf <= corte)
        if any(abs(a - b) > TOL for a, b in zip(ref, t)):
            fallas.append(f"[cruce|{n}] resumen {[round(x / 1e6, 1) for x in ref]} vs {nombre} {[round(x / 1e6, 1) for x in t]}")

if fallas:
    print(f"FALLAS ({len(fallas)}):")
    print("\n".join(fallas))
    sys.exit(1)
print(f"OK: {n_ramas} ramas padre=suma de hijos y cruce de totales entre pestañas (semana de corte {ds.smr}).")
