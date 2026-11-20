# -*- coding: utf-8 -*-
"""Genera las 3 presentaciones de la semana y las deja en DASH APP/salidas/presentacion/.
Lo llama MENU_DASH.bat al terminar la actualización semanal (opción A); también corre solo:
    python generar_salidas.py
Autónomo: usa solo archivos de DASH APP. Un error aquí NO invalida la actualización de datos."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from presentaciones_servicio import SALIDA, generar_zip


def main() -> int:
    try:
        nombre, _ = generar_zip()
    except Exception as e:
        print(f"  No se generaron las presentaciones: {e}")
        return 1
    print(f"  Presentaciones listas en {SALIDA} ({nombre})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
