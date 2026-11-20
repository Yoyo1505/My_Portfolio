# -*- coding: utf-8 -*-
"""Atajo: presentación semanal de Canales Digitales (ver ppt_semanal/LEEME.md).

    python generar_ppt_digitales.py                  # semana vigente, fuente SQL (datos/digitales)
    python generar_ppt_digitales.py --semana 42      # otra semana
    python generar_ppt_digitales.py --fuente excel --excel "<ruta .xlsx>"   # cuadre contra el Excel oficial
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ppt_semanal.digitales_ppt import main

if __name__ == "__main__":
    main()
