# -*- coding: utf-8 -*-
"""
semana.py — Calendario ARG y nombres de parquet por semana.

Fuente única de verdad para "¿en qué semana ARG estamos?" y "¿cómo se llama
el parquet de esta semana?". Antes el nombre estaba escrito a mano como
`r26_sem26.parquet` en extraer_sql.py y build_data.py, así que al pasar la
semana se seguía sobrescribiendo el parquet viejo.

Calendario: semanas de 7 días, de lunes a domingo. La semana 1 de 2026
arranca el lunes 29-dic-2025 (confirmado por el usuario 2026-10-22; así
coincide con ANCLA). 2025 tuvo 52 semanas y su semana 1 arranca el LUNES
30-dic-2024 (2026-10-22: corregido; antes decía 29-dic-2024, un domingo).
Verificado contra SEMANA_ARG y MES de los datos: con el lunes 30-dic-2024 las
semanas de 2025 que cruzan de mes son 1, 5, 9, 14, 18, 22, 27, 31, 40 y 44,
igual que en r25; y 52 semanas desde ahí terminan el 28-dic-2025, justo antes
del lunes 29-dic-2025. Igual que app.py.

Publicación: la semana N aparece en el tablero el JUEVES de la semana N
(la semana 1 empezó el lunes 29-dic y se publicó el jueves 1-ene).
`semana_corte()` aún devuelve la semana de calendario en curso, sin esperar
al jueves.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent.resolve() / "datos"  # los parquets r25/r26/p26 viven en /datos, no en /codigo

ANCLA = {2025: date(2024, 12, 30), 2026: date(2025, 12, 29)}


def semana_arg(d: date | None = None, anio: int = 2026) -> int:
    """Semana ARG (1-53) en la que cae `d`. Por defecto, hoy."""
    d = d or date.today()
    if anio not in ANCLA:
        raise ValueError(f"sin ancla de calendario para {anio}")
    return (d - ANCLA[anio]).days // 7 + 1


def rango_semana(s: int, anio: int = 2026) -> tuple[date, date]:
    """(primer día, último día) de la semana ARG `s`."""
    ini = ANCLA[anio] + timedelta(days=7 * (s - 1))
    return ini, ini + timedelta(days=6)


def semana_corte(d: date | None = None, anio: int = 2026) -> int:
    """Semana de corte = semana ARG en curso (2026-09-24: antes era la anterior,
    pero el tablero ya muestra esa semana como actual — sem_max_real — y los
    parquets quedaban rotulados con una semana de menos)."""
    return semana_arg(d, anio)


def nombres(sem: int | None = None) -> dict[str, str]:
    """{'R25': 'r25_sem28.parquet', 'Real 2025': 'r25_sem28.parquet', ...} para la semana dada."""
    s = sem if sem is not None else semana_corte()
    return {
        "R25": f"r25_sem{s}.parquet",
        "R26": f"r26_sem{s}.parquet",
        "P26": f"p26_sem{s}.parquet",
        "FCST": f"fcst_sem{s}.parquet",
        "Real 2025": f"r25_sem{s}.parquet",
        "Real 2026": f"r26_sem{s}.parquet",
        "Plan 2026": f"p26_sem{s}.parquet",
    }


def parquet_mas_reciente(prefijo: str) -> Path | None:
    """El parquet existente con la semana más alta, p.ej. prefijo='r26'.
    Sirve para leer sin saber qué semana se extrajo por última vez."""
    cands = []
    for p in BASE.glob(f"{prefijo}_sem*.parquet"):
        try:
            cands.append((int(p.stem.split("_sem")[1]), p))
        except (IndexError, ValueError):
            continue
    return max(cands)[1] if cands else None


if __name__ == "__main__":
    hoy = date.today()
    s = semana_arg(hoy)
    ini, fin = rango_semana(s)
    print(f"hoy {hoy} -> semana ARG {s} ({ini} a {fin})")
    print(f"semana de corte (última cerrada): {semana_corte(hoy)}")
    print("parquet de esta semana:")
    for t, n in nombres().items():
        existe = "OK" if (BASE / n).exists() else "falta"
        print(f"  {t:12s} {n:22s} [{existe}]")
    print("más reciente en disco:")
    for pre in ("r25", "r26", "p26"):
        p = parquet_mas_reciente(pre)
        print(f"  {pre}: {p.name if p else '(ninguno)'}")
