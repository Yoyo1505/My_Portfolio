# -*- coding: utf-8 -*-
"""
dash_app/services/cache_ligera.py
Memoria pequeña y acotada para callbacks que no tenían caché (medidos: 0.4 a 2 s en cada carga).

- Se limpia sola cada 15 minutos: un hilo de fondo vacía TODAS las cachés cada `DASH_CACHE_BARRIDO` segundos (900) y,
  además, ningún resultado vive más de `ttl_seg` (900 s por defecto), así que la memoria nunca se acumula.
- Tope duro: máximo `max_entradas` resultados por función (el más viejo sale primero).
- Se invalida también al reconstruir los datos (cambia aggs/_meta.json) y al reiniciar el tablero.
- Si la función falla, no se guarda nada. Se apaga con DASH_CACHE_LIGERA=0.
  Ajustes sin tocar código: DASH_CACHE_TTL (segundos, defecto 900) y DASH_CACHE_BARRIDO (segundos, defecto 900).
"""
from __future__ import annotations

import functools
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

_META = Path(__file__).resolve().parents[2] / "aggs" / "_meta.json"
_TTL_DEFECTO = float(os.environ.get("DASH_CACHE_TTL", "900"))
_BARRIDO_SEG = float(os.environ.get("DASH_CACHE_BARRIDO", "900"))

_registro: list = []          # (store, lock, ttl) de cada función con memo
_barredor = {"hilo": None}
_registro_lock = threading.Lock()


def _sello() -> float:
    try:
        return _META.stat().st_mtime
    except OSError:
        return 0.0


def _bucle_barrido() -> None:
    while True:
        time.sleep(_BARRIDO_SEG)
        try:
            limpiar_todo()          # cada 15 min se vacía todo (no solo lo vencido)
        except Exception:
            pass


def _arrancar_barredor() -> None:
    with _registro_lock:
        if _barredor["hilo"] is None:
            h = threading.Thread(target=_bucle_barrido, daemon=True, name="cache-barrido")
            h.start()
            _barredor["hilo"] = h


def memo(max_entradas: int = 24, ttl_seg: float | None = None, sello=_sello):
    ttl = float(ttl_seg) if ttl_seg is not None else _TTL_DEFECTO

    def deco(fn):
        store: OrderedDict = OrderedDict()
        lock = threading.Lock()
        _registro.append((store, lock, ttl))
        _arrancar_barredor()

        @functools.wraps(fn)
        def envoltura(*args, **kwargs):
            if os.environ.get("DASH_CACHE_LIGERA", "1") == "0":
                return fn(*args, **kwargs)
            try:
                clave = json.dumps([args, kwargs, sello()], sort_keys=True, default=str)
            except Exception:
                return fn(*args, **kwargs)
            ahora = time.time()
            with lock:
                hit = store.get(clave)
                if hit is not None and ahora - hit[0] < ttl:
                    store.move_to_end(clave)
                    return hit[1]
                if hit is not None:
                    del store[clave]
            valor = fn(*args, **kwargs)
            with lock:
                store[clave] = (ahora, valor)
                store.move_to_end(clave)
                while len(store) > max_entradas:
                    store.popitem(last=False)
            return valor

        envoltura.limpiar = lambda: store.clear()
        envoltura.tamano = lambda: len(store)
        return envoltura
    return deco


def limpiar_todo() -> None:
    """Vacía todas las cachés (por si se necesita liberar memoria a mano)."""
    for store, lock, _ttl in list(_registro):
        with lock:
            store.clear()
