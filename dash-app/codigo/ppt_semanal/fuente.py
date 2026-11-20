# -*- coding: utf-8 -*-
"""Fuente de datos de físicos para las presentaciones, propia de DASH APP.
El proyecto Spend Legacy leía aggs/pdc.parquet; aquí se lee la tabla `consolidado` de
aggs/dashboard.duckdb (formato largo: Serie R25/R26/P26 + monto_cons) y se expone con las mismas
columnas (Real_2025_cons, Plan_2026_cons, Real_2026_cons) para no tocar el resto del cálculo."""
from pathlib import Path

import duckdb

DUCKDB = Path(__file__).resolve().parents[2] / "aggs" / "dashboard.duckdb"

PDC = f"""(
    select sem, cat_Agrupa1, cat_Direccion_Division,
           case when cat_Subdireccion_Territorio = '0' then 'Sin agrupar'   -- el consolidado de Dash guarda "Sin agrupar" como '0'
                else cat_Subdireccion_Territorio end as cat_Subdireccion_Territorio,
           cat_Grupo_de_Cuentas, cat_Cuentas,
           case when Serie = 'R25' then monto_cons end as Real_2025_cons,
           case when Serie = 'P26' then monto_cons end as Plan_2026_cons,
           case when Serie = 'R26' then monto_cons end as Real_2026_cons
    from consolidado
)"""


def conectar():
    return duckdb.connect(str(DUCKDB), read_only=True)
