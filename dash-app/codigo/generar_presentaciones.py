# -*- coding: utf-8 -*-
"""Genera las dos presentaciones semanales en salidas/presentacion/:
Canales Físicos (tablero) y Canales Digitales (Excel oficial). Una no detiene a la otra.

    python generar_presentaciones.py                    # físicos: semana vigente; digitales: Excel más reciente
    python generar_presentaciones.py --fisicos 40 --digitales 42
"""
import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def correr(args: list[str]) -> int:
    print("$", " ".join(args), flush=True)
    return subprocess.call([sys.executable, *args], cwd=BASE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fisicos", default=None, help="semana de físicos (default: vigente del tablero)")
    ap.add_argument("--digitales", default=None, help="semana de digitales (default: la más reciente con Excel)")
    a = ap.parse_args()
    rc_f = correr(["generar_ppt_semanal.py", *(["--semana", a.fisicos] if a.fisicos else [])])
    rc_d = correr(["generar_ppt_digitales.py",*(["--semana", a.digitales] if a.digitales else [])])
    rc_c = correr(["generar_consolidada.py", *(["--semana", a.fisicos] if a.fisicos else [])])
    print(f"\nFísicos: {'OK' if rc_f == 0 else 'ERROR ' + str(rc_f)} · Digitales: {'OK' if rc_d == 0 else 'ERROR ' + str(rc_d)}"
          f" · Consolidada: {'OK' if rc_c == 0 else 'ERROR ' + str(rc_c)}")
    sys.exit(1 if rc_f or rc_d or rc_c else 0)


if __name__ == "__main__":
    main()
