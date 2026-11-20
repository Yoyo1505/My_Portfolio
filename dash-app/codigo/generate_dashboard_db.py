#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Regenera aggs/dashboard.duckdb.

2026-10-21: ya no relee el parquet crudo ni crea indices. La BD se genera dentro de build_data.py (etapa
`duckdb`), desde el mismo consolidado post-procesado que alimenta los aggs: una pasada, una sola fuente.
Este script solo la invoca, para que MENU_DASH opcion 6 siga funcionando. Conserva la semana de corte
que ya esta en aggs/_meta.json (no la recalcula ni la reescribe) y usa POCA RAM: el consolidado se escribe
directo al archivo (no se materializa en memoria). Ajustable: DUCKDB_THREADS (def. 6) y DUCKDB_MEMORY_LIMIT (def. 8GB).

USAR: python generate_dashboard_db.py
"""
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent.resolve()
env = dict(os.environ)
env.setdefault("DUCKDB_THREADS", "6")
env.setdefault("DUCKDB_MEMORY_LIMIT", "8GB")
sys.exit(subprocess.run([sys.executable, str(BASE / "build_data.py"), "--aggs-only", "--solo", "duckdb"],
                        cwd=str(BASE), env=env).returncode)
