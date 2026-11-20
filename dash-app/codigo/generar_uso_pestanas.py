# -*- coding: utf-8 -*-
"""Genera datos/uso_pestanas.json con el uso real de las pestañas (bitácora y tiempos del Streamlit) para que el
Dash sepa en qué orden precargar al iniciar sesión.

Uso:  python codigo/generar_uso_pestanas.py [ruta\\bitacora.json]
Por defecto lee E:\\Desarrollos\\Spend Legacy\\datos\\bitacora.json (eventos NAVEGACION y 'rendimiento').
Se puede volver a correr cuando se quiera: el orden se recalcula con el uso más reciente.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
FUENTE = Path(r"<LEGACY_PROJECT>\datos\bitacora.json")
SALIDA = RAIZ / "datos" / "uso_pestanas.json"

# Nombre de la pestaña en el Streamlit -> id de la pestaña en el Dash (None = no se precarga)
EQUIV = {
    "Resumen": "tab-resumen", "Tabla Dinámica": "tab-tabla-dinamica", "Detalle Cuenta": "tab-detalle-cuenta",
    "Detalle CECO": "tab-detalle-ceco", "Detalle PDV": "tab-detalle-pdv", "Temporalidad": "tab-temporalidad",
    "Contraloria": "tab-contraloria", "Comparación de Versiones": "tab-comparacion", "Cierres": "tab-cierres",
    "Cashi AI Assistant": None, "Gestión de Usuarios": None, "Canales Digitales": None,
}
# Pestañas que solo existen en el Dash: peso bajo y fijo hasta que haya uso propio.
SOLO_DASH = {"tab-sobregasto": 3.0, "tab-plan-conta": 2.0}


def main(ruta: Path) -> int:
    b = json.loads(ruta.read_text(encoding="utf-8-sig"))
    ev = pd.DataFrame(b["eventos"])
    nav = ev[ev["tipo"] == "NAVEGACION"]
    rend = pd.DataFrame(b.get("rendimiento", []))
    pest = {}
    total = len(nav)
    for nombre, g in nav.groupby("pestaña"):
        r = rend[rend["pestaña"] == "pestaña_" + nombre.lower().replace(" ", "_")] if len(rend) else pd.DataFrame()
        pest[nombre] = {
            "tab": EQUIV.get(nombre), "visitas": int(len(g)), "pct": round(len(g) / total * 100, 1), "usuarios": int(g["usuario"].nunique()),
            "mediana_ms_streamlit": round(float(r["tiempo_ms"].median()), 0) if len(r) else None,
            "p90_ms_streamlit": round(float(r["tiempo_ms"].quantile(.9)), 0) if len(r) else None,
        }
    pesos = {v["tab"]: v["pct"] for v in pest.values() if v["tab"]}
    # Uso propio del Dash (datos/uso_dash.jsonl, lo escribe uso_service): cuando hay suficientes visitas se mezcla 50/50
    # con el del Streamlit; con más uso del Dash, su peso real sustituye a los valores fijos de SOLO_DASH.
    dash_vis = {}
    uso_dash = RAIZ / "datos" / "uso_dash.jsonl"
    if uso_dash.exists():
        for ln in uso_dash.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if e.get("tipo") == "NAVEGACION" and e.get("pestana"):
                dash_vis[e["pestana"]] = dash_vis.get(e["pestana"], 0) + 1
    tot_dash = sum(dash_vis.values())
    if tot_dash >= 100:
        for t in set(pesos) | set(dash_vis):
            pesos[t] = round(0.5 * pesos.get(t, 0.0) + 0.5 * dash_vis.get(t, 0) / tot_dash * 100, 1)
        pest["_dash"] = {"visitas": tot_dash, "por_pestana": dash_vis}
    else:
        pesos.update({t: p for t, p in SOLO_DASH.items() if t not in pesos})
    orden = [t for t, _ in sorted(pesos.items(), key=lambda kv: -kv[1])]
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps({
        "fuente": str(ruta), "generado": dt.datetime.now().isoformat(timespec="seconds"),
        "periodo": [str(nav["fecha"].min()), str(nav["fecha"].max())], "visitas_total": int(total),
        "primera_pestana_tras_login": "tab-resumen", "orden_precarga": orden, "pestanas": pest,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Escrito {SALIDA}\nOrden de precarga: {orden}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else FUENTE))
