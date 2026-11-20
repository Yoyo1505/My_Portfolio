# -*- coding: utf-8 -*-
"""Atajo: presentación Consolidada (Físicos + Digitales en un solo pptx) de la semana vigente.

Requiere Gasto_semana_NN.pptx y Gasto_digitales_semana_NN.pptx ya generados (ver generar_presentaciones.py).
Usa la fuente SQL de digitales (datos/digitales/*.parquet): la del Excel no cuadra las filas de grupo con
las de físicos (ver ppt_semanal/LEEME.md).

    python generar_consolidada.py              # semana vigente (aggs/_meta.json -> sem_max_real)
    python generar_consolidada.py --semana 42
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
META = BASE.parent / "aggs" / "_meta.json"


def semana_vigente() -> int:
    return int(json.loads(META.read_text(encoding="utf-8-sig"))["sem_max_real"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--semana", type=int, default=None)
    a = ap.parse_args()
    sem = a.semana or semana_vigente()
    rc = subprocess.call([sys.executable, "-m", "ppt_semanal.digitales_ppt",
                          "--consolidar", "--semana", str(sem), "--fuente", "sql"], cwd=BASE)
    sys.exit(rc)


if __name__ == "__main__":
    main()
