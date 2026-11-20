# -*- coding: utf-8 -*-
"""
dash_app/services/pdf_service.py
Convierte el HTML de los reportes a PDF con Chrome o Edge en modo headless.
xhtml2pdf ignoraba gran parte del CSS (anchos, fondos, bordes finos); el navegador
imprime el mismo diseño que se ve en pantalla.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_NAVEGADORES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _navegador():
    for ruta in _NAVEGADORES:
        if os.path.exists(ruta):
            return ruta
    return shutil.which("chrome") or shutil.which("msedge")


def html_a_pdf(html_str: str) -> bytes:
    """Devuelve los bytes del PDF. Lanza RuntimeError si no hay navegador o no generó el archivo."""
    exe = _navegador()
    if not exe:
        raise RuntimeError("No se encontró Chrome ni Edge para generar el PDF.")
    with tempfile.TemporaryDirectory(prefix="reporte_pdf_") as tmp:
        tmp_path = Path(tmp)
        html_path = tmp_path / "reporte.html"
        pdf_path = tmp_path / "reporte.pdf"
        html_path.write_text(html_str, encoding="utf-8")
        cmd = [
            exe, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
            f"--user-data-dir={tmp_path / 'perfil'}",  # perfil propio: no interfiere con tu Chrome abierto
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        resultado = subprocess.run(cmd, capture_output=True, timeout=120)
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            raise RuntimeError(f"El navegador no generó el PDF (código {resultado.returncode}).")
        return pdf_path.read_bytes()
