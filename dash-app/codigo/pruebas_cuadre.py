# -*- coding: utf-8 -*-
"""
codigo/pruebas_cuadre.py
Prueba de cuadre tras un build: cifras de control (sem 1..semana de corte, Monto Constante)
contra el KPI de Resumen y contra Tabla Dinámica. Sale con código 1 si algo no cuadra.

Uso:  python codigo\pruebas_cuadre.py
Las cifras de control son de la semana 39; si cambia la semana de corte, actualizar CONTROL.
"""
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONTROL = {39: {"Real_2026": 21_407_592_317.07, "Real_2025": 23_046_033_056.72}}
TOL = 1.0  # pesos


def main() -> int:
    from dash_app.services.data_service import DataService
    from dash_app.pages import tabla_dinamica as td

    ds = DataService()
    sem = ds.smr
    f = {"tipo_monto": "Monto Constante", "si": 1, "sf": sem}
    fallos = []

    def check(nombre, obtenido, esperado):
        ok = abs(obtenido - esperado) <= TOL
        print(f"{'OK ' if ok else 'FALLA'} {nombre}: {obtenido:,.2f} (esperado {esperado:,.2f})")
        if not ok:
            fallos.append(nombre)

    k = ds.compute_summary_kpis(f)
    for medida, esperado in CONTROL.get(sem, {}).items():
        check(f"KPI {medida} sem 1-{sem}", k[medida], esperado)
    if sem not in CONTROL:
        print(f"AVISO: sin cifras de control para la semana {sem}; solo se compara entre vistas.")

    for dims in (["cat_Grupo_de_Cuentas"], ["cat_Cuentas"], ["cat_Grupo_de_Cuentas", "cat_Cuentas"]):
        t, _, _ = td._df_plano_td(f, dims, {})
        etq = "+".join(dims)
        check(f"TablaDin[{etq}] Real_2026 = KPI", t["Real_2026"].sum(), k["Real_2026"])
        check(f"TablaDin[{etq}] Forecast = KPI", t["Forecast"].sum(), k["Forecast_Cierre"])

    print("\nRESULTADO:", "TODO CUADRA" if not fallos else f"{len(fallos)} FALLA(S): {fallos}")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
