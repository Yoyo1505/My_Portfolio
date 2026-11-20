# -*- coding: utf-8 -*-
"""
dash_app/services/cashi_log_service.py
Logging ligero y no-bloqueante de las consultas que los usuarios hacen a Cashi
(el asistente de chat del dashboard). Se guarda como JSONL (un JSON por línea,
append-only) en datos/cashi_queries_log.jsonl.

Diseño:
- register_query() nunca debe romper el chat de Cashi: cualquier error de I/O
  se ignora en silencio (try/except mudo).
- read_last_queries() lee de forma eficiente solo las últimas N líneas del
  archivo (sin cargarlo completo a memoria), pensado para archivos que pueden
  crecer con el tiempo.
"""
from pathlib import Path
from datetime import datetime
import json

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
CASHI_LOG_PATH = BASE_DIR / "datos" / "cashi_queries_log.jsonl"

# Tamaño de bloque para lectura inversa eficiente (bytes)
_CHUNK_SIZE = 8192


def register_query(username, pregunta, intencion_detectada, exitosa=True):
    """
    Registra una consulta a Cashi en el log JSONL. No-bloqueante en el sentido
    de que cualquier fallo de escritura se ignora silenciosamente para no
    afectar la respuesta al usuario.
    """
    try:
        entry = {
            "usuario": str(username or "Desconocido"),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pregunta": str(pregunta or "")[:500],
            "intencion_detectada": str(intencion_detectada or ""),
            "exitosa": bool(exitosa)
        }
        CASHI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CASHI_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Silencioso a propósito: el logging jamás debe tumbar el chat de Cashi.
        pass


def read_last_queries(n=200):
    """
    Lee de forma eficiente las últimas N líneas del log JSONL (sin cargar
    el archivo completo a memoria) y las retorna como lista de dicts,
    ordenadas de más reciente a más antigua.
    """
    try:
        if not CASHI_LOG_PATH.exists():
            return []

        file_size = CASHI_LOG_PATH.stat().st_size
        if file_size == 0:
            return []

        lines = []
        with open(CASHI_LOG_PATH, "rb") as f:
            pos = file_size
            buffer = b""
            while pos > 0 and len(lines) <= n:
                read_size = min(_CHUNK_SIZE, pos)
                pos -= read_size
                f.seek(pos)
                chunk = f.read(read_size)
                buffer = chunk + buffer
                lines = buffer.split(b"\n")
            # La última "línea" puede estar vacía (archivo termina en \n)
            raw_lines = [l for l in lines if l.strip()]

        # Tomar las últimas n líneas crudas (ya están casi todas leídas)
        raw_lines = raw_lines[-n:]

        out = []
        for raw in raw_lines:
            try:
                out.append(json.loads(raw.decode("utf-8")))
            except Exception:
                continue

        # Más reciente primero
        out.reverse()
        return out
    except Exception:
        return []
