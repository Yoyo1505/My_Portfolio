# -*- coding: utf-8 -*-
"""
dash_app/services/precarga.py
Precarga de pestañas al iniciar sesión: en un hilo de fondo ejecuta, con los filtros por defecto, los callbacks de
cada pestaña (en el orden de uso real, datos/uso_pestanas.json) para que sus consultas queden en la caché del
servidor y la primera visita no espere. Nunca bloquea el login ni toca datos: solo lee.

- Una sola vez por proceso (el primer usuario que entra la dispara; los demás ya la encuentran caliente).
- Apagable sin tocar código:  DASH_PRECARGA=0
- Deja un registro de tiempos por callback en archivos_proyecto/precarga.log (sirve de "log" de rendimiento).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
USO = RAIZ / "datos" / "uso_pestanas.json"
LOG = RAIZ / "archivos_proyecto" / "precarga.log"

# Qué callbacks pertenecen a cada pestaña (por prefijo del id de sus salidas).
PREFIJOS = {
    "tab-resumen": ("resumen-",), "tab-detalle-cuenta": ("detalle-cuenta-", "cuenta-"),
    "tab-detalle-pdv": ("detalle-pdv-", "pdv-"), "tab-detalle-ceco": ("detalle-ceco-", "ceco-"),
    "tab-contraloria": ("contraloria-",), "tab-cierres": ("cierres-",), "tab-temporalidad": ("temp-", "temporalidad-"),
    "tab-comparacion": ("cmp-",), "tab-tabla-dinamica": ("td-", "tabla-dinamica-"),
    "tab-sobregasto": ("sg-", "mon-"), "tab-plan-conta": ("ppto-",),
}
ORDEN_DEFECTO = ["tab-resumen", "tab-tabla-dinamica", "tab-detalle-cuenta", "tab-detalle-ceco", "tab-detalle-pdv",
                 "tab-temporalidad", "tab-sobregasto", "tab-contraloria", "tab-comparacion", "tab-plan-conta", "tab-cierres"]
# Nada con efectos (descargas, sesión, administración, chat) y nada que dependa de un clic.
EXCLUIR = ("download", "login", "admin", "usuario", "feedback", "pwd", "password", "session", "logout", "alert", "modal", "cashi")

_lock = threading.Lock()
_estado = {"corriendo": False, "hecho": False, "inicio": None, "seg": None}


def _log(msg: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _orden() -> list:
    try:
        o = json.loads(USO.read_text(encoding="utf-8"))["orden_precarga"]
        return [t for t in o if t in PREFIJOS] + [t for t in ORDEN_DEFECTO if t not in o]
    except Exception:
        return list(ORDEN_DEFECTO)


def _defaults(layouts: dict) -> dict:
    """(id, propiedad) -> valor inicial de cada componente, leído de los layouts reales."""
    d = {}

    def walk(c):
        if c is None or isinstance(c, (str, int, float)):
            return
        if isinstance(c, (list, tuple)):
            for x in c:
                walk(x)
            return
        cid = getattr(c, "id", None)
        if cid is not None and not isinstance(cid, dict) and getattr(c, "value", None) is not None:
            d.setdefault((cid, "value"), c.value)
        walk(getattr(c, "children", None))

    for fn in layouts.values():
        try:
            walk(fn())
        except Exception:
            pass
    return d


def _correr(app, layouts: dict, filtros: dict, usuario: str) -> None:
    import dash._callback as dc
    t0 = time.perf_counter()
    _log(f"precarga iniciada (disparada por {usuario or '?'})")
    try:
        defaults = _defaults(layouts)
        mapa = {k: e for k, e in dc.GLOBAL_CALLBACK_MAP.items() if "callback" in e}
        for tab in _orden():
            tt = time.perf_counter()
            n = 0
            for key, e in mapa.items():
                salidas = e["output"] if isinstance(e["output"], list) else [e["output"]]
                ids = [str(o.component_id) for o in salidas]
                if not any(i.startswith(p) for i in ids for p in PREFIJOS[tab]):
                    continue
                if any(x in key.lower() for x in EXCLUIR) or any(i["property"] == "n_clicks" for i in e["inputs"]):
                    continue
                args = []
                for i in e["inputs"] + e["state"]:
                    cid = i["id"]
                    args.append(dict(filtros) if cid == "global-filter-store" else (None if isinstance(cid, dict) else defaults.get((cid, i["property"]))))
                ol = [{"id": o.component_id, "property": o.component_property} for o in salidas]
                c0 = time.perf_counter()
                try:
                    with app.server.test_request_context("/"):
                        e["callback"](*args, outputs_list=ol if len(ol) > 1 else ol[0], inputs_list=e["inputs"], states_list=e["state"])
                    n += 1
                    dt_c = time.perf_counter() - c0
                    if dt_c > 1.0:
                        _log(f"   {tab}: {ids[0]} tardó {dt_c:.1f} s")
                except Exception as ex:      # un callback que falle no detiene la precarga
                    _log(f"   {tab}: {ids[0]} falló ({type(ex).__name__}: {str(ex)[:80]})")
                time.sleep(0.02)            # cede el turno a las peticiones de los usuarios
            _log(f"{tab}: {n} callbacks en {time.perf_counter() - tt:.1f} s")
    except Exception:
        _log("precarga abortada: " + traceback.format_exc()[-400:])
    finally:
        with _lock:
            _estado.update(corriendo=False, hecho=True, seg=round(time.perf_counter() - t0, 1))
        _log(f"precarga terminada en {_estado['seg']} s")


def iniciar(app, layouts: dict, filtros: dict, usuario: str = "") -> bool:
    """Dispara la precarga si no se hizo ya en este proceso. Devuelve True si arrancó. Nunca lanza excepciones."""
    try:
        if os.environ.get("DASH_PRECARGA", "1") == "0":
            return False
        with _lock:
            if _estado["corriendo"] or _estado["hecho"]:
                return False
            _estado.update(corriendo=True, inicio=time.time())
        threading.Thread(target=_correr, args=(app, layouts, filtros, usuario), daemon=True, name="precarga-pestanas").start()
        return True
    except Exception:
        return False
