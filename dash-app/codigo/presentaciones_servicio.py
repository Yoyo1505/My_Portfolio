# -*- coding: utf-8 -*-
"""Generación y empaque de las 3 presentaciones semanales. Archivo único que usan los DOS tableros
(Streamlit de Spend Legacy y el Dash de DASH APP): ambos llaman a `generar_zip()`.

    from presentaciones_servicio import generar_zip
    nombre, datos = generar_zip()          # corre generar_presentaciones.py y devuelve el ZIP
"""
import io
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
SALIDA = BASE.parent / "salidas" / "presentacion"
_PATRONES = ("Gasto_semana_{s}.pptx", "Gasto_digitales_semana_{s}.pptx", "Gasto_consolidado_semana_{s}.pptx")
_lock = threading.Lock()   # una sola generación a la vez: las 3 presentaciones usan los mismos archivos


def _semana_mas_reciente(desde: float):
    """Semana de los .pptx de físicos escritos a partir de `desde` (mtime)."""
    cand = []
    for p in SALIDA.glob("Gasto_semana_*.pptx"):
        m = re.search(r"Gasto_semana_(.+)\.pptx$", p.name)
        if m and p.stat().st_mtime >= desde:
            cand.append((p.stat().st_mtime, m.group(1)))
    return max(cand)[1] if cand else None


def zip_existente(semana):
    """(nombre, bytes) con las 3 presentaciones ya generadas de `semana`, o None si falta alguna."""
    archivos = [SALIDA / pat.format(s=semana) for pat in _PATRONES]
    if not all(a.exists() for a in archivos):
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in archivos:
            z.write(a, a.name)
    return f"Presentaciones_semana_{semana}.zip", buf.getvalue()


def copiar_a(destino, semana, nombre_zip, datos_zip):
    """Deja en `destino` los 3 .pptx y el ZIP de la semana (carpeta de salidas de otro tablero)."""
    import shutil
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    for pat in _PATRONES:
        shutil.copy2(SALIDA / pat.format(s=semana), destino / pat.format(s=semana))
    (destino / nombre_zip).write_bytes(datos_zip)


def generar_zip(fisicos=None, digitales=None, timeout=1800, destino=None):
    """Corre generar_presentaciones.py y devuelve (nombre_zip, bytes). Lanza RuntimeError si falla.
    Con `destino`, además copia ahí los 3 .pptx y el ZIP."""
    with _lock:
        t0 = time.time() - 2
        cmd = [sys.executable, str(BASE / "generar_presentaciones.py")]
        if fisicos:
            cmd += ["--fisicos", str(fisicos)]
        if digitales:
            cmd += ["--digitales", str(digitales)]
        r = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, timeout=timeout)
        semana = str(fisicos) if fisicos else _semana_mas_reciente(t0)
        res = zip_existente(semana) if semana else None
        if res is None:
            cola = (r.stdout + "\n" + r.stderr).strip().splitlines()[-6:]
            raise RuntimeError("No se generaron las 3 presentaciones. " + " | ".join(cola))
        if destino:
            copiar_a(destino, semana, *res)
        return res
