# -*- coding: utf-8 -*-
"""Atajo: genera la presentación semanal de Canales Físicos (ver ppt_semanal/LEEME.md).

    python generar_ppt_semanal.py                # semana vigente del tablero
    python generar_ppt_semanal.py --semana 39
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ppt_semanal.generar_ppt import main

if __name__ == "__main__":
    main()
