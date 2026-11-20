# -*- coding: utf-8 -*-
"""
dash_app/services/uso_service.py
Registro de uso del Dash en datos/uso_dash.jsonl (una línea JSON por evento, solo se agrega al final):
  LOGIN / LOGOUT, NAVEGACION (pestaña abierta + ms en armarla), CASHI_CONSULTA (pregunta, ruta, ms, éxito),
  DESCARGA (archivo, filas) y LENTA (cualquier petición del tablero que tarde más de 1 s: qué callback y cuánto).
Nunca estorba: cualquier error de escritura se ignora. Se apaga con DASH_REGISTRO_USO=0.
Lo lee codigo/generar_uso_pestanas.py para ordenar la precarga y sirve para ver qué usa cada quien.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

RUTA = Path(__file__).resolve().parents[2] / "datos" / "uso_dash.jsonl"
_lock = threading.Lock()


def registrar(tipo: str, usuario=None, pestana: str | None = None, ms: float | None = None, detalle=None, **extra) -> None:
    if os.environ.get("DASH_REGISTRO_USO", "1") == "0":
        return
    try:
        ev = {"fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "tipo": tipo, "usuario": str(usuario or "Desconocido")}
        if pestana:
            ev["pestana"] = pestana
        if ms is not None:
            ev["ms"] = round(float(ms), 1)
        if detalle:
            ev["detalle"] = str(detalle)[:300]
        ev.update({k: v for k, v in extra.items() if v is not None})
        linea = json.dumps(ev, ensure_ascii=False) + "\n"
        with _lock:
            RUTA.parent.mkdir(parents=True, exist_ok=True)
            with open(RUTA, "a", encoding="utf-8") as f:
                f.write(linea)
    except Exception:
        pass


def ultimos(tipo: str, n: int = 200) -> list[dict]:
    """Últimos `n` eventos de un tipo (más recientes primero); lee solo el final del archivo."""
    try:
        with open(RUTA, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 4_000_000))
            lineas = f.read().decode("utf-8", errors="ignore").splitlines()
        out = []
        for ln in reversed(lineas):
            try:
                ev = json.loads(ln)
            except Exception:
                continue
            if ev.get("tipo") == tipo:
                out.append(ev)
                if len(out) >= n:
                    break
        return out
    except Exception:
        return []


def usuario_actual(por_defecto: str = "Desconocido") -> str:
    """Usuario de la petición en curso (cookie `vt_usuario`, que escribe app.py al iniciar sesión). Sirve para atribuir
    descargas y otros eventos cuando el callback no recibe la sesión. Fuera de una petición devuelve `por_defecto`."""
    try:
        from urllib.parse import unquote
        from flask import has_request_context, request
        if has_request_context():
            u = unquote(request.cookies.get("vt_usuario", "") or "").strip()
            if u:
                return u
    except Exception:
        pass
    return por_defecto


def usuario_de(sesion) -> str:
    """Nombre de usuario a partir del dato de `session-user-store` (None si no hay sesión)."""
    try:
        return (sesion or {}).get("username") or "Desconocido"
    except Exception:
        return "Desconocido"
