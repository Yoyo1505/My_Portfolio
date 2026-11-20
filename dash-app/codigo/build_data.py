# -*- coding: utf-8 -*-
"""
===============================================================================
build_data.py — PIPELINE PRINCIPAL DE PROCESAMIENTO DE DATOS (ETL)
===============================================================================

Este script es el motor de extracción, transformación y carga (ETL) para el 
Dashboard de Gasto Canales Grupo Aurora.

FUENTES DE DATOS DE ENTRADA:
-------------------------------------------------------------------------------
1. Archivos Parquet extraídos de SQL Server (vía extraer_sql.py):
   - r25_semXX.parquet  : Real 2025 (acumulado semanal)
   - r26_semXX.parquet  : Real 2026 (acumulado semanal)
   - p26_semXX.parquet  : Plan 2026 (presupuesto inicial)
2. Catálogos Excel (en la carpeta del proyecto):
   - Catálogo grupo cuentas.xlsx : Cruza Cta Mayor (ID_CONCEPTO_CUENTA_NIV3) con Grupo de Cuentas y PosPre.
   - Catálogo de estructura.xlsx : Cruza CECO (ID_CENTRO_COSTOS) con la Jerarquía Territorial (División, Territorio, Zona, Región, PDC, Agrupa 1-3).
   - FCST VTA RAPIDA.xlsx : Forecast por Cta Mayor.

SALIDAS GENERADAS (en la carpeta aggs/):
-------------------------------------------------------------------------------
1. aggs/_consolidado.parquet : Tabla acumulada unificada (Serie x Semana x CECO x CtaMayor)
2. aggs/<dimensión>.parquet  : Archivos precomputados por cada dimensión (división, territorio, cuentas, etc.)
3. aggs/_meta.json           : Metadatos con rango de semanas reales y presupuestadas.

INSTRUCCIONES DE USO MANUAL:
-------------------------------------------------------------------------------
  - Reconstruir todo desde cero:     python build_data.py --rebuild
  - Regenerar solo agregados (.parquet): python build_data.py --aggs-only
  - Forzar semana específica:        python build_data.py --semana 31
===============================================================================
"""
import argparse
import gc
import json
import os
import subprocess
import sys
import threading
import time
import yaml
from datetime import datetime, date, timedelta
from pathlib import Path

import duckdb
import polars as pl
import semana
import catalogos
import build_series
from catalogos import obtener_cat_estructura, obtener_cat_cuentas, obtener_ceco_nombres_concil

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# -----------------------------------------------------------------------------
# 1. CONFIGURACIÓN DE RUTAS Y CONSTANTES GLOBALES
# -----------------------------------------------------------------------------
BASE = Path(__file__).parent.resolve()   # /codigo — código y config.yaml
ROOT = BASE.parent                       # raíz del proyecto (tras reorganización 2026-08-14)
DATOS = ROOT / "datos"                   # parquets, Excel, JSON de datos
ARCHIVOS = ROOT / "archivos_proyecto"    # docx, otros
OUT = ROOT / "aggs"                      # Carpeta de agregados precomputados (NO se movió)
OUT.mkdir(exist_ok=True)
DL = Path.home() / "Downloads"

# Constantes numéricas y de configuración del pipeline ETL
TOTAL_STEPS_PIPELINE = 43               # Pasos totales del pipeline de reconstrucción
# 2026-08-24: máquina de 16 núcleos/22 hilos y 31GB RAM medida en vivo -> 4
# hilos/6GB dejaba la mayor parte del hardware sin usar durante el rebuild
# (cuello de botella real, confirmado con el pipeline corriendo). Subido a
# 16 hilos/20GB (deja ~2 núcleos y ~11GB para el resto del sistema). Si se
# corre en una máquina más chica, bajar estos dos valores.
DUCKDB_THREADS = int(os.environ.get("DUCKDB_THREADS", "16"))          # Hilos concurrentes de DuckDB
DUCKDB_MEMORY_LIMIT = os.environ.get("DUCKDB_MEMORY_LIMIT", "20GB")   # Límite de memoria para DuckDB (env para bajarlo)
TOTAL_SEMANAS_ANIO = 53                 # Semanas en el año fiscal ARG
FACTOR_PORCENTAJE = 100.0               # Factor para cálculo de porcentaje


def _fuente(prefijo, tabla, sem):   # sem: int o None (py3.9: sin `int | None`)
    """Parquet de la semana pedida; si no está, el más reciente en disco.
    Antes esto era el literal 'r26_sem26.parquet' y por eso la app seguía
    leyendo el corte viejo aunque se hubiera extraído uno nuevo."""
    p = DATOS / semana.nombres(sem)[tabla]
    if not p.exists():
        alt = semana.parquet_mas_reciente(prefijo)
        if alt is None:
            raise FileNotFoundError(
                f"no hay parquet de {tabla}: falta {p.name} y no existe ningún "
                f"{prefijo}_sem*.parquet. Corre primero: python extraer_sql.py")
        print(f"[aviso] {p.name} no existe; uso {alt.name}", flush=True)
        p = alt
    return p.as_posix()


R25 = R26 = P26 = FCST = None    # se resuelven en _resolver_fuentes()

# 2026-10-21: FCST PLAN (tabla FCST del servidor), igual que en Spend Legacy: Real 2026 sem 1-40
# congelado (r26_sem40.parquet) + tabla FCST sem 41+. Está CONGELADO en fcst_sem42.parquet; solo cambia
# si se borra ese archivo o se define la variable de entorno FCST_DESCONGELAR=1.
# No es el "Forecast Finanzas" (Excel, build_forecast_cuenta): son dos series distintas con nombre propio.
FCST_FIJO = DATOS / "fcst_sem42.parquet"
R26_CONGELADO = DATOS / "r26_sem40.parquet"


def _resolver_fuentes(sem=None):
    global R25, R26, P26, FCST
    R25 = _fuente("r25", "R25", sem)
    R26 = _fuente("r26", "R26", sem)
    P26 = _fuente("p26", "P26", sem)
    try:
        FCST = _fuente("fcst", "FCST", sem)
    except FileNotFoundError:
        FCST = None      # sin FCST la etapa fcst_srv se omite con aviso; el resto del build sigue igual
    if FCST_FIJO.exists() and not os.environ.get("FCST_DESCONGELAR"):
        FCST = FCST_FIJO.as_posix()
    print(f"[fuentes] {Path(R25).name} · {Path(R26).name} · {Path(P26).name} · {Path(FCST).name if FCST else 'sin FCST'}", flush=True)


CONS = OUT / "_consolidado.parquet"

# 2026-10-22 (pedido del usuario, PLAN_AGGS_POR_SERIE): consolidado, aggs y rollups POR SERIE (R25, P26, R26)
# con caché incremental en aggs/series/ (ver build_series.py). APAGADO POR DEFECTO: se activa con `--series`.
# Medido el 2026-10-22: aggs idénticos al modo normal y etapas previas a `duckdb` ~70% más rápidas, pero la
# etapa `duckdb` (escribir `consolidado` de 85 M filas) no mejoró, así que el tiempo total no justifica
# cambiar el modo por defecto. SERIES_CTX lo fija main(); None = modo normal.
SERIES_CTX = None


def _sql_consolidado_final(origen: str = "consolidado_raw") -> str:
    """SELECT del consolidado post-procesado, a partir de `origen` (vista/tabla con el esquema completo).
    2026-10-21 (pedido del usuario): el tablero es FIEL al catálogo. Los '0' de PDC/Territorio/Zona/Región se dejan TAL
    CUAL (antes se sustituían por 'ID · Nombre' y 'Sin agrupar'); los árboles los separan por rama con la ruta de
    ancestros (un mismo nombre —p. ej. PDC '0'— cuelga de muchas regiones). Lo que el catálogo no trae sigue como
    'No Clasificado' (join en build_consolidado).
    2026-09-01 (pedido del usuario): un CECO 'No Clasificado' (sin División en el catálogo) SIN NINGÚN movimiento
    (suma de monto_cons = 0) se omite; si tiene movimiento NO se toca (ocultar gasto real sería peligroso)."""
    return f"""
        SELECT r.*
        FROM {origen} r
        WHERE r.ID_CENTRO_COSTOS NOT IN (
            SELECT ID_CENTRO_COSTOS FROM {origen}
            WHERE cat_Direccion_Division = 'No Clasificado'
            GROUP BY ID_CENTRO_COSTOS
            HAVING SUM(monto_cons) = 0
        )
    """


def _select_consolidado(con) -> str:
    """SELECT sobre el caché _consolidado.parquet que devuelve SIEMPRE el esquema completo.
    2026-10-21: el caché ya no guarda `monto` (idéntica a monto_cons en el 100% de las filas,
    medido sobre 113M de filas: -348 MB); aquí se reconstruye para que nada aguas abajo cambie.
    Acepta también el caché en formato anterior (que sí trae `monto`)."""
    ruta = CONS.as_posix()
    cols = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{ruta}')").fetchall()}
    if "monto" in cols:
        return f"SELECT * FROM read_parquet('{ruta}')"
    return f"SELECT *, monto_cons AS monto FROM read_parquet('{ruta}')"

# Forecast por Cta Mayor (2026-07-24, reemplaza el forecast_ceco.parquet SOLO
# en las columnas Forecast/vs Forecast de tablas/árboles que se puedan cruzar
# por ID_CONCEPTO_CUENTA_NIV3 — Resumen, Div/Terr jerarquía Contable, Detalle
# Cuenta. La pestaña "Plan + Real" NO se toca, sigue con forecast_ceco.parquet.
CAT_FORECAST_CUENTA = DATOS / "Forecast Finanzas 2026.xlsx"   # 2026-10-20: reemplaza FCST VTA RAPIDA.xlsx (igual que Spend Legacy)
# hoja '4.Detalle PDC': Eco -> Nombre; da nombre a los CECOs sin PDC.
# 2026-08-26: se cachea en ceco_nombres_concil.parquet vía catalogos.py
# (obtener_ceco_nombres_concil) — antes se reabría el .xlsx (10 hojas, ~44
# columnas) con openpyxl en CADA corrida de build_data.py aunque solo se
# necesitaran 2 columnas de 1 hoja: ~3.4s de abrir el workbook, siempre,
# incluso en un --solo de puras etapas de catálogo. La localización del
# archivo más reciente (_concil_mas_reciente, por número de semana) también
# vive ahora en catalogos.py.
# 2026-08-24: se eliminó CAT_PUNTOS ("Puntos de contacto.xlsx") — quedaba
# desactualizado frente al Catálogo de estructura (decisión del usuario).
# pdc_ids.json (ID · Nombre) y el flag de "oficial" salen ahora 100% de
# Catálogo de estructura.xlsx, columnas "PDC" / "Eco PDV" (ver build_pdc_ids
# y ECO_PDV en build_consolidado).
# Pestaña Cierres (2026-07-24, reemplaza cat_Agrupa3 por completo): categorías
# de "Proyecto" (Mismos/Nuevos/Baja/Transformaciones/Transformaciones
# Matriz/Aperturas Modulo de Préstamos/Aperturas) por PDC, desde el Master
# Cierres de Planeación. Se reemplaza manualmente cada semana con el mismo
# nombre de archivo (decisión del usuario), copiado a la carpeta del
# dashboard por la misma razón que los demás catálogos (ver nota arriba).
CAT_CIERRES_EXP = DATOS / "Seguimiento Expansion.xlsx"

# -----------------------------------------------------------------------------
# 2. DEFINICIÓN DE DIMENSIONES Y JERARQUÍAS PARA AGREGACIÓN
# -----------------------------------------------------------------------------
# Cargar configuración de dimensiones desde config.yaml
with open(BASE / "config.yaml", "r", encoding="utf-8") as f:
    _config = yaml.safe_load(f)

# 2026-08-24: "ceco" NO va aquí -> ceco.parquet/ceco_detalle.parquet se
# generan con funciones dedicadas (build_ceco_general/build_ceco_detalle),
# no con el loop genérico de build_aggs(), para controlar a mano qué columnas
# lleva cada uno y mantenerlos ligeros (ID_CENTRO_COSTOS tiene ~5x más
# cardinalidad que cat_PDC -> cruzarlo con cat_Cuentas explota el tamaño).
# 2026-08-24: se quitaron los alias cuyas columnas ya no viajan en el
# consolidado (formato, naturaleza, agrupador, agrupadores, clasificacion2,
# segmento1, segmento2, estatus) — ninguna pestaña los consumía. "conciliacion"
# y "trimestre" tampoco existen en config.yaml/DIMS, así que nunca se generaban.
BUILD_DIMS_KEYS = {
    "division", "grupo_cuentas", "cuentas", "agrupa2", "agrupa3", "global"
}
DIMS = {k: v[1] for k, v in _config["DIMS"].items() if k in BUILD_DIMS_KEYS}


# HIER: Define las columnas padre necesarias para cada dimensión.
# Esto permite que los árboles perezosos (drill-down) mantengan el contexto completo.
HIER = {
    "division": ["cat_Agrupa1", "cat_Prefijo"],
    "territorio": ["cat_Agrupa1", "cat_Direccion_Division"],
    "zona": ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio"],
    "region": ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona"],
    "pdc": ["cat_Agrupa1", "cat_Grupo_de_Cuentas", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
            "cat_Subdireccion_Zona", "cat_Subdireccion_Region"],
    "cuentas": ["cat_Agrupa1", "cat_Grupo_de_Cuentas"],
    "agrupa2": ["cat_Agrupa1"],
    "agrupa3": ["cat_Agrupa1", "cat_Agrupa2"],
}

# -----------------------------------------------------------------------------
# 3. MAPEO DE COLUMNAS DE CATÁLOGOS EXCEL A COLUMNAS PARQUET DE LA APP
# (Nombre exacto en Excel, Nombre técnico en Parquet/App)
# -----------------------------------------------------------------------------
# 2026-08-24: se recortaron 11 columnas de estructura y 1 de cuentas que NO
# usa ninguna pestaña ni generador (verificado con grep sobre todo codigo/:
# solo aparecían en declaraciones de config, nunca en lógica). Cada una
# multiplicaba ~30M filas en _consolidado.parquet -> rebuild más lento y
# archivo más grande sin beneficio.
# Eliminadas de estructura: Formato, Agrupador, Agrupadores, Clasificacion_2,
#   Conciliación, Segmento 1, Segmento 2, Estatus, Aperturas,
#   "PDV Cierres - Transformaciones - Aperturas", Semana de Cierre.
# Eliminada de cuentas: Naturaleza.
# Si alguna vuelve a necesitarse, basta con re-agregarla aquí (y su alias en
# BUILD_DIMS_KEYS/config.yaml si además debe generar su propio agg) y correr
# un --rebuild.
COLS_ESTRUCT = [
    ("Prefijo", "cat_Prefijo"),                              # filtro de Análisis PDC
    ("Dirección / División", "cat_Direccion_Division"),
    ("Subdirección / Territorio", "cat_Subdireccion_Territorio"),
    ("Subdirección / Zona", "cat_Subdireccion_Zona"),
    ("Subdirección / Región", "cat_Subdireccion_Region"),
    ("PDC", "cat_PDC"),
    ("Agrupa 1", "cat_Agrupa1"),
    ("Agrupa 2", "cat_Agrupa2"),                             # padre de agrupa3 (HIER/PARENTS)
    ("Agrupa 3", "cat_Agrupa3"),                             # cierres_pdc.parquet
    ("Subtipo", "cat_Subtipo"),                              # filtro global
    ("Contraloria", "cat_Contraloria"),                       # pestaña Contraloria (árbol Cuenta<->Contraloria)
    ("Contraloria_6", "cat_Contraloria_6"),
]
COLS_CUENTAS = [
    ("Grupo de Cuentas", "cat_Grupo_de_Cuentas"),
    ("Cuentas", "cat_Cuentas"),
    ("Agrupador Reales", "cat_Agrupador_Reales"),            # árboles Detalle Cuenta / Cierres
    ("Denom.Cta. Mayor", "cat_CtaMayor_Nombre"),             # FILTROS_LIGEROS + cierres_expansion
    ("PosPre", "cat_PosPre"),                                # pdc_pospre + agg 'cuentas'
    ("Denom.PosPre", "cat_Denom_PosPre"),
]

MEDIDAS = ["Real_2025", "Plan_2026", "Real_2026"]
MEDIDAS_CONS = ["Real_2025_cons", "Plan_2026_cons", "Real_2026_cons"]

# ------------------------------------------------------- cuentas duplicadas (split 1:2)
# El catálogo de cuentas trae 20 "Cta Mayor" duplicadas (llaves repetidas, problema
# conocido). De esas, estas 12 tienen un PosPre DISTINTO entre sus 2 filas duplicadas:
# es una relación real 1:2 (misma cuenta, mismo "Denom.Cta. Mayor", reparte a 2
# posiciones presupuestales) y NO dato sucio -> decisión del usuario: conservar AMBAS
# filas (split) en vez de deduplicar con keep="first". El consolidado de movimientos
# no dice a cuál PosPre pertenece cada movimiento (solo agrega por Cta Mayor), así
# que NO se debe dividir el monto: cada movimiento se muestra completo (100%) bajo
# AMBAS combinaciones Grupo+PosPre (fan-out real del join), con una nota en la UI
# aclarando que es el mismo monto duplicado por falta de detalle de PosPre en el
# origen -> decisión del usuario 2026-07-20 (antes se partía 50/50, estaba mal:
# nadie pidió prorratear, solo mostrar el detalle). Para 2 de las 12 (6010100047 y
# 6250100014) el "Grupo de Cuentas" TAMBIÉN cambia entre filas duplicadas -> "split
# completo": se respeta la combinación (Grupo de Cuentas, PosPre) de cada fila como
# combinación válida distinta, cada una con el monto completo.
CUENTAS_SPLIT_POSPRE = [
    "1140999006", "1140999025", "1140999041", "1150090107", "6010100047",
    "6010600004", "6200501009", "6200600017", "6210100006", "6210119001",
    "6250100004", "6250100014",
]

PROGRESS = None

# Segundos que tarda cada paso en una corrida semanal completa (medido en las corridas del 22-oct y 29-oct-2026).
# Sirven para que el porcentaje y el tiempo restante reflejen el trabajo real (un paso de `duckdb` pesa 400 s, un JSON 1 s)
# y no solo "pasos hechos / pasos totales". El primer prefijo que coincide gana; lo desconocido pesa PESO_PASO_DEFECTO.
PESOS_PASO = [
    ("Leyendo catálogos", 5), ("Uniendo 3 series", 3), ("Construyendo y persistiendo", 60),
    ("Consolidado por serie", 150), ("Usando", 5), ("Mapeando", 20), ("Detectando", 2), ("Agg ", 3),
    ("Generando cierres_expansion", 4), ("Generando forecast_cuenta", 2), ("Generando fcst_srv", 42),
    ("Generando agrupadores_reales", 3), ("Generando contraloria", 2), ("Generando cierres_arbol", 108),
    ("Generando dashboard.duckdb", 2), ("duckdb: consolidado", 140), ("duckdb: base de rollups", 110),
    ("duckdb: rollup_pdv_general_gpo", 62), ("duckdb: rollup_pdv_general_prefijo", 6),
    ("duckdb: rollup_pdv_general", 8), ("duckdb: rollup_pdv_porcuenta", 78),
    ("duckdb: rollup_ceco_general_gpo", 70), ("duckdb: rollup_ceco_general", 7),
    ("duckdb: rollup_mes_contraloria", 3), ("duckdb: aggs y digitales", 45),
    ("Generando", 3), ("Validando", 2),
]
PESO_PASO_DEFECTO = 10
LATIDO_SEG = 30          # cada cuánto se avisa que una etapa larga sigue viva (una sola consulta de DuckDB no imprime nada)


def _peso_paso(detalle: str) -> int:
    for prefijo, seg in PESOS_PASO:
        if detalle.startswith(prefijo):
            return seg
    return PESO_PASO_DEFECTO


def _fmt_dur(seg: float) -> str:
    seg = int(max(0, seg))
    return f"{seg // 60}m {seg % 60:02d}s"


def _tam_gb(ruta: Path) -> float:
    """Tamaño en GB de un archivo o, si es carpeta, de todo lo que contiene (0 si no existe)."""
    try:
        if ruta.is_dir():
            return sum(e.stat().st_size for e in os.scandir(ruta) if e.is_file()) / 1e9
        return ruta.stat().st_size / 1e9
    except OSError:
        return 0.0


def _cpu_sistema():
    """(ocupado, total) acumulados del equipo en unidades de 100 ns, o None. Incluye los procesos hijos (escritura de
    `consolidado` por serie), que el contador de CPU de este proceso no ve."""
    try:
        import ctypes
        from ctypes import wintypes
        idle, kern, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not ctypes.WinDLL("kernel32").GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return None
        v = lambda f: (f.dwHighDateTime << 32) | f.dwLowDateTime
        total = v(kern) + v(user)               # el tiempo de kernel ya incluye el inactivo
        return total - v(idle), total
    except Exception:
        return None


def _io_bytes():
    """(bytes leídos, bytes escritos) acumulados por este proceso (incluye el derrame a _ddb_tmp); (0, 0) si no se puede leer."""
    try:
        import ctypes
        from ctypes import wintypes

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ro", "wo", "oo", "rb", "wb", "ob")]
        c, k = _IO(), ctypes.WinDLL("kernel32")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.POINTER(_IO)]
        k.GetProcessIoCounters.restype = wintypes.BOOL
        if k.GetProcessIoCounters(k.GetCurrentProcess(), ctypes.byref(c)):
            return c.rb, c.wb
    except Exception:
        pass
    return 0, 0


class ProgressTracker:
    """Barra de progreso del build. Con consola real usa tqdm; con salida redirigida (actualizar.py la lee por tubería)
    imprime una línea `Paso n/total: ...` por paso. `fijar_plan` calcula el total exacto de pasos (antes quedaba
    fijo en len(--solo)+3 y la barra se desbordaba: "13/?") y el porcentaje/tiempo restante se ponderan con PESOS_PASO.
    Un hilo de latido avisa cada LATIDO_SEG segundos qué etapa sigue corriendo, cuánto lleva y qué tanto escribe en disco."""

    def __init__(self, total_steps=TOTAL_STEPS_PIPELINE, desc="Rebuild Pipeline"):
        self.total_steps = total_steps
        self.current_step = 0
        self.completados = 0
        self.t0 = time.time()
        self.peso_total = 0          # 0 = sin plan: porcentaje por número de pasos
        self.peso_hecho = 0
        self._peso_actual = 0
        self._detalle = ""
        self._t_paso = self.t0
        self._lock = threading.Lock()
        self._parar = threading.Event()
        self.pbar = None
        if sys.stdout.isatty():
            try:
                from tqdm import tqdm
                self.pbar = tqdm(
                    total=total_steps,
                    desc=desc,
                    unit="paso",
                    ncols=100,
                    bar_format="{l_bar}{bar:25}| {n_fmt}/{total_fmt} [{elapsed}] {postfix}",
                    file=sys.stdout
                )
            except Exception:
                self.pbar = None
        self.estado_file = DATOS / "estado.json"
        self._hilo = threading.Thread(target=self._latido, daemon=True)
        self._hilo.start()

    def fijar_plan(self, plan):
        """plan = [(texto_del_paso, num_steps), ...] en el orden en que va a correr. Fija el total exacto de pasos."""
        with self._lock:
            self.total_steps = max(1, sum(n for _, n in plan))
            self.peso_total = sum(_peso_paso(t) for t, _ in plan)
            if self.pbar is not None:
                self.pbar.total = self.total_steps
                self.pbar.refresh()

    def _pct_eta(self):
        """(porcentaje, texto de tiempo restante) con los pasos ya terminados."""
        elapsed = time.time() - self.t0
        if self.peso_total > 0:
            hecho, total = self.peso_hecho, self.peso_total
        else:
            hecho, total = self.completados, self.total_steps
        pct = int(min(1.0, hecho / total) * FACTOR_PORCENTAJE) if total else 0
        if hecho >= 0.05 * total and elapsed > 20:
            # Ritmo = tiempo real hasta que empezó el paso actual / peso de los pasos ya terminados. Antes se usaba el tiempo
            # total transcurrido y, durante un paso largo (escribir `consolidado`, 7 min), el "restante" crecía en vez de bajar.
            ritmo = (self._t_paso - self.t0) / hecho
            dentro = time.time() - self._t_paso
            if self.peso_total > 0:
                esperado = max(0.0, total - hecho) * ritmo                 # incluye el paso actual completo
                despues = max(0.0, total - hecho - self._peso_actual) * ritmo
                restante = max(despues, esperado - dentro)                 # nunca menos que lo que falta DESPUÉS del paso actual
            else:
                restante = max(0.0, total - hecho) * ritmo
            return pct, f"~{_fmt_dur(restante)} rest."
        return pct, "calculando..."

    def step(self, detalle: str, num_steps: int = 1):
        # step() se llama AL INICIAR cada paso: el paso anterior ya terminó, el actual todavía no.
        # Un bloque de num_steps>1 (etapas en caché) se da por terminado de un golpe.
        with self._lock:
            self.peso_hecho += self._peso_actual
            anterior = self.current_step
            self.current_step += num_steps
            self.total_steps = max(self.total_steps, self.current_step)   # nunca "13/?": si el plan se quedó corto, crece
            if num_steps > 1:
                self.peso_hecho += _peso_paso(detalle)
                self._peso_actual = 0
                self.completados = self.current_step
            else:
                self._peso_actual = _peso_paso(detalle)
                self.completados = anterior
            self._detalle, self._t_paso = detalle, time.time()
            pct, eta_str = self._pct_eta()
            n, total = self.current_step, self.total_steps

        if self.pbar is not None:
            self.pbar.total = total
            self.pbar.set_postfix_str(f"{detalle[:35]} · {pct}% ({eta_str})")
            self.pbar.update(num_steps)
            sys.stdout.flush()
        else:
            print(f"Paso {n}/{total}: {detalle} · {pct}% · {eta_str}", flush=True)

        self._update_estado("procesando", pct, f"Paso {n}/{total}: {detalle} ({eta_str})")

    def _latido(self):
        """Mientras una consulta larga corre no hay líneas de log: aquí se avisa que sigue viva y qué tan activa está."""
        cpu0, t_ant = time.process_time(), time.time()
        sis0 = _cpu_sistema()
        rd0, wr0 = _io_bytes()
        while not self._parar.wait(LATIDO_SEG):
            with self._lock:
                detalle, t_paso, n, total = self._detalle, self._t_paso, self.current_step, self.total_steps
            if not detalle:
                continue
            ahora = time.time()
            cpu1 = time.process_time()
            dt = max(ahora - t_ant, 1e-6)
            nucleos = (cpu1 - cpu0) / dt
            cpu0, t_ant = cpu1, ahora
            nuevo = OUT / "dashboard.duckdb.nuevo"
            rd1, wr1 = _io_bytes()
            sis1 = _cpu_sistema()
            if sis0 and sis1 and sis1[1] > sis0[1]:   # CPU de todo el equipo (incluye procesos hijos), no solo de este proceso
                nucleos = (sis1[0] - sis0[0]) / (sis1[1] - sis0[1]) * (os.cpu_count() or 1)
            sis0 = sis1
            extras = [f"{nucleos:.1f} núcleos activos"]
            if rd1 or wr1:
                extras.append(f"disco: lee {(rd1 - rd0) / dt / 1e6:,.0f} MB/s, escribe {(wr1 - wr0) / dt / 1e6:,.0f} MB/s")
            rd0, wr0 = rd1, wr1
            gb_db = _tam_gb(nuevo) + _tam_gb(Path(str(nuevo) + ".wal"))
            if gb_db:
                extras.append(f"dashboard.duckdb.nuevo {gb_db:.1f} GB")
            gb_tmp = _tam_gb(Path("_ddb_tmp"))
            if gb_tmp:
                extras.append(f"derrame a disco {gb_tmp:.1f} GB")
            pct, eta_str = self._pct_eta()
            try:
                self.log(f"  ... sigue en curso: {detalle} · lleva {_fmt_dur(ahora - t_paso)} · {' · '.join(extras)} · "
                         f"avance {pct}% ({eta_str})")
                self._update_estado("procesando", pct,
                                    f"Paso {n}/{total}: {detalle} · lleva {_fmt_dur(ahora - t_paso)} ({eta_str})")
            except Exception:
                pass

    def log(self, msg: str):
        # 2026-08-24: "+Xm Ys" desde que arrancó el pipeline, en CADA línea
        # (no solo en la barra tqdm) -> pedido del usuario: quien esté viendo
        # la corrida (o releyendo el log si el output se redirigió a archivo,
        # donde la barra tqdm sale ilegible) debe poder ver cuánto lleva
        # corriendo sin tener que restar timestamps a mano.
        transcurrido = time.time() - self.t0
        marca = f"+{int(transcurrido // 60)}m {int(transcurrido % 60):02d}s"
        msg_str = f"[{time.strftime('%H:%M:%S')} {marca}] {msg}"
        if self.pbar is not None:
            try:
                self.pbar.write(msg_str)
            except UnicodeEncodeError:
                self.pbar.write(msg_str.encode("ascii", "replace").decode("ascii"))
        else:
            try:
                print(msg_str, flush=True)
            except UnicodeEncodeError:
                print(msg_str.encode("ascii", "replace").decode("ascii"), flush=True)
        sys.stdout.flush()

    def _update_estado(self, fase: str, pct: int, detalle: str, error=None):
        try:
            import os
            data = {
                "fase": fase,
                "pct": pct,
                "detalle": detalle,
                "error": error,
                "actualizado": datetime.now().isoformat(timespec="seconds"),
                "corriendo": fase not in ("listo", "error", "sin_cambios"),
                "pid": os.getpid(),
            }
            with self._lock:
                self.estado_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass

    def close(self, ok=True):
        self._parar.set()
        if self.pbar is not None:
            self.pbar.close()
        sys.stdout.flush()
        elapsed_min = (time.time() - self.t0) / 60.0
        if ok:
            self._update_estado("listo", int(FACTOR_PORCENTAJE), f"Completado exitosamente en {elapsed_min:.1f} min")
        else:
            self._update_estado("error", 0, "Error durante el rebuild")


def log(msg):
    if PROGRESS is not None and hasattr(PROGRESS, "log"):
        PROGRESS.log(msg)
    else:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _registrar_tiempo_build(ok: bool, dur_seg: float, args) -> None:
    """Historial de duración de cada corrida (rebuild/resume/aggs-only) en
    datos/build_timings.json — pedido del usuario 2026-08-24 para poder medir
    tiempos reales entre corridas y detectar regresiones/mejoras (ej. threads,
    :memory: vs archivo) en vez de adivinar. Se guarda SIEMPRE, incluso si el
    pipeline falla (ok=False), para no perder la medición de corridas rotas.
    Nunca debe tumbar el pipeline -> todo en try/except."""
    try:
        fp = DATOS / "build_timings.json"
        registros = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
        modo = ("aggs-only" if args.aggs_only else
                "resume" if args.resume else
                "rebuild" if args.rebuild else "normal")
        cons_mb = round(CONS.stat().st_size / 1e6, 1) if CONS.exists() else None
        registros.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "modo": modo,
            "ok": ok,
            "duracion_seg": round(dur_seg, 1),
            "duracion_min": round(dur_seg / 60, 2),
            "consolidado_mb": cons_mb,
            "duckdb_threads": DUCKDB_THREADS,
            "duckdb_memory_limit": DUCKDB_MEMORY_LIMIT,
        })
        registros = registros[-200:]  # no crecer sin límite
        fp.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  build_timings.json: corrida registrada ({dur_seg/60:.1f} min, modo={modo})")
    except Exception as e:
        log(f"  AVISO: no pude registrar tiempo en build_timings.json: {e}")


# ---------------------------------------------------------------- consolidado
def build_consolidado(con: duckdb.DuckDBPyConnection, forzar_catalogos: bool = False, interactivo: bool = False):
    if PROGRESS is not None:
        PROGRESS.step("Leyendo catálogos Parquet optimizados")
    log("Leyendo catálogos Parquet...")
    cta = obtener_cat_cuentas(forzar=forzar_catalogos, interactivo=interactivo)
    est = obtener_cat_estructura(forzar=forzar_catalogos, interactivo=interactivo)

    log(f"  estructura: {est.height:,} CECOs únicos ({est.width} columnas)")
    log(f"  cuentas: {cta.height:,} filas ({cta.width} columnas, split PosPre incluido)")

    con.register("cat_c", cta.to_arrow())
    con.register("cat_e", est.to_arrow())

    sel_cols = [f'COALESCE(c."{a}", \'No Clasificado\') AS "{a}"' for _, a in COLS_CUENTAS] + [f'COALESCE(e."{a}", \'No Clasificado\') AS "{a}"' for _, a in COLS_ESTRUCT]
    sel = ",\n            ".join(sel_cols)

    if PROGRESS is not None:
        PROGRESS.step("Uniendo 3 series en DuckDB")
    log("Uniendo 3 series...")
    # 2026-08-26: el Nuevo Plan (Serie 'NVO') NO se integra al consolidado.
    # 2026-09-30: ya tampoco se generan columnas Nvo_Plan_2026_* en los aggs ni existe la etapa 'movimiento'.
    # 2026-10-29: ya NO se materializan `du` ni `consolidado_raw` (17 GB en RAM que derramaban ~17 GB a _ddb_tmp y tardaban
    # 4 min 40 s): la unión de las 3 series, el join con los catálogos y el filtro de canales digitales corren en UNA sola
    # consulta que escribe directo el parquet (medido: 38.6 s, 0 GB de derrame, contenido idéntico fila por fila:
    # mismas columnas, mismo conteo y misma suma de hash de todas las columnas).
    from build_digitales import cecos_digitales
    _dig = cecos_digitales()
    con.register("ceco_dig", pl.DataFrame({"ID_CENTRO_COSTOS": _dig}).to_arrow())

    def _serie(ruta, s):
        return f"""SELECT CAST(ID_CENTRO_COSTOS AS VARCHAR) AS ID_CENTRO_COSTOS,
                 CAST(ID_CONCEPTO_CUENTA_NIV3 AS VARCHAR) AS ID_CONCEPTO_CUENTA_NIV3,
                 SEMANA_ARG, '{s}' AS Serie,
                 CAST("SUM_MONTO_MNX_CONS_(SUMA)" AS DOUBLE) AS monto_cons,
                 CAST(COALESCE(TRY_CAST("Monto_Local" AS DOUBLE), "SUM_MONTO_MNX_CONS_(SUMA)") AS DOUBLE) AS monto_loc,
                 TRY_CAST(MES AS INTEGER) AS MES FROM read_parquet('{ruta}')"""

    if PROGRESS is not None:
        PROGRESS.step("Construyendo y persistiendo _consolidado.parquet")
    log("Construyendo consolidado (unir + join + escribir, en streaming)...")
    # Canales físicos vs digitales: R25/P26 (y R26 cuando lo traiga) pueden incluir los CECOs digitales
    # (Agrupa Canales = Digitales); esos van solo a aggs_digitales/ (build_digitales.py), no a los aggs físicos.
    tmp_cons = CONS.with_name(CONS.stem + ".nuevo.parquet")
    tmp_cons.unlink(missing_ok=True)
    con.execute(f"""
        COPY (
            SELECT
                CAST(d.SEMANA_ARG AS INTEGER) AS sem,
                d.Serie,
                d.monto_cons,
                d.monto_loc,
                d.ID_CENTRO_COSTOS,
                d.ID_CONCEPTO_CUENTA_NIV3,
                COALESCE(e."ECO_PDV", 0) AS "ECO_PDV",
                d.MES AS "MES",
                {sel},
                CASE WHEN CAST(d.SEMANA_ARG AS INTEGER)<=13 THEN 'Q1'
                     WHEN CAST(d.SEMANA_ARG AS INTEGER)<=26 THEN 'Q2'
                     WHEN CAST(d.SEMANA_ARG AS INTEGER)<=39 THEN 'Q3'
                     ELSE 'Q4' END AS TRIMESTRE
            FROM ({_serie(R25, 'R25')} UNION ALL {_serie(P26, 'P26')} UNION ALL {_serie(R26, 'R26')}) d
            LEFT JOIN cat_c c ON d.ID_CONCEPTO_CUENTA_NIV3 = c.ID_CONCEPTO_CUENTA_NIV3
            LEFT JOIN cat_e e ON d.ID_CENTRO_COSTOS = e.ID_CENTRO_COSTOS
            WHERE TRIM(d.ID_CENTRO_COSTOS) NOT IN (SELECT ID_CENTRO_COSTOS FROM ceco_dig)
        ) TO '{tmp_cons.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{tmp_cons.as_posix()}')").fetchone()[0]
    n_origen = sum(con.execute(f"SELECT COUNT(*) FROM read_parquet('{r}')").fetchone()[0] for r in (R25, P26, R26))
    log(f"  canales digitales excluidos del consolidado físico: {n_origen - n:,} filas ({len(_dig)} CECOs)")
    log(f"  consolidado: {n:,} filas")
    # Auditoría de registros no clasificados
    n_noclasif = con.execute(f"SELECT COUNT(*) FROM read_parquet('{tmp_cons.as_posix()}') "
                             f"WHERE cat_Grupo_de_Cuentas = 'No Clasificado' OR cat_Cuentas = 'No Clasificado'").fetchone()[0]
    if n_noclasif > 0:
        log(f"  [AUDITORÍA BUILD DATA] Se detectaron {n_noclasif:,} registros sin categorización en el catálogo maestro. Asignados internamente como 'No Clasificado'.")
    # el reemplazo del caché es atómico y solo ocurre si la escritura terminó bien
    CONS.unlink(missing_ok=True)
    tmp_cons.replace(CONS)
    log(f"  {CONS.name}: {CONS.stat().st_size/1e6:.0f} MB")
    # igual que la ruta con caché: `consolidado_raw` es una vista sobre el parquet (sin la columna `monto`, que se reconstruye)
    con.execute(f"CREATE OR REPLACE VIEW consolidado_raw AS {_select_consolidado(con)}")


def construir_consolidado_series(con, ctx, forzar_catalogos: bool = False, interactivo: bool = False) -> list:
    """Modo por serie: igual que build_consolidado (mismos catálogos, mismo SQL, mismas columnas) pero cada
    serie va a su propio c_<S>.parquet y solo se reconstruyen las que cambiaron. Devuelve las series cambiadas."""
    if PROGRESS is not None:
        PROGRESS.step("Consolidado por serie (solo las series que cambiaron)")
    log("Leyendo catálogos Parquet...")
    cta = obtener_cat_cuentas(forzar=forzar_catalogos, interactivo=interactivo)
    est = obtener_cat_estructura(forzar=forzar_catalogos, interactivo=interactivo)
    log(f"  estructura: {est.height:,} CECOs únicos ({est.width} columnas)")
    log(f"  cuentas: {cta.height:,} filas ({cta.width} columnas, split PosPre incluido)")
    con.register("cat_c", cta.to_arrow())
    con.register("cat_e", est.to_arrow())
    sel_cols = [f'COALESCE(c."{a}", \'No Clasificado\') AS "{a}"' for _, a in COLS_CUENTAS] + [f'COALESCE(e."{a}", \'No Clasificado\') AS "{a}"' for _, a in COLS_ESTRUCT]
    sel = ",\n            ".join(sel_cols)
    # Canales físicos vs digitales: los CECOs digitales van solo a aggs_digitales/ (build_digitales.py).
    from build_digitales import cecos_digitales
    _dig = cecos_digitales()
    con.register("ceco_dig", pl.DataFrame({"ID_CENTRO_COSTOS": _dig}).to_arrow())
    cat_sig = ctx.firma_catalogo([catalogos.CAT_CUENTAS_PQ, catalogos.CAT_ESTRUCT_PQ], _dig)
    log(f"Consolidado por serie ({len(_dig)} CECOs digitales excluidos del físico)...")
    cambiadas = ctx.construir(con, {"R25": R25, "P26": P26, "R26": R26}, sel, cat_sig)
    gc.collect()
    return cambiadas


def detect_cortes(con) -> dict:
    """Semana de corte del Real 2026: última con volumen >= 50% del promedio de las 8 primeras."""
    stats = con.execute(
        "SELECT sem, COUNT(*) n FROM consolidado WHERE Serie='R26' AND sem IS NOT NULL GROUP BY 1 ORDER BY 1"
    ).fetchall()
    if not stats:
        raise RuntimeError("no hay filas Serie='R26'")
    base = sum(n for _, n in stats[:8]) / max(len(stats[:8]), 1)
    smr = 1
    for s, n in stats:
        if n >= 0.5 * base:
            smr = int(s)
        else:
            break
    # 2026-08-26: ya no incluye Serie='NVO' (Nuevo Plan) — dejó de integrarse
    # al consolidado (ver comentario en build_consolidado).
    smp = con.execute("SELECT MAX(sem) FROM consolidado WHERE Serie = 'P26'").fetchone()[0] or TOTAL_SEMANAS_ANIO
    return {"sem_min": 1, "sem_max_real": smr, "sem_max_plan": int(smp),
            "generado": datetime.now().isoformat(timespec="seconds")}


# ---------------------------------------------------------------- agregados
def ceco_nombres() -> "pl.DataFrame":
    """{ID CECO -> nombre} para etiquetar los CECOs sin PDC como '<id> · <nombre>'.
    Fuente principal: 'Descripción' del Catálogo de estructura (ahí viven los
    CECOs administrativos/staff). Complemento: hoja '4.Detalle PDC' del
    Conciliación PDC (Eco -> Nombre, por si aparece algún eco de PDV)."""
    nombres = {}
    try:
        est = obtener_cat_estructura()
        for r in est.select(["ID_CENTRO_COSTOS", "Descripción"]).filter(pl.col("Descripción") != "No Clasificado").drop_nulls().to_dicts():
            nombres.setdefault(r["ID_CENTRO_COSTOS"], str(r["Descripción"]).strip())
    except Exception as e:
        log(f"  AVISO: estructura sin Descripción de CECO ({e})")
    try:
        concil = obtener_ceco_nombres_concil()
        for eco, nombre in zip(concil["eco"].to_list(), concil["nombre"].to_list()):
            nombres.setdefault(eco, nombre)
    except Exception as e:
        log(f"  AVISO: sin hoja 4 del Conciliación PDC ({e})")
    log(f"  ceco_nombres: {len(nombres):,} cecos con nombre")
    return pl.DataFrame({"eco": list(nombres.keys()), "nombre": list(nombres.values())},
                        schema={"eco": pl.Utf8, "nombre": pl.Utf8})


# cat_Grupo_de_Cuentas va en TODOS los aggs (baja cardinalidad, 843 valores).
# Subtipo/Cta Mayor solo en los aggs chicos usados por Resumen/Trimestres +
# Cierres: en pdc.parquet (2.6M filas, 2,930 ctas mayor distintas) inflarían
# el tamaño varias veces -> ahí el filtro global no aplica (decisión del usuario).
FILTROS_LIGEROS = ["cat_Subtipo", "ID_CONCEPTO_CUENTA_NIV3", "cat_CtaMayor_Nombre"]
ALGGS_CON_FILTROS_LIGEROS = {"global", "division", "grupo_cuentas", "cuentas",
                             "trimestre", "conciliacion"}
# cat_Cuentas (alta cardinalidad) solo en los aggs de Puntos de Contacto: al
# meterla en conciliacion/cierres_det el archivo se disparaba de 41MB a 718MB
# y de 18MB a 122MB -> decisión del usuario: filtro global de Cuenta solo
# aplica en Resumen/Semanal/Trimestres/Cierres-cards (aggs chicos) + PDC.
ALGGS_CON_CUENTAS = {"pdc", "conciliacion"}


# columnas extra SOLO del agg 'cuentas': cat_PosPre/cat_Denom_PosPre, propagación
# análoga a cat_Grupo_de_Cuentas. Las 12 cuentas de CUENTAS_SPLIT_POSPRE llegan aquí
# ya duplicadas (fan-out 1:2 desde 'consolidado', monto completo en cada fila); al
# agrupar por PosPre aparecen con sus 2 combinaciones Grupo+PosPre, cada una con el
# monto real (no partido) — la UI debe aclarar que es el mismo movimiento repetido
# por falta de detalle de PosPre en el origen.
COLS_EXTRA_AGG = {
    "cuentas": ["cat_PosPre", "cat_Denom_PosPre"],
    # 2026-08-25: ECO_PDV (filtro "Solo PDCs oficiales") y cat_Prefijo
    # (filtro "Filtrar por Prefijo") en pdc.parquet -> el árbol "General" de
    # Análisis PDC pasó a leer TODOS sus niveles de este único archivo (antes
    # cada nivel leía un parquet distinto vía _FUENTE_ALT en app.py y los
    # filtros que no vivían en ESE parquet se ignoraban en silencio -> los
    # totales de arriba no cuadraban con el nivel PDC filtrado, reportado
    # por el usuario 2026-08-25).
    # cat_Agrupador_Reales: nivel nuevo del árbol "Por Cuenta" de Análisis PDC
    # (Grupo de Cuentas -> Cuenta -> Agrupador de Reales -> PDC, pedido del usuario 2026-08-25).
    "pdc": ["ECO_PDV", "cat_Prefijo", "cat_Agrupador_Reales"],
}


def build_aggs(con, meta, resume=False, solo_dims=None):
    """solo_dims: None = todas las dimensiones; set(...) = solo esas
    (2026-08-24, ver --solo: regenerar las 22 dimensiones cuesta la mayor
    parte del rebuild y casi siempre solo cambió una)."""
    if solo_dims:
        log(f"Generando agregados por dimensión (solo: {', '.join(sorted(solo_dims))})...")
    else:
        log("Generando agregados por dimensión...")
    for i, (alias, col) in enumerate(DIMS.items(), 1):
        if solo_dims is not None and alias not in solo_dims:
            continue
        if PROGRESS is not None:
            PROGRESS.step(f"Agg {i}/{len(DIMS)} ({alias})")
        fp = (OUT / f"{alias}.parquet").as_posix()
        if resume and Path(fp).exists():
            n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{fp}')").fetchone()[0]
            log(f"  {alias:18s} {n:>8,} filas (ya existe, saltando)")
            continue
        gcols = ([col] if col else []) + HIER.get(alias, [])
        if "cat_Grupo_de_Cuentas" not in gcols:
            gcols.append("cat_Grupo_de_Cuentas")
        if "cat_Agrupa1" not in gcols:
            gcols.append("cat_Agrupa1")
        if "MES" not in gcols:
            gcols.append("MES")
        if alias in ALGGS_CON_CUENTAS and "cat_Cuentas" not in gcols:
            gcols.append("cat_Cuentas")
        if alias in ALGGS_CON_FILTROS_LIGEROS:
            for fg in FILTROS_LIGEROS:
                if fg not in gcols:
                    gcols.append(fg)
        for extra in COLS_EXTRA_AGG.get(alias, []):
            if extra not in gcols:
                gcols.append(extra)
        gsql = "".join(f'"{c}", ' for c in gcols)
        if SERIES_CTX is not None:     # modo por serie: solo se recalculan las series cambiadas
            _t = time.time()
            n, rehechas = SERIES_CTX.agg_ancho(con, alias, gcols, OUT / f"{alias}.parquet")
            log(f"  {alias:18s} {n:>8,} filas · series recalculadas: {', '.join(rehechas) or 'ninguna (caché)'} ({time.time()-_t:,.1f} s)")
            continue
        sql = f"""
            SELECT {gsql} sem,
                COALESCE(SUM(monto_cons) FILTER (WHERE Serie='R25'), 0.0) AS Real_2025_cons,
                COALESCE(SUM(monto_cons) FILTER (WHERE Serie='P26'), 0.0) AS Plan_2026_cons,
                COALESCE(SUM(monto_cons) FILTER (WHERE Serie='R26'), 0.0) AS Real_2026_cons,
                COALESCE(SUM(monto_loc) FILTER (WHERE Serie='R25'), 0.0) AS Real_2025_loc,
                COALESCE(SUM(monto_loc) FILTER (WHERE Serie='P26'), 0.0) AS Plan_2026_loc,
                COALESCE(SUM(monto_loc) FILTER (WHERE Serie='R26'), 0.0) AS Real_2026_loc
            FROM consolidado GROUP BY {gsql} sem
        """
        tmp_fp = (OUT / f"{alias}.parquet.tmp").as_posix()
        final_p = OUT / f"{alias}.parquet"
        con.execute(f"COPY ({sql}) TO '{tmp_fp}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        # Reemplazo seguro en Windows con reintentos para evitar colisiones de antivirus/visor
        for attempt in range(5):
            try:
                if final_p.exists():
                    try:
                        final_p.unlink()
                    except OSError:
                        pass
                Path(tmp_fp).replace(final_p)
                break
            except OSError:
                time.sleep(0.5)
        else:
            Path(tmp_fp).replace(final_p)

        n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{final_p.as_posix()}')").fetchone()[0]
        log(f"  {alias:18s} {n:>8,} filas")


_SUMS = """
                COALESCE(SUM(monto_cons) FILTER (WHERE Serie='R25'), 0.0) AS Real_2025_cons,
                COALESCE(SUM(monto_cons) FILTER (WHERE Serie='P26'), 0.0) AS Plan_2026_cons,
                COALESCE(SUM(monto_cons) FILTER (WHERE Serie='R26'), 0.0) AS Real_2026_cons,
                COALESCE(SUM(monto_loc) FILTER (WHERE Serie='R25'), 0.0) AS Real_2025_loc,
                COALESCE(SUM(monto_loc) FILTER (WHERE Serie='P26'), 0.0) AS Plan_2026_loc,
                COALESCE(SUM(monto_loc) FILTER (WHERE Serie='R26'), 0.0) AS Real_2026_loc"""


def build_agrupadores_reales(con):
    """agrupadores_reales.parquet: árbol Agrupa1->Agrupa2->Agrupa3->Agrupador
    Reales de la pestaña Detalle Cuenta (2026-09-01, pedido del usuario).
    Mismo patrón que build_pdc_pospre/build_cierres_arbol: agrega consolidado
    por estas 4 columnas de catálogo, sin cruzar nada más."""
    log("Generando agrupadores_reales.parquet...")
    # 2026-10-21: + Grupo, Cuenta y Subtipo, para que los filtros globales (Responsable/Grupo/Cuenta/
    # Subtipo) y el buscador de cuenta apliquen en Detalle Cuenta. Medido sobre el consolidado:
    # 125,887 -> 228,431 filas (unos MB), sin chocar con la regla de aggs chicos.
    gcols = ["cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3", "cat_Agrupador_Reales",
             "cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Subtipo", "MES"]
    gsql = "".join(f'"{c}", ' for c in gcols)
    fp = (OUT / "agrupadores_reales.parquet").as_posix()
    if SERIES_CTX is not None:
        _t = time.time()
        n, rehechas = SERIES_CTX.agg_ancho(con, "agrupadores_reales", gcols, OUT / "agrupadores_reales.parquet")
        log(f"  agrupadores_reales {n:>8,} filas · series recalculadas: {', '.join(rehechas) or 'ninguna (caché)'} ({time.time()-_t:,.1f} s)")
        return
    con.execute(f"""
        COPY (
            SELECT {gsql} sem,
                {_SUMS}
            FROM consolidado GROUP BY {gsql} sem
        ) TO '{fp}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{fp}')").fetchone()[0]
    log(f"  agrupadores_reales {n:>8,} filas")


def build_contraloria(con):
    """contraloria.parquet: árbol Grupo de Cuentas -> Cuenta -> Contraloria de
    la pestaña Contraloria (2026-09-04, pedido del usuario). Mismo patrón que
    build_agrupadores_reales/build_agrupa_ceco: agg dedicado, chico, que
    arbol_perezoso lee en ambas direcciones (Cuenta->Contraloria y
    Contraloria->Cuenta) reordenando las mismas 3 columnas."""
    log("Generando contraloria.parquet...")
    # 2026-10-21: + cat_Subtipo (filtro global). Medido: 53,726 -> 143,062 filas.
    gcols = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Contraloria", "cat_Contraloria_6", "cat_Subtipo", "MES"]
    gsql = "".join(f'"{c}", ' for c in gcols)
    fp = (OUT / "contraloria.parquet").as_posix()
    if SERIES_CTX is not None:
        _t = time.time()
        n, rehechas = SERIES_CTX.agg_ancho(con, "contraloria", gcols, OUT / "contraloria.parquet")
        log(f"  contraloria        {n:>8,} filas · series recalculadas: {', '.join(rehechas) or 'ninguna (caché)'} ({time.time()-_t:,.1f} s)")
        return
    con.execute(f"""
        COPY (
            SELECT {gsql} sem,
                {_SUMS}
            FROM consolidado GROUP BY {gsql} sem
        ) TO '{fp}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{fp}')").fetchone()[0]
    log(f"  contraloria        {n:>8,} filas")


def build_cierres_arbol(con, resume=False):
    fp = (OUT / "cierres_arbol.parquet").as_posix()
    if resume and Path(fp).exists():
        n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{fp}')").fetchone()[0]
        log(f"  cierres_arbol      {n:>8,} filas (ya existe, saltando)")
        return
    log("Generando cierres_arbol.parquet...")
    gcols = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "cat_PDC", "MES"]
    gsql = "".join(f'"{c}", ' for c in gcols)
    con.execute(f"""
        COPY (
            SELECT {gsql} sem,
                {_SUMS}
            FROM consolidado GROUP BY {gsql} sem
        ) TO '{fp}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{fp}')").fetchone()[0]
    log(f"  cierres_arbol      {n:>8,} filas")


def build_orden_gpo():
    """Mapa Grupo de Cuentas -> 'Orden presentación Gpo. Cta' del catálogo."""
    log("Generando orden_gpo.json...")
    try:
        cta = obtener_cat_cuentas()
    except Exception as e:
        log(f"  AVISO: no pude leer catálogo de cuentas: {e}")
        return
    m = (cta.select(["cat_Grupo_de_Cuentas", "Orden_Presentacion_Gpo"])
            .filter(pl.col("Orden_Presentacion_Gpo") != "No Clasificado")
            .with_columns(pl.col("Orden_Presentacion_Gpo").cast(pl.Int64, strict=False))
            .drop_nulls()
            .group_by("cat_Grupo_de_Cuentas").agg(pl.col("Orden_Presentacion_Gpo").min()))
    orden = {r["cat_Grupo_de_Cuentas"]: r["Orden_Presentacion_Gpo"] for r in m.to_dicts()}
    (OUT / "orden_gpo.json").write_text(json.dumps(orden, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  orden_gpo.json: {len(orden)} grupos")


def build_responsables_gpo():
    """Genera Responsable Interno -> grupos y cuentas desde catálogo optimizado."""
    log("Generando responsables_gpo.json...")
    try:
        cta = obtener_cat_cuentas()
    except Exception as e:
        log(f"  AVISO: no pude leer catálogo de cuentas: {e}")
        return

    pares = (
        cta.select(["Responsable_Interno", "cat_Grupo_de_Cuentas"])
        .filter(
            (pl.col("Responsable_Interno") != "No Clasificado")
            & (pl.col("cat_Grupo_de_Cuentas") != "No Clasificado")
            & (pl.col("Responsable_Interno").str.to_lowercase() != "revisar")
        )
        .unique()
    )

    responsables = {}
    for fila in pares.to_dicts():
        responsables.setdefault(fila["Responsable_Interno"], set()).add(fila["cat_Grupo_de_Cuentas"])

    salida = {k: sorted(v) for k, v in sorted(responsables.items())}
    (OUT / "responsables_gpo.json").write_text(
        json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    pares_cta = (
        cta.select(["Responsable_Interno", "cat_Cuentas"])
        .filter(
            (pl.col("Responsable_Interno") != "No Clasificado")
            & (pl.col("cat_Cuentas") != "No Clasificado")
            & (pl.col("Responsable_Interno").str.to_lowercase() != "revisar")
        )
        .unique()
    )
    resp_ctas = {}
    for fila in pares_cta.to_dicts():
        resp_ctas.setdefault(fila["Responsable_Interno"], set()).add(fila["cat_Cuentas"])
    salida_cta = {k: sorted(v) for k, v in sorted(resp_ctas.items())}
    (OUT / "responsables_cta.json").write_text(
        json.dumps(salida_cta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"  responsables_gpo.json: {len(salida):,} responsables · {len(pares):,} relaciones GPO")
    log(f"  responsables_cta.json: {len(salida_cta):,} responsables · {len(pares_cta):,} relaciones Cta")


def build_pdc_ids():
    """Mapa nombre de PDC -> Eco PDV (id) desde catálogo de estructura."""
    log("Generando pdc_ids.json...")
    est = obtener_cat_estructura()
    m = (est.select([pl.col("cat_PDC").alias("nombre"), pl.col("ECO_PDV").alias("eco")])
            .filter(
                pl.col("nombre").is_not_null()
                & (pl.col("nombre") != "Sin Agrupar")
                & (pl.col("nombre") != "No Clasificado")
                & (pl.col("nombre") != "0")
                & (pl.col("eco") != 0)
            )
            .group_by("nombre").agg(pl.col("eco").min()))
    ids = {r["nombre"]: r["eco"] for r in m.to_dicts()}

    (OUT / "pdc_ids.json").write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"  pdc_ids.json: {len(ids):,} PDCs con id (fuente: Catálogo optimizado)")


def build_pdc_cecos(con):
    """Mapa cat_PDC -> lista de ID_CENTRO_COSTOS (CECO)."""
    log("Generando pdc_cecos.json...")
    est = obtener_cat_estructura()
    mapa: dict = {}
    for pdc, ceco in zip(est["cat_PDC"].to_list(), est["ID_CENTRO_COSTOS"].to_list()):
        mapa.setdefault(pdc, []).append(ceco)
    (OUT / "pdc_cecos.json").write_text(json.dumps(mapa, ensure_ascii=False), encoding="utf-8")
    log(f"  pdc_cecos.json: {len(mapa):,} PDC -> {sum(len(v) for v in mapa.values()):,} CECOs")


def build_pdv_agrupacion():
    """Mapa Eco PDV -> nombres y CECOs."""
    log("Generando pdv_nombres.json y pdv_cecos.json...")
    est = obtener_cat_estructura().filter(pl.col("ECO_PDV") != 0)

    cecos_map: dict = {}
    nombres_map: dict = {}
    for eco, ceco, nombre in zip(est["ECO_PDV"].to_list(), est["ID_CENTRO_COSTOS"].to_list(), est["cat_PDC"].to_list()):
        k = str(eco)
        cecos_map.setdefault(k, []).append(ceco)
        if nombre and nombre not in ("No Clasificado", "Sin Agrupar", "0"):
            nombres_map.setdefault(k, set()).add(nombre)
    nombres_out = {k: ", ".join(sorted(v)) for k, v in nombres_map.items()}

    (OUT / "pdv_nombres.json").write_text(json.dumps(nombres_out, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "pdv_cecos.json").write_text(json.dumps(cecos_map, ensure_ascii=False), encoding="utf-8")
    log(f"  pdv_nombres.json: {len(nombres_out):,} Eco PDV con nombre")
    log(f"  pdv_cecos.json: {len(cecos_map):,} Eco PDV -> {sum(len(v) for v in cecos_map.values()):,} CECOs (excluye Eco PDV=0)")


def build_cierres_expansion(con):
    """2026-07-24: pestaña Cierres REEMPLAZA cat_Agrupa3 por las categorías de
    'Proyecto' del Master Cierres de Expansión (Mismos/Nuevos/Baja/
    Transformaciones/Transformaciones Matriz/Aperturas Modulo de
    Préstamos/Aperturas) — decisión del usuario: reemplazo total, ya no
    Operativo Normal/Sin Movimiento/Otros/Cerrados y Cancelados. Todo depende
    de este Excel — pedido explícito del usuario, SIN cruzar contra
    pdc_ids.json (el intento inicial via 'Eco' solo matcheaba 45% de los PDC,
    'Mismos'/'Nuevos' casi no cruzaban por ser aperturas recientes aún no
    catalogadas). Se usa directo la columna 'PDC' (nombre) del propio Excel,
    cruzada por NOMBRE contra consolidado.cat_PDC.

    El Excel (CAT_CIERRES_EXP, hoja 'Seguimiento semanal') solo trae PDC
    (nombre, col 'PDC') + categoría (col 'Proyecto'), SIN monto — el gasto se
    cruza con lo que el dashboard ya calcula por PDC (consolidado).
    Salida: aggs/cierres_expansion.parquet — mismo shape que cierres_det.parquet
    (Categoria en vez de cat_Agrupa3) para poder reusar arbol_multicol()."""
    log("Generando cierres_expansion.parquet...")
    if not CAT_CIERRES_EXP.exists():
        log(f"  AVISO: no existe {CAT_CIERRES_EXP.name} — se omite cierres_expansion.parquet")
        return

    import openpyxl
    wb = openpyxl.load_workbook(CAT_CIERRES_EXP, read_only=True, data_only=True)
    ws = wb["Seguimiento semanal"]
    # fila 21 = encabezados (Proyecto/Especialidad/.../Eco/PDC/...), datos desde la 22.
    rows = list(ws.iter_rows(min_row=22, values_only=True))
    wb.close()

    # Cargar y revertir pdc_ids.json para cruzar por Eco ID
    ids_path = OUT / "pdc_ids.json"
    _ID_A_PDC = {}
    if ids_path.exists():
        try:
            pdc_ids_dict = json.loads(ids_path.read_text(encoding="utf-8"))
            for name, pid in pdc_ids_dict.items():
                try:
                    _ID_A_PDC[int(float(pid))] = name
                except (ValueError, TypeError) as e:
                    log(f"  AVISO: no pude convertir PDC ID '{pid}': {e}")
                    continue
        except Exception as e:
            log(f"  AVISO: no pude cargar pdc_ids.json para cruzar cierres: {e}")

    def eco_a_int(v):
        try:
            return int(float(v))
        except (ValueError, TypeError) as e:
            log(f"  AVISO: no pude convertir eco '{v}' a int: {e}")
            return None

    proyectos, pdcs = [], []
    for r in rows:
        proyecto = r[1]
        if proyecto is None or str(proyecto).strip() == "Proyecto":
            continue
        
        eco_val = r[5]
        pdc_nombre = r[6]
        eco_id = eco_a_int(eco_val)
        
        # Intentar cruzar por Eco ID primero
        pdc_mapped = _ID_A_PDC.get(eco_id) if eco_id is not None else None
        if not pdc_mapped:
            pdc_mapped = str(pdc_nombre).strip() if pdc_nombre is not None else ""
            
        if not pdc_mapped:
            continue
            
        proyectos.append(str(proyecto).strip())
        pdcs.append(pdc_mapped)

    exp = pl.DataFrame({"Categoria": proyectos, "cat_PDC": pdcs})
    log(f"  Seguimiento Expansión: {exp.height:,} PDCs (mapeados por Eco ID / PDC del Excel)")

    con.register("cierres_exp_map", exp.to_arrow())
    fp = (OUT / "cierres_expansion.parquet").as_posix()
    # LEFT JOIN desde el Excel (fuente maestra, "todo depende de este Excel" —
    # pedido explícito): un PDC del plan de expansión que aún no tiene
    # movimiento en el sistema transaccional (apertura muy reciente) igual
    # aparece, con Real/Plan/Nvo Plan en 0 en vez de desaparecer del listado.
    con.execute(f"""
        COPY (
            SELECT m.Categoria, m.cat_PDC,
                   COALESCE(c.cat_Grupo_de_Cuentas, '(Sin dato)') AS cat_Grupo_de_Cuentas,
                   COALESCE(c.cat_Direccion_Division, '(Sin dato)') AS cat_Direccion_Division,
                   COALESCE(c.cat_Subdireccion_Territorio, '(Sin dato)') AS cat_Subdireccion_Territorio,
                   COALESCE(c.cat_Subdireccion_Region, '(Sin dato)') AS cat_Subdireccion_Region,
                   c.cat_Subtipo, c.ID_CONCEPTO_CUENTA_NIV3, c.cat_CtaMayor_Nombre,
                   COALESCE(c.sem, 1) AS sem, c.MES AS MES,
                   COALESCE(SUM(c.monto_cons) FILTER (WHERE c.Serie='R25'), 0.0) AS Real_2025_cons,
                   COALESCE(SUM(c.monto_cons) FILTER (WHERE c.Serie='P26'), 0.0) AS Plan_2026_cons,
                   COALESCE(SUM(c.monto_cons) FILTER (WHERE c.Serie='R26'), 0.0) AS Real_2026_cons,
                   COALESCE(SUM(c.monto_loc) FILTER (WHERE c.Serie='R25'), 0.0) AS Real_2025_loc,
                   COALESCE(SUM(c.monto_loc) FILTER (WHERE c.Serie='P26'), 0.0) AS Plan_2026_loc,
                   COALESCE(SUM(c.monto_loc) FILTER (WHERE c.Serie='R26'), 0.0) AS Real_2026_loc
            FROM cierres_exp_map m
            LEFT JOIN consolidado c ON c.cat_PDC = m.cat_PDC
            GROUP BY 1,2,3,4,5,6,7,8,9,10,11
        ) TO '{fp}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{fp}')").fetchone()[0]
    n_pdc = con.execute(f"SELECT COUNT(DISTINCT cat_PDC) FROM read_parquet('{fp}')").fetchone()[0]
    n_con_gasto = con.execute(
        f"SELECT COUNT(DISTINCT cat_PDC) FROM read_parquet('{fp}') WHERE Real_2026_cons <> 0 OR Plan_2026_cons <> 0"
    ).fetchone()[0]
    log(f"  cierres_expansion  {n:>8,} filas · {n_pdc:,} PDCs del Excel · "
        f"{n_con_gasto:,} con gasto cruzado, {n_pdc - n_con_gasto:,} en $0 (sin match en consolidado)")



def build_forecast_cuenta():
    """2026-07-24: nueva fuente de Forecast por Cta Mayor (FCST VTA RAPIDA.xlsx),
    reemplaza la fórmula "Real + Plan restante" SOLO en vistas que agrupan por
    cuenta (Resumen, Div/Terr contable, Detalle Cuenta) — ver nota junto a
    CAT_FORECAST_CUENTA. NO toca forecast_ceco.parquet (pestaña Plan + Real).

    El Excel viene ANCHO: CECO_N7 | TipoOrigen | CuentaMayor | semanas 1-53.
    Pedido explícito del usuario: ignorar CECO_N7 y TipoOrigen, agrupar y sumar
    todo por CuentaMayor (ya no se ve por Centro de Costos). Encabezados reales
    en la fila 2 (fila 1 solo trae 'Semana' en una celda suelta).
    Salida: aggs/forecast_cuenta.parquet, columnas
    ID_CONCEPTO_CUENTA_NIV3 (Cta Mayor) x sem x Forecast — SIN Grupo de
    Cuentas/Cuenta (el cruce con el catálogo vive en app.py, igual que
    Real/Plan/Nvo Plan, para no duplicar la lógica de homologación de
    mayúsculas que ya aplica load_all() a todos los aggs)."""
    if not CAT_FORECAST_CUENTA.exists():
        log(f"  AVISO: no existe {CAT_FORECAST_CUENTA.name} — se omite forecast_cuenta.parquet")
        return
    log("Generando forecast_cuenta.parquet...")
    # 2026-10-20 (pedido del usuario): Forecast Finanzas = Excel "Forecast 2026" copiado
    # IDENTICO a datos/ (hoja 'Sheet 1', encabezado en la fila 3 con 'Cta de Mayor' y
    # semanas 1-53). Cruce SOLO por Cta de Mayor, sin reclasificar nada. Trae fisico
    # (Red de Grupo Aurora) y negocios digitales: el fisico va a forecast_cuenta.parquet
    # (Forecast Finanzas del tablero) y los digitales a forecast_cuenta_dig.parquet.
    import openpyxl
    wb = openpyxl.load_workbook(CAT_FORECAST_CUENTA, read_only=True, data_only=True)
    filas = list(wb["Sheet 1"].iter_rows(values_only=True))
    wb.close()
    ih = next((i for i, f in enumerate(filas[:10])
               if any("mayor" in str(c).lower() for c in f if c is not None)), None)
    if ih is None:
        raise RuntimeError(f"{CAT_FORECAST_CUENTA.name}: no se encontro el encabezado con Cta de Mayor")
    enc = [str(c).strip() if c is not None else "" for c in filas[ih]]
    i_cta = next(i for i, c in enumerate(enc) if "mayor" in c.lower())
    i_neg = max(i for i, c in enumerate(enc) if c.upper().startswith("CECO_N7"))
    i_sem = {int(c): i for i, c in enumerate(enc) if c.isdigit()}
    if not i_sem:
        raise RuntimeError(f"{CAT_FORECAST_CUENTA.name}: no se encontraron columnas de semana (1-53)")
    _num = lambda v: float(v) if isinstance(v, (int, float)) else 0.0
    _es_dig = lambda n: any(t in str(n).upper() for t in ("DIGITAL", "SHOPINBAZ"))
    acum = {False: {}, True: {}}          # es_digital -> {(cuenta, sem): monto}
    for f in filas[ih + 1:]:
        if f[i_cta] is None or f[i_neg] is None:
            continue
        cta_id = str(int(f[i_cta])) if isinstance(f[i_cta], (int, float)) else str(f[i_cta]).strip()
        d = acum[_es_dig(f[i_neg])]
        for s, i in i_sem.items():
            d[(cta_id, s)] = d.get((cta_id, s), 0.0) + _num(f[i])
    def _a_df(d):
        return (pl.DataFrame({"ID_CONCEPTO_CUENTA_NIV3": [k[0] for k in d], "sem": [k[1] for k in d],
                              "Forecast": list(d.values())},
                             schema={"ID_CONCEPTO_CUENTA_NIV3": pl.Utf8, "sem": pl.Int64, "Forecast": pl.Float64})
                .sort(["ID_CONCEPTO_CUENTA_NIV3", "sem"]))
    out = _a_df(acum[False])
    out_dig = _a_df(acum[True])
    # 2026-11-10 (igual que Spend Legacy, 06-nov): Forecast Finanzas fisico/digital desde los parquet
    # v04.11.26 (totales por CECO x semana repartidos por cuenta NIV3). NO afecta al FCST PLAN. Para volver
    # al Excel viejo: borrar/renombrar esos parquet.
    def _fin_pq(nombre):
        return (pl.read_parquet((DATOS / nombre).as_posix(), columns=['ID_CONCEPTO_CUENTA_NIV3', 'SEMANA_ARG', 'SUM_MONTO_MNX_CONS_(SUMA)'])
                .group_by(['ID_CONCEPTO_CUENTA_NIV3', 'SEMANA_ARG']).agg(pl.col('SUM_MONTO_MNX_CONS_(SUMA)').cast(pl.Float64).sum().alias('Forecast'))
                .rename({'SEMANA_ARG': 'sem'}).with_columns(pl.col('ID_CONCEPTO_CUENTA_NIV3').cast(pl.Utf8), pl.col('sem').cast(pl.Int64))
                .select(['ID_CONCEPTO_CUENTA_NIV3', 'sem', 'Forecast']).sort(['ID_CONCEPTO_CUENTA_NIV3', 'sem']))
    if (DATOS / "fcst_finanzas_v041126.parquet").exists():
        out = _fin_pq("fcst_finanzas_v041126.parquet")
        log("  Forecast Finanzas desde fcst_finanzas_v041126.parquet")
    if (DATOS / "fcst_finanzas_digital_v041126.parquet").exists():
        out_dig = _fin_pq("fcst_finanzas_digital_v041126.parquet")
        log("  Forecast Finanzas digitales desde fcst_finanzas_digital_v041126.parquet")
    log(f"  Forecast Finanzas: fisico {out['Forecast'].sum() / 1e6:,.1f} M · digitales {out_dig['Forecast'].sum() / 1e6:,.1f} M (forecast_cuenta_dig.parquet)")
    # 2026-09-21: cuentas con Forecast pero SIN Real/Plan (no salen en los aggs)
    # se perdían en el cruce de app.py. Se anexan sus columnas del catálogo para
    # que app.py las ubique en su grupo/cuenta (ver _forecast_huerfanas).
    try:
        cta = obtener_cat_cuentas()
        _cc = [c for c in ("cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales",
                           "cat_CtaMayor_Nombre") if c in cta.columns]
        cat = (cta.select(["ID_CONCEPTO_CUENTA_NIV3"] + _cc)
                  .with_columns(pl.col("ID_CONCEPTO_CUENTA_NIV3").cast(pl.Utf8).str.strip_chars())
                  .unique(subset=["ID_CONCEPTO_CUENTA_NIV3"], keep="first", maintain_order=True))
        out = out.join(cat, on="ID_CONCEPTO_CUENTA_NIV3", how="left")
        out_dig = out_dig.join(cat, on="ID_CONCEPTO_CUENTA_NIV3", how="left")
    except Exception as e:
        log(f"  AVISO: forecast_cuenta sin columnas de catálogo: {e}")
    out_dig.write_parquet(OUT / "forecast_cuenta_dig.parquet", compression="zstd")   # pestaña Canales Digitales
    (OUT.parent / "aggs_digitales").mkdir(exist_ok=True)   # el Dash lee el forecast digital de aggs_digitales/
    out_dig.write_parquet(OUT.parent / "aggs_digitales" / "forecast_cuenta.parquet", compression="zstd")
    fp = OUT / "forecast_cuenta.parquet"
    out.write_parquet(fp, compression="zstd")
    n_cta = out.select(pl.col("ID_CONCEPTO_CUENTA_NIV3").n_unique()).item()
    log(f"  forecast_cuenta    {out.height:>8,} filas · {n_cta:,} cuentas mayor")


RIESGOS_DOCX = ARCHIVOS / "Riesgos y Oportunidades.docx"

def build_riesgos():
    """Lee 'Riesgos y Oportunidades.docx' (en la carpeta del dashboard, lo
    edita Planeación en Word) y lo vuelca a aggs/riesgos.json. Formato del
    Word: 4 párrafos título abren cada sección — 'RIESGOS' / 'OPORTUNIDADES'
    (bloques YTD, los que muestra la pestaña Riesgos & Oport. del dashboard) y
    'RIESGOS SEMANAL' / 'OPORTUNIDADES SEMANAL' (bloques semanales, los usa
    generar_correo_canales.py) — cada párrafo debajo de un título es una
    viñeta. Las 2 secciones semanales son opcionales (si no están en el Word,
    quedan como listas vacías). Sin dependencias: un .docx es un ZIP con
    word/document.xml (se parsea con la librería estándar)."""
    log("Generando riesgos.json...")
    if not RIESGOS_DOCX.exists():
        log(f"  AVISO: no existe {RIESGOS_DOCX.name} — corre _crear_word_riesgos.py")
        return
    import re
    import zipfile
    try:
        with zipfile.ZipFile(RIESGOS_DOCX) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception as e:
        log(f"  AVISO: no pude leer {RIESGOS_DOCX.name}: {e}")
        return
    # párrafos = <w:p>...</w:p>; texto = concatenación de sus <w:t>
    parrafos = []
    for p in re.findall(r"<w:p[ >].*?</w:p>|<w:p/>", xml, flags=re.S):
        t = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S))
        t = (t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
              .replace("&quot;", '"').replace("&#39;", "'").strip())
        if t:
            parrafos.append(t)
    secciones = {"RIESGOS": "riesgos", "OPORTUNIDADES": "oportunidades",
                 "RIESGOS SEMANAL": "riesgos_semanal", "OPORTUNIDADES SEMANAL": "oportunidades_semanal"}
    out = {"riesgos": [], "oportunidades": [], "riesgos_semanal": [], "oportunidades_semanal": [],
           "actualizado": datetime.fromtimestamp(RIESGOS_DOCX.stat().st_mtime)
                          .strftime("%Y-%m-%d %H:%M")}
    seccion = None
    for t in parrafos:
        clave = t.strip().upper().rstrip(":")
        if clave in secciones:
            seccion = secciones[clave]
        elif seccion:
            out[seccion].append(t)
    (OUT / "riesgos.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    log(f"  riesgos.json: {len(out['riesgos'])} riesgos · {len(out['oportunidades'])} oportunidades"
        f" · {len(out['riesgos_semanal'])} riesgos sem · {len(out['oportunidades_semanal'])} oport. sem")


# ---------------------------------------------------------------- validación
def validar(con, meta):
    log("Validando...")
    smr = meta["sem_max_real"]
    ok = True

    g = pl.read_parquet(OUT / "global.parquet")
    _med_val = MEDIDAS_CONS if "Real_2025_cons" in g.columns else MEDIDAS
    tot = {m: g.filter(pl.col("sem") <= smr).select(pl.col(m).sum()).item() for m in _med_val}
    log(f"  Acumulado 1..{smr}:")
    for m, v in tot.items():
        log(f"    {m:20s} {v/1e6:>14,.1f} M")

    # "trimestre" queda fuera: no está en config.yaml/DIMS (no lo genera build_aggs)
    # ni lo usa app.py (la pestaña Trimestres calcula sus totales al vuelo desde
    # otros aggs) -> el archivo trimestre.parquet es un artefacto huérfano de antes
    # de la reorganización 2026-08-14 y compararlo aquí solo produce falsos FALLA.
    for alias in ["division", "grupo_cuentas"]:
        d = pl.read_parquet(OUT / f"{alias}.parquet").filter(pl.col("sem") <= smr)
        _med_d = MEDIDAS_CONS if "Real_2025_cons" in d.columns else MEDIDAS
        for m in _med_d:
            a, b = tot.get(m, tot.get(m.replace("_cons", ""), 0)), d.select(pl.col(m).sum()).item()
            if abs(a - b) > max(1.0, abs(a) * 1e-9):
                log(f"    [FALLA] {alias}.{m}: {b/1e6:,.1f}M != global {a/1e6:,.1f}M")
                ok = False
    if ok:
        log("  [OK] todas las dimensiones suman igual que global")

    _k_r26 = "Real_2026_cons" if "Real_2026_cons" in tot else "Real_2026"
    _k_p26 = "Plan_2026_cons" if "Plan_2026_cons" in tot else "Plan_2026"
    if tot[_k_r26] <= 0 or tot[_k_p26] <= 0:
        log("  [FALLA] Real_2026 o Plan_2026 no positivo (revisar signos)")
        ok = False

    _col_r26 = "Real_2026_cons" if "Real_2026_cons" in g.columns else "Real_2026"
    r26 = g.filter(pl.col("sem") > smr).select(pl.col(_col_r26).sum()).item()
    if abs(r26) > 1:
        log(f"  [AVISO] hay Real_2026 = {r26/1e6:,.1f}M despues del corte sem {smr}")
    return ok


# Rollups de dashboard.duckdb: mismas combinaciones que DuckDBService._ROLLUPS (duckdb_service.py).
# Si cambias una, cambia la otra.
ROLLUPS_DB = {
    "rollup_pdv_general": ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                           "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ECO_PDV", "MES"],
    "rollup_pdv_general_gpo": ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                               "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ECO_PDV",
                               "cat_Grupo_de_Cuentas", "MES"],
    "rollup_pdv_general_prefijo": ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                                   "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ECO_PDV",
                                   "cat_Prefijo", "MES"],
    "rollup_pdv_porcuenta": ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "ECO_PDV", "MES"],
    "rollup_ceco_general": ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                            "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ID_CENTRO_COSTOS", "MES"],
    "rollup_ceco_general_gpo": ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio",
                                "cat_Subdireccion_Zona", "cat_Subdireccion_Region", "ID_CENTRO_COSTOS",
                                "cat_Grupo_de_Cuentas", "MES"],
    "rollup_mes_contraloria": ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Contraloria", "cat_Contraloria_6",
                               "cat_Subtipo", "MES"],
}
# Aggs de más de este tamaño NO se copian a dashboard.duckdb (los grandes los cubre `consolidado` + rollups).
DB_MAX_AGG_MB = 400    # cierres_arbol pesa ~250 MB con el catálogo nuevo (PDC real); la pestaña Cierres lo necesita

# Rollups derivados (2026-10-29, medido en sandbox con las 86.5 M filas de la semana 44: 5.6 min contra 18.4 min del SQL
# directo, mismas filas, mismas llaves y diferencia máxima por celda de 1.1e-7 en los 7). Idea: agregación temprana
# (Yan y Larson, VLDB 1995) + dependencias funcionales (Simmen et al., SIGMOD 1996) + retícula de vistas (Harinarayan,
# Rajaraman y Ullman, SIGMOD 1996). Cada CECO determina su jerarquía, ECO_PDV y Prefijo; cada Cta Mayor su Grupo, Cuenta y
# Agrupador. Se agrega UNA vez por CECO x Grupo x MES x sem x Serie (llave de 5 columnas, ~21 M filas) y los rollups se
# derivan uniendo las dimensiones (20 k y 218 filas). Si el catálogo rompiera una dependencia (un CECO con dos jerarquías),
# se detecta aquí y los rollups se calculan con el SQL directo de siempre.
_H5 = ["cat_Agrupa1", "cat_Direccion_Division", "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona", "cat_Subdireccion_Region"]
_DIM_CECO = ["ECO_PDV", "cat_Prefijo"] + _H5
_DIM_CTA = ["cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales"]


def _preparar_rollups_derivados(con):
    """Crea en memoria el agregado base y las dos dimensiones y devuelve {rollup: SELECT} listos para `CREATE TABLE out.x AS`.
    Devuelve None (y no deja nada creado) si alguna dependencia funcional no se cumple en los datos de esta corrida."""
    t0 = time.time()
    src = f"({_sql_consolidado_final()})"

    def dimension(nombre, llave, cols):
        mn = ", ".join(f'MIN("{c}") AS "{c}"' for c in cols)
        cf = " OR ".join(f'MAX("{c}") <> MIN("{c}")' for c in cols)
        con.execute(f"CREATE OR REPLACE TABLE {nombre} AS SELECT {llave}, {mn}, ({cf}) AS _conflicto FROM {src} GROUP BY {llave}")
        n_conf = con.execute(f"SELECT COUNT(*) FROM {nombre} WHERE _conflicto").fetchone()[0]
        n = con.execute(f"SELECT COUNT(*) FROM {nombre}").fetchone()[0]
        return n, n_conf

    n_ce, c_ce = dimension("h2_dim_ceco", "ID_CENTRO_COSTOS", _DIM_CECO)
    n_cu, c_cu = dimension("h2_dim_cuenta", "ID_CONCEPTO_CUENTA_NIV3", _DIM_CTA)
    log(f"  rollups derivados: dim_ceco {n_ce:,} CECOs ({c_ce} con jerarquía distinta) · dim_cuenta {n_cu:,} cuentas ({c_cu} con grupo distinto)")
    if c_ce or c_cu:
        log("  [AVISO] el catálogo rompe una dependencia funcional (un CECO o cuenta con dos valores): los rollups se calculan con el SQL directo (más lento)")
        for t in ("h2_dim_ceco", "h2_dim_cuenta"):
            con.execute(f"DROP TABLE IF EXISTS {t}")
        return None

    con.execute(f"""CREATE OR REPLACE TABLE h2_base AS
        SELECT ID_CENTRO_COSTOS, cat_Grupo_de_Cuentas, MES, sem, Serie, SUM(monto_cons) AS monto_cons, SUM(monto_loc) AS monto_loc
        FROM {src} GROUP BY ALL""")
    con.execute("""CREATE OR REPLACE TABLE h2_base_ceco AS
        SELECT ID_CENTRO_COSTOS, MES, sem, Serie, SUM(monto_cons) AS monto_cons, SUM(monto_loc) AS monto_loc
        FROM h2_base GROUP BY ALL""")
    nb = con.execute("SELECT COUNT(*) FROM h2_base").fetchone()[0]
    log(f"  rollups derivados: agregado base {nb:,} filas en {time.time()-t0:,.1f} s (incluye las dos dimensiones)")

    h5 = ", ".join(f"d.{c}" for c in _H5)
    suma = "SUM(b.monto_cons) AS monto_cons, SUM(b.monto_loc) AS monto_loc"
    unir = "JOIN h2_dim_ceco d USING (ID_CENTRO_COSTOS)"
    return {
        "rollup_pdv_general": f"SELECT {h5}, d.ECO_PDV, b.MES, b.sem, b.Serie, {suma} FROM h2_base_ceco b {unir} GROUP BY ALL",
        "rollup_pdv_general_gpo": f"SELECT {h5}, d.ECO_PDV, b.cat_Grupo_de_Cuentas, b.MES, b.sem, b.Serie, {suma} FROM h2_base b {unir} GROUP BY ALL",
        "rollup_pdv_general_prefijo": f"SELECT {h5}, d.ECO_PDV, d.cat_Prefijo, b.MES, b.sem, b.Serie, {suma} FROM h2_base_ceco b {unir} GROUP BY ALL",
        "rollup_pdv_porcuenta": f"""SELECT c.cat_Grupo_de_Cuentas, c.cat_Cuentas, c.cat_Agrupador_Reales, n.ECO_PDV, n.MES, n.sem, n.Serie,
            SUM(n.monto_cons) AS monto_cons, SUM(n.monto_loc) AS monto_loc
            FROM (SELECT ID_CONCEPTO_CUENTA_NIV3, ECO_PDV, MES, sem, Serie, SUM(monto_cons) AS monto_cons, SUM(monto_loc) AS monto_loc
                  FROM {src} GROUP BY ALL) n
            JOIN h2_dim_cuenta c USING (ID_CONCEPTO_CUENTA_NIV3) GROUP BY ALL""",
        "rollup_ceco_general": f"SELECT {h5}, b.ID_CENTRO_COSTOS, b.MES, b.sem, b.Serie, b.monto_cons, b.monto_loc FROM h2_base_ceco b {unir}",
        "rollup_ceco_general_gpo": f"SELECT {h5}, b.ID_CENTRO_COSTOS, b.cat_Grupo_de_Cuentas, b.MES, b.sem, b.Serie, b.monto_cons, b.monto_loc FROM h2_base b {unir}",
        "rollup_mes_contraloria": f"""SELECT cat_Grupo_de_Cuentas, cat_Cuentas, cat_Contraloria, cat_Contraloria_6, cat_Subtipo, MES, sem, Serie,
            SUM(monto_cons) AS monto_cons, SUM(monto_loc) AS monto_loc FROM {src} GROUP BY ALL""",
    }


def _escribir_consolidado_por_serie(nuevo: Path) -> bool:
    """Escribe la tabla `consolidado` de `nuevo` desde _consolidado.parquet, UNA serie por proceso nuevo (_escribe_consolidado.py).
    2026-10-29: un solo CREATE TABLE de 86 M filas tarda 250 a 434 s (la escritura de las 19 columnas de texto se degrada con
    el volumen escrito por proceso, no por hilos ni memoria); por serie en procesos nuevos 126 s (28 + 30 + 52 s), mismas filas.
    Devuelve False si algo falla; el llamador cae entonces al CREATE TABLE en el proceso."""
    try:
        series = [r[0] for r in duckdb.sql(f"SELECT DISTINCT Serie FROM read_parquet('{CONS.as_posix()}') ORDER BY 1").fetchall()]
        script = Path(__file__).with_name("_escribe_consolidado.py")
        tmp = Path("_ddb_tmp")
        tmp.mkdir(exist_ok=True)
        for i, serie in enumerate(series):
            _t = time.time()
            r = subprocess.run([sys.executable, str(script), str(CONS), str(nuevo), serie, "1" if i == 0 else "0",
                                str(DUCKDB_THREADS), DUCKDB_MEMORY_LIMIT, tmp.as_posix()], capture_output=True, text=True)
            if r.returncode != 0:
                log(f"  [AVISO] escribir {serie} por proceso falló ({r.stderr.strip()[-300:]}); se usa el método en el proceso")
                for p in (nuevo, Path(str(nuevo) + ".wal")):
                    p.unlink(missing_ok=True)
                return False
            log(f"  consolidado {serie} escrito en {time.time()-_t:,.1f} s")
        return True
    except Exception as e:
        log(f"  [AVISO] escritura por serie no disponible ({e}); se usa el método en el proceso")
        for p in (nuevo, Path(str(nuevo) + ".wal")):
            p.unlink(missing_ok=True)
        return False


def build_dashboard_db(con, desde_raw=False):
    """aggs/dashboard.duckdb en UNA pasada, desde el MISMO `consolidado` post-procesado que alimenta los
    aggs (PDC/Territorio '0' -> 'Sin agrupar', CECOs sin clasificar ni movimiento fuera). Antes lo hacía
    generate_dashboard_db.py releyendo el parquet CRUDO: 11 min más y etiquetas distintas a las de los aggs.
    Contenido: `consolidado` (tabla, sin `monto` e SIN índices ART: medido 2.4 GB vs 17.7 GB, y igual o
    más rápida), rollups_* con chequeo de paridad, y cada agg chico como tabla `agg_<nombre>`.
    Se escribe en dashboard.duckdb.nuevo y se reemplaza al final; si el tablero tiene abierto el
    archivo (Windows) queda el .nuevo con aviso, para renombrarlo con el tablero apagado."""
    nuevo = OUT / "dashboard.duckdb.nuevo"
    final = OUT / "dashboard.duckdb"
    for p in (nuevo, Path(str(nuevo) + ".wal")):
        p.unlink(missing_ok=True)
    log("Generando dashboard.duckdb (consolidado + rollups + aggs)...")
    if PROGRESS is not None:
        PROGRESS.step("duckdb: consolidado")
    # 2026-10-29: `consolidado` se escribe ANTES de abrir el archivo en este proceso, por serie y en procesos nuevos (ver
    # _escribir_consolidado_por_serie). Los hilos NO son el factor: en proceso nuevo 16 hilos (26 a 28 s) ganan a 4 (31 a 33 s).
    ya_escrito = SERIES_CTX is None and CONS.exists() and _escribir_consolidado_por_serie(nuevo)
    con.execute(f"ATTACH '{nuevo.as_posix()}' AS out")
    try:
        if ya_escrito:
            pass   # la tabla `consolidado` ya está en el archivo (ver arriba)
        elif SERIES_CTX is not None:   # modo por serie: `consolidado` es una vista LAZY sobre aggs/series/ (ya sin CECOs excluidos)
            _t = time.time()
            con.execute("CREATE TABLE out.consolidado AS SELECT * EXCLUDE (monto) FROM consolidado")
            log(f"  consolidado escrito desde las series en {time.time()-_t:,.1f} s")
        elif desde_raw:   # --solo duckdb: el post-proceso se aplica al vuelo, escribiendo directo al archivo (poca RAM)
            _t = time.time()
            con.execute(f"CREATE TABLE out.consolidado AS SELECT * EXCLUDE (monto) FROM ({_sql_consolidado_final()})")
            log(f"  consolidado escrito en {time.time()-_t:,.1f} s")
        else:
            con.execute("CREATE TABLE out.consolidado AS SELECT * EXCLUDE (monto) FROM consolidado")
        n = con.execute("SELECT COUNT(*) FROM out.consolidado").fetchone()[0]
        log(f"  consolidado          {n:>12,} filas")
        base = con.execute("SELECT ROUND(SUM(monto_cons), 2), ROUND(SUM(monto_loc), 2) FROM out.consolidado").fetchone()
        if PROGRESS is not None:
            PROGRESS.step("duckdb: base de rollups")
        derivados = _preparar_rollups_derivados(con) if (SERIES_CTX is None and desde_raw) else None
        for nombre, gcols in ROLLUPS_DB.items():
            g = ", ".join(gcols)
            if PROGRESS is not None:
                PROGRESS.step(f"duckdb: {nombre}")
            _t_roll = time.time()
            if SERIES_CTX is not None:   # rollups por serie en caché: solo se recalculan las series que cambiaron
                _t = time.time()
                rutas, rehechas = SERIES_CTX.rollup(con, nombre, gcols)
                con.execute(f"CREATE TABLE out.{nombre} AS SELECT * FROM read_parquet([{', '.join(repr(r) for r in rutas)}])")
                log(f"  {nombre:28s} series recalculadas: {', '.join(rehechas) or 'ninguna (caché)'} ({time.time()-_t:,.1f} s)")
            elif derivados is not None:   # rollup derivado del agregado base + dimensiones (ver _preparar_rollups_derivados)
                con.execute(f"CREATE TABLE out.{nombre} AS {derivados[nombre]}")
            else:
                con.execute(f"""
                    CREATE TABLE out.{nombre} AS
                    SELECT {g}, sem, Serie, SUM(monto_cons) AS monto_cons, SUM(monto_loc) AS monto_loc
                    FROM out.consolidado GROUP BY {g}, sem, Serie
                """)
            tot = con.execute(f"SELECT ROUND(SUM(monto_cons), 2), ROUND(SUM(monto_loc), 2) FROM out.{nombre}").fetchone()
            if max(abs((a or 0) - (b or 0)) for a, b in zip(base, tot)) > 1.0:
                log(f"  [ERROR de paridad] {nombre} no cuadra con consolidado -- se elimina")
                con.execute(f"DROP TABLE out.{nombre}")
                continue
            nr = con.execute(f"SELECT COUNT(*) FROM out.{nombre}").fetchone()[0]
            log(f"  {nombre:28s} {nr:>12,} filas (paridad OK) en {time.time()-_t_roll:,.1f} s")
        for t in ("h2_base", "h2_base_ceco", "h2_dim_ceco", "h2_dim_cuenta"):   # liberan la RAM de los rollups derivados
            con.execute(f"DROP TABLE IF EXISTS {t}")
        if PROGRESS is not None:
            PROGRESS.step("duckdb: aggs y digitales")
        for p in sorted(OUT.glob("*.parquet")):
            if p.name.startswith(("_", "tmp_")) or p.stat().st_size < 1024 or p.stat().st_size > DB_MAX_AGG_MB * 1e6:
                continue   # '_'/'tmp_' = temporales de otra corrida; <1 KB = vacío/corrupto
            con.execute(f"CREATE TABLE out.agg_{p.stem} AS SELECT * FROM read_parquet('{p.as_posix()}')")
            log(f"  agg_{p.stem:24s} copiado ({p.stat().st_size / 1e6:,.1f} MB parquet)")
        # canal digital (aggs_digitales/): tablas dig_<nombre>; el tablero elige agg_ / dig_ / ambas según el filtro Canal
        for p in sorted((ROOT / "aggs_digitales").glob("*.parquet")):
            con.execute(f"CREATE TABLE out.dig_{p.stem} AS SELECT * FROM read_parquet('{p.as_posix()}')")
            log(f"  dig_{p.stem:24s} copiado ({p.stat().st_size / 1e6:,.1f} MB parquet)")
        con.execute("CHECKPOINT out")
    finally:
        con.execute("DETACH out")
    try:
        final.unlink(missing_ok=True)
        Path(str(final) + ".wal").unlink(missing_ok=True)
        nuevo.replace(final)
        log(f"  dashboard.duckdb listo: {final.stat().st_size / 1e6:,.0f} MB")
    except OSError as e:
        log(f"  [AVISO] el tablero tiene abierto dashboard.duckdb ({e}). Quedó {nuevo.name}: apaga el tablero, "
            f"borra dashboard.duckdb y renombra {nuevo.name} -> dashboard.duckdb")
    # canal digital completo (aggs/digitales.duckdb): segundos, archivo aparte
    try:
        import build_digitales_db
        build_digitales_db.main()
    except Exception as e:
        log(f"  [AVISO] digitales.duckdb no se generó: {e}")


def build_fcst_srv(con, destino_dir=None):
    """FCST PLAN por Cuenta x Semana: aggs/fcst_srv_cuenta.parquet (físico) y
    aggs_digitales/fcst_srv_cuenta.parquet (digital). Antes lo copiaba importar_fcst_gcf.py desde
    Spend Legacy; ahora sale de las mismas fuentes (r26_sem40.parquet sem <= 40 + tabla FCST
    sem > 40) y del mismo catálogo de cuentas. Las filas FCS NO entran al consolidado: así no
    contaminan los SUM sin filtro de Serie (Cashi, rollups).
    `destino_dir`: escribe ambos archivos ahí (para comparar sin tocar producción)."""
    if FCST is None or not R26_CONGELADO.exists():
        log("  AVISO: faltan fcst_semNN.parquet o r26_sem40.parquet — se omite fcst_srv_cuenta")
        return
    from build_digitales import cecos_digitales
    log("Generando fcst_srv_cuenta.parquet (FCST PLAN)...")
    con.register("ceco_dig_fc", pl.DataFrame({"ID_CENTRO_COSTOS": cecos_digitales()}).to_arrow())
    con.register("cat_c_fc", obtener_cat_cuentas().to_arrow())

    def _sel(fuente, filtro):
        return (f"SELECT TRIM(CAST(ID_CENTRO_COSTOS AS VARCHAR)) AS ceco,"
                f" CAST(ID_CONCEPTO_CUENTA_NIV3 AS VARCHAR) AS cta, CAST(SEMANA_ARG AS INTEGER) AS sem,"
                f" CAST(\"SUM_MONTO_MNX_CONS_(SUMA)\" AS DOUBLE) AS m_cons,"
                f" CAST(COALESCE(TRY_CAST(\"Monto_Local\" AS DOUBLE), \"SUM_MONTO_MNX_CONS_(SUMA)\") AS DOUBLE) AS m_loc"
                f" FROM read_parquet('{fuente}') WHERE {filtro}")
    # 2026-11-10 (igual que Spend Legacy, 06-nov): corte del FCST PLAN en sem 43 con r26_sem43 congelado
    # (solo fisico: sin CECOs digitales) + tabla FCST del servidor sem >= 44.
    _corte = 43
    _r43 = DATOS / "r26_sem43.parquet"
    if _r43.exists():
        base = f"{_sel(_r43.as_posix(), f'SEMANA_ARG <= {_corte}')} UNION ALL {_sel(FCST, f'SEMANA_ARG > {_corte}')}"
    else:
        base = f"{_sel(R26_CONGELADO.as_posix(), 'SEMANA_ARG <= 40')} UNION ALL {_sel(FCST, 'SEMANA_ARG > 40')}"

    dig_dir = ROOT / "aggs_digitales"
    dig_dir.mkdir(exist_ok=True)
    salidas = ((Path(destino_dir) / "fcst_srv_cuenta.parquet" if destino_dir else OUT / "fcst_srv_cuenta.parquet", "NOT IN", "fisico"),
               (Path(destino_dir) / "fcst_srv_cuenta_dig.parquet" if destino_dir else dig_dir / "fcst_srv_cuenta.parquet", "IN", "digital"))
    for destino, op, canal in salidas:
        tmp = destino.with_suffix(".parquet.tmp")
        con.execute(f"""
            COPY (
                SELECT f.cta AS ID_CONCEPTO_CUENTA_NIV3,
                       COALESCE(c.cat_Grupo_de_Cuentas, 'No Clasificado') AS cat_Grupo_de_Cuentas,
                       COALESCE(c.cat_Cuentas, 'No Clasificado') AS cat_Cuentas,
                       f.sem, SUM(f.m_cons) AS FCST_cons, SUM(f.m_loc) AS FCST_loc
                FROM ({base}) f
                LEFT JOIN cat_c_fc c ON f.cta = c.ID_CONCEPTO_CUENTA_NIV3
                WHERE f.ceco {op} (SELECT ID_CENTRO_COSTOS FROM ceco_dig_fc)
                GROUP BY 1, 2, 3, 4
                HAVING SUM(f.m_cons) <> 0 OR SUM(f.m_loc) <> 0
            ) TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        n, tot = con.execute(f"SELECT COUNT(*), SUM(FCST_cons) FROM read_parquet('{tmp.as_posix()}')").fetchone()
        if n == 0:
            # Hoy fcst_semNN y r26_sem40 no traen CECOs digitales: no se pisa un archivo existente con uno vacío.
            tmp.unlink()
            log(f"  fcst_srv {canal:8s} sin filas en las fuentes FCST/r26_sem40 — se conserva {destino.name} existente")
            continue
        tmp.replace(destino)
        log(f"  fcst_srv {canal:8s} {n:>8,} filas · FCST PLAN {tot/1e6:,.1f} M")


# 2026-08-24: etapas del pipeline, para --solo. Regenerar TODO cuesta ~48 min;
# casi siempre solo cambió 1 agg (pedido del usuario: "solo corre los aggs que
# necesitamos, no todos, para no esperar otra hora").
ETAPAS = {
    "dims":              "todos los aggs por dimensión (build_aggs)",
    "cierres_expansion": "cierres_expansion.parquet",
    "forecast_cuenta":   "forecast_cuenta.parquet",
    "fcst_srv":          "fcst_srv_cuenta.parquet (FCST PLAN: tabla FCST del servidor; + aggs_digitales/)",
    "agrupadores_reales": "agrupadores_reales.parquet",
    "contraloria":       "contraloria.parquet",
    "cierres_arbol":     "cierres_arbol.parquet",
    "pdc_cecos":         "pdc_cecos.json",
    "orden_gpo":         "orden_gpo.json",
    "responsables":      "responsables_gpo.json + responsables_cta.json",
    "pdc_ids":           "pdc_ids.json",
    "pdv_agrupacion":    "pdv_nombres.json + pdv_cecos.json (agrupación por Eco PDV en Análisis PDC)",
    "riesgos":           "riesgos.json",
    "duckdb":            "dashboard.duckdb (consolidado + rollups + aggs chicos, una sola pasada; va al final)",
}

# 2026-08-25 (pedido del usuario): etapas que SOLO dependen del Catálogo de
# estructura/cuentas (Excel), no de los parquets semanales de Real/Plan ->
# no hace falta regenerarlas en cada actualización semanal, solo cuando el
# catálogo mismo cambia. "riesgos" queda AFUERA de este set a propósito: lee
# un Word que Planeación edita cada semana (ver build_riesgos), sí es
# contenido semanal aunque no venga de los parquets de Real/Plan.
CATALOG_ONLY_ETAPAS = {"pdc_cecos", "orden_gpo", "responsables", "pdc_ids", "pdv_agrupacion"}

# Texto del paso (PROGRESS.step) de cada etapa, en el orden en que main() las corre; sirve para calcular el plan.
_PASOS_ETAPA = [
    ("cierres_expansion", "Generando cierres_expansion.parquet"),
    ("forecast_cuenta", "Generando forecast_cuenta.parquet"),
    ("fcst_srv", "Generando fcst_srv_cuenta.parquet"),
    ("agrupadores_reales", "Generando agrupadores_reales.parquet"),
    ("contraloria", "Generando contraloria.parquet"),
    ("cierres_arbol", "Generando cierres_arbol.parquet"),
    ("pdc_cecos", "Generando pdc_cecos.json"),
    ("orden_gpo", "Generando orden de grupos"),
    ("responsables", "Generando responsables de grupos"),
    ("pdc_ids", "Generando pdc_ids.json"),
    ("pdv_agrupacion", "Generando pdv_nombres.json y pdv_cecos.json"),
    ("riesgos", "Generando riesgos.json"),
]


def _plan_pasos(corre, dims_solo, usar_series: bool, usar_cache: bool) -> list:
    """Lista exacta [(texto, num_steps)] de los pasos que main() va a ejecutar, para fijar el total de la barra."""
    plan = []
    if usar_series:
        plan.append(("Usando aggs/series/ existentes", 5) if usar_cache else ("Consolidado por serie", 1))
    elif usar_cache:
        plan.append(("Usando _consolidado.parquet existente", 5))
    else:
        plan += [("Leyendo catálogos", 1), ("Uniendo 3 series", 1), ("Construyendo y persistiendo", 1)]
    plan += [("Mapeando", 1), ("Detectando", 1)]
    if dims_solo is None or dims_solo:
        n_dims = len(DIMS) if dims_solo is None else len(dims_solo & set(DIMS))
        plan += [(f"Agg {i}/{n_dims}", 1) for i in range(1, n_dims + 1)]
    plan += [(texto, 1) for etapa, texto in _PASOS_ETAPA if corre(etapa)]
    if corre("duckdb"):
        plan += [("Generando dashboard.duckdb", 1), ("duckdb: consolidado", 1), ("duckdb: base de rollups", 1)]
        plan += [(f"duckdb: {nombre}", 1) for nombre in ROLLUPS_DB]
        plan.append(("duckdb: aggs y digitales", 1))
    plan.append(("Validando totales e integridad", 1))
    return plan


def _preguntar(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        # sin stdin real (invocado por error desde un proceso sin terminal) ->
        # mejor cancelar limpio que colgarse esperando un input() que no llega.
        print("\n(sin entrada disponible — cancelando)")
        return ""


def _asistente_bare(args):
    """Wizard para 'python build_data.py' SIN ninguna bandera de alcance —
    ningún caller interno (actualizar.py, su --menu, cli.py) invoca el script
    así, todos pasan --rebuild/--resume/--solo/--aggs-only explícitos, así que
    llegar aquí solo puede ser una persona corriendo el script a mano sin
    saber bien qué pedir. Muta `args` in-place; el resto de main() sigue igual."""
    print("=" * 70)
    print("build_data.py — sin banderas de alcance, ¿qué quieres regenerar?")
    print("=" * 70)

    if catalogos.estructura_desactualizada() or catalogos.cuentas_desactualizado():
        print("\nSe detectó un cambio en el Catálogo de estructura y/o de cuentas.")
        print("Se te preguntará por cada uno antes de reconvertirlo.")
        args.interactivo = True
    else:
        print("\nCatálogos de estructura/cuentas: sin cambios detectados.")

    print("\n¿Qué aggs quieres regenerar?")
    print("  Enter   = 'semanal' (todo lo que depende de los datos, sin catálogo)")
    print("  'todo'  = rebuild completo (~48 min)")
    print("  o una lista de etapas/dimensiones separadas por coma")
    print(f"Etapas disponibles: {', '.join(sorted(ETAPAS))}")
    raw = _preguntar(">>> ")
    etapas_arg = raw if raw else "semanal"

    if etapas_arg == "todo":
        args.solo = None
        args.rebuild = True
    else:
        args.solo = etapas_arg
    print()


def _regenerar_dashboard_db_si_hace_falta() -> None:
    """Regenera aggs/dashboard.duckdb (usada por la búsqueda SQL de CECO en
    'Detalle CECO', ver _DDB_PATH en app.py) cuando _consolidado.parquet es
    más nuevo que la BD. 2026-08-27: esta función faltaba en este archivo —
    la llamada de más abajo (if ok: ...) apuntaba a un nombre que solo existía
    en actualizar.py (sin el guion bajo inicial), y tronaba con NameError al
    final de cada --rebuild, después de los 40+ min del pipeline completo."""
    cons = OUT / "_consolidado.parquet"
    ddb = OUT / "dashboard.duckdb"
    if not cons.exists():
        return
    if ddb.exists() and ddb.stat().st_mtime >= cons.stat().st_mtime:
        log("dashboard.duckdb: ya está al día (no se regenera).")
        return
    log("dashboard.duckdb: _consolidado.parquet es más nuevo — regenerando "
        "(si no, la búsqueda de CECO en 'Detalle CECO' mostraría la semana vieja)...")
    rc = subprocess.run([sys.executable, str(BASE / "generate_dashboard_db.py")],
                        cwd=str(BASE)).returncode
    if rc != 0:
        log(f"[ERROR] generate_dashboard_db.py falló (código {rc}) — "
            f"la búsqueda de CECO en 'Detalle CECO' puede mostrar datos viejos. "
            f"Corre a mano: python codigo/generate_dashboard_db.py")


def main():
    global PROGRESS, SERIES_CTX
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="reconstruir _consolidado desde los parquets fuente")
    ap.add_argument("--con-duckdb", action="store_true", help="al terminar regenera también dashboard.duckdb (+15 min)")
    ap.add_argument("--aggs-only",action="store_true", help="solo regenerar aggs desde _consolidado existente")
    ap.add_argument("--actualizar-catalogos", action="store_true", help="fuerza la reconversión de Catálogos Excel a Parquet optimizado")
    ap.add_argument("--interactivo", action="store_true", help="pregunta antes de reconvertir si los Excel cambiaron")
    ap.add_argument("--semana", type=int, default=None,
                    help="semana ARG de los parquet a leer (por defecto, última semana cerrada)")
    ap.add_argument("--forzar-semana-real", type=int, default=None, metavar="N",
                    help="fuerza sem_max_real a N en vez del valor auto-detectado por "
                         "detect_cortes(). Lo usa actualizar.py cuando, tras traer datos "
                         "nuevos de Real 2026, el usuario elige NO avanzar el corte del "
                         "tablero a la semana recién cerrada.")
    ap.add_argument("--resume", action="store_true", help="continuar desde donde se paró (salta consolidado si ya existe)")
    ap.add_argument("--solo", default=None, metavar="ETAPAS",
                    help="regenerar SOLO estas etapas (separadas por coma). Acepta nombres de "
                         "etapa (ceco, pdc_pospre, cierres...), de dimensión (pdc, division, "
                         "conciliacion...) o la palabra especial 'semanal' (todo lo que depende "
                         "de los parquets de Real/Plan, sin las etapas que solo dependen del "
                         "Catálogo Excel — ver CATALOG_ONLY_ETAPAS). Ej: --solo ceco  |  "
                         "--solo ceco,pdc  |  --rebuild --solo semanal (actualización semanal). "
                         "Sin --rebuild, reusa _consolidado.parquet en caché; con --rebuild, "
                         "primero lo reconstruye desde los parquets fuente y LUEGO limita qué "
                         "más se regenera a estas etapas. Usa --listar-etapas para ver todas.")
    ap.add_argument("--listar-etapas", action="store_true",
                    help="imprime las etapas/dimensiones válidas para --solo y termina")
    ap.add_argument("--auto", action="store_true",
                    help="omite el asistente interactivo de --solo/catálogos que se activa "
                         "cuando no se pasa ninguna bandera de alcance. Para invocar desde "
                         "otro script; nunca a mano si esperas poder contestar algo.")
    ap.add_argument("--series", action="store_true",
                    help="EXPERIMENTAL (apagado por defecto): consolidado y aggs por serie con caché incremental en "
                         "aggs/series/ (build_series.py). Probado: aggs idénticos al modo normal y las etapas previas a "
                         "`duckdb` ~70%% más rápidas, PERO la etapa `duckdb` no mejoró (14 min solo para escribir "
                         "`consolidado`). Ver documentacion/PLAN_AGGS_POR_SERIE_2026-10-22.md, sección 8.")
    ap.add_argument("--sin-series", action="store_true", help="(compatibilidad) equivale a no pasar --series")
    args = ap.parse_args()

    if args.listar_etapas:
        print("\nEtapas válidas para --solo:\n")
        for k, v in ETAPAS.items():
            print(f"  {k:20s} {v}")
        print("\nDimensiones válidas para --solo (subconjunto de 'dims'):\n")
        for k in sorted(DIMS):
            print(f"  {k}")
        print(f"\nPalabra especial 'semanal' = todas las etapas EXCEPTO "
              f"{', '.join(sorted(CATALOG_ONLY_ETAPAS))}\n(esas 5 solo dependen del Catálogo "
              f"Excel, no de los parquets de Real/Plan de la semana).")
        print("\nEjemplos:\n  python build_data.py --solo ceco"
              "\n  python build_data.py --solo ceco,pdc_pospre"
              "\n  python build_data.py --solo pdc,division"
              "\n  python build_data.py --rebuild --solo semanal   (actualización semanal)\n")
        sys.exit(0)

    # 2026-08-26 (pedido del usuario): sin NINGUNA bandera de alcance y sin
    # --auto, se asume que una persona corrió el script a mano sin saber bien
    # qué pedir, y se le pregunta en vez de asumir "todo" en silencio. Ningún
    # caller interno (actualizar.py, su --menu, cli.py) invoca build_data.py
    # así — todos pasan --rebuild/--resume/--solo/--aggs-only explícitos.
    if not args.auto and not any([args.rebuild, args.resume, args.aggs_only, args.solo]):
        _asistente_bare(args)

    # --solo: validar y derivar qué corre
    solo = None
    if args.solo:
        solo = {s.strip() for s in args.solo.split(",") if s.strip()}
        if "semanal" in solo:
            solo.discard("semanal")
            solo |= (set(ETAPAS) - CATALOG_ONLY_ETAPAS)
        _validos = set(ETAPAS) | set(DIMS)
        _malos = solo - _validos
        if _malos:
            sys.exit(f"ERROR: etapa(s) desconocida(s): {', '.join(sorted(_malos))}\n"
                     f"Corre --listar-etapas para ver las válidas.")
        # 2026-08-25 (pedido del usuario): --solo ya NO fuerza rebuild=False
        # siempre — si el usuario también pasó --rebuild explícito (ej. la
        # actualización semanal: "--rebuild --solo semanal"), se reconstruye
        # consolidado desde los parquets fuente Y LUEGO se limita qué más se
        # regenera a las etapas pedidas, en un solo comando. Sin --rebuild,
        # el comportamiento de siempre: reusa el consolidado en caché.
        args.aggs_only = not args.rebuild

    def _corre(etapa: str) -> bool:
        return solo is None or etapa in solo

    # dims: None = todas; set() = ninguna; set(...) = solo esas
    if solo is None:
        _dims_solo = None
    elif "dims" in solo:
        _dims_solo = None
    else:
        _dims_solo = solo & set(DIMS)

    _total_pasos = TOTAL_STEPS_PIPELINE if solo is None else max(3, len(solo) + 3)
    PROGRESS = ProgressTracker(total_steps=_total_pasos, desc="Rebuild Pipeline")
    t0 = time.time()
    # 2026-08-24: ":memory:" en vez de archivo (`_build.duckdb`) — medido en
    # vivo que el archivo en disco era el cuello de botella real (I/O, no
    # CPU: ~1.3x de paralelismo real con 16 threads, debía ser ~10-16x).
    # Nada depende de que este DuckDB persista entre corridas: el `finally`
    # de abajo ya borraba `_build.duckdb`/`.wal` al terminar (era un scratch
    # de una sola corrida) y el caché real entre corridas es
    # `aggs/_consolidado.parquet` (archivo aparte, sin relación con esto).
    # Con 20GB de memory_limit y este dataset (~1GB comprimido en parquet),
    # cabe cómodo en RAM; si algún día no cupiera, DuckDB hace spill solo a
    # `_ddb_tmp` (ver PRAGMA temp_directory abajo).
    con = duckdb.connect(":memory:")
    con.execute(f"PRAGMA threads={DUCKDB_THREADS}")
    con.execute(f"PRAGMA memory_limit='{DUCKDB_MEMORY_LIMIT}'")
    # 2026-08-24: el pipeline solo agrega (GROUP BY) y nunca depende del
    # orden de filas de salida -> preserve_insertion_order en true (default)
    # le hace a DuckDB cargar con contabilidad extra de orden en cada
    # agregación grande, sin ningún beneficio aquí.
    con.execute("PRAGMA preserve_insertion_order=false")
    con.execute("PRAGMA temp_directory='_ddb_tmp'")

    ok = False
    try:
        _resolver_fuentes(args.semana)   # necesario también para comparar mtimes contra CONS
        # Modo por serie: con --rebuild, o si ya existe aggs/series/ (corridas sin --rebuild). Si no, modo anterior.
        _ctx = build_series.SeriesBuild(OUT, log)
        usar_series = args.series and (not args.sin_series) and (args.rebuild or _ctx.existe())
        SERIES_CTX = _ctx if usar_series else None
        _ref_cache = _ctx.firmas_path if (usar_series and _ctx.existe()) else CONS
        fuente_mas_nueva = _ref_cache.exists() and max(
            Path(p).stat().st_mtime for p in (R25, R26, P26)) > _ref_cache.stat().st_mtime
        # 2026-08-25 (pedido del usuario): ya NO se dispara un --rebuild
        # automático y silencioso cuando llega la semana nueva — corría los
        # 44 pasos completos (incluyendo etapas que solo dependen del
        # Catálogo Excel, que casi nunca cambia semana a semana) sin que
        # nadie lo pidiera explícitamente. Ahora, si hay parquets más nuevos
        # y no se pasó --rebuild, el script se detiene y explica cómo pedirlo
        # (--resume es la única excepción: es una decisión explícita de
        # "sigue con lo que hay", aunque esté vieja).
        # 2026-11-10: --solo forecast_cuenta,fcst_srv (menu "Solo Forecast") no lee R25/R26/P26 ni el consolidado:
        # no aplica el aviso de semana nueva ni se reconstruye el consolidado.
        _solo_forecast = (solo is not None and bool(solo) and solo <= {"forecast_cuenta", "fcst_srv"}
                          and not args.rebuild)
        if fuente_mas_nueva and not args.rebuild and not args.resume and not _solo_forecast:
            sys.exit(
                f"Hay parquets fuente más nuevos que {CONS.name} (llegó una semana nueva) — "
                f"nada se reconstruye solo, hay que decirle explícitamente qué hacer:\n\n"
                f"  Actualización semanal normal (recomendado, más rápido):\n"
                f"    python build_data.py --rebuild --solo semanal\n"
                f"    Reconstruye consolidado con la semana nueva + todo lo que depende de\n"
                f"    datos semanales (los 22 aggs, ceco, ceco_detalle, pdc_pospre, cierres,\n"
                f"    forecast, riesgos...). Se SALTA lo que solo depende del\n"
                f"    Catálogo Excel ({', '.join(sorted(CATALOG_ONLY_ETAPAS))}) — eso no\n"
                f"    cambia cada semana.\n"
                f"    Después, corre también: python generate_dashboard_db.py\n"
                f"    (regenera aggs/dashboard.duckdb con la semana nueva — si no, la\n"
                f"    búsqueda de CECO en Detalle CECO sigue mostrando datos viejos vía SQL\n"
                f"    aunque el resto de la app ya tenga la semana nueva).\n\n"
                f"  Si además cambió el Catálogo de estructura/cuentas:\n"
                f"    python build_data.py --rebuild\n"
                f"    Reconstruye TODO, ~48 min.\n\n"
                f"  --listar-etapas para ver el detalle de cada etapa disponible."
            )
        # --resume prioriza usar caché incluso si --rebuild se pasó (resume = "continúa donde se paró")
        _hay_cache = _ctx.existe() if usar_series else CONS.exists()
        usar_cache = (args.resume and _hay_cache) or (_hay_cache and not args.rebuild and (not fuente_mas_nueva or _solo_forecast))
        # 2026-10-29: total exacto de pasos (antes len(--solo)+3 se quedaba corto y la barra salía "13/?" sin avance ni ETA)
        PROGRESS.fijar_plan(_plan_pasos(_corre, _dims_solo, usar_series, usar_cache))

        try:
            con.execute("DROP VIEW IF EXISTS consolidado_raw")
        except duckdb.Error:
            pass
        try:
            con.execute("DROP TABLE IF EXISTS consolidado_raw")
        except duckdb.Error:
            pass

        if usar_series:
            if usar_cache:
                PROGRESS.step("Usando aggs/series/ existentes", num_steps=5)
                log("Usando aggs/series/ existentes (c_R25, c_P26, c_R26). --rebuild recalcula las series que cambiaron.")
                _cambiadas = []
            else:
                if args.aggs_only:
                    sys.exit("ERROR: --aggs-only requiere aggs/series/, que no existe. Corre con --rebuild.")
                _cambiadas = construir_consolidado_series(con, _ctx, forzar_catalogos=args.actualizar_catalogos,
                                                          interactivo=args.interactivo)
            _ctx.vistas(con)     # consolidado_raw y consolidado (vistas lazy, sin los CECOs excluidos)
            if _cambiadas or not CONS.exists():
                _ctx.exportar_consolidado(con, CONS)    # _consolidado.parquet: respaldo de Cashi y fecha para el menú
        elif usar_cache:
            PROGRESS.step("Usando _consolidado.parquet existente", num_steps=5)
            log(f"Usando {CONS.name} existente ({CONS.stat().st_size/1e6:.0f} MB). --rebuild para recrear.")
            con.execute(f"CREATE OR REPLACE VIEW consolidado_raw AS {_select_consolidado(con)}")
        else:
            if args.aggs_only:
                sys.exit(f"ERROR: --aggs-only requiere {CONS.name}, que no existe. Corre sin esa bandera.")
            build_consolidado(con, forzar_catalogos=args.actualizar_catalogos, interactivo=args.interactivo)

        # El catálogo trae '0' (texto) en varias columnas de la jerarquía:
        #  - PDC = '0' fusionaba miles de CECOs en un renglón -> se sustituye por
        #    'CECO <ID_CENTRO_COSTOS>' (o 'Sin agrupar' si tampoco hay CECO).
        #  - Territorio/Zona/Región = '0' son niveles de agrupación (un solo '0'
        #    cubre muchos CECOs, no se puede mapear a UN ceco) -> 'Sin agrupar'.
        PROGRESS.step("Mapeando catálogo de CECOs y vista consolidado")
        _ceco_nom_df = ceco_nombres()
        con.register("ceco_nom", _ceco_nom_df.to_arrow())
        # 2026-08-24: export para la pestaña "Detalle CECO" (etiqueta "ID · Nombre"
        # de cada CECO en la tabla General y en el árbol Grupo Cuentas->Cuenta->CECO).
        (OUT / "ceco_nombres.json").write_text(
            json.dumps(dict(zip(_ceco_nom_df["eco"].to_list(), _ceco_nom_df["nombre"].to_list())),
                       ensure_ascii=False),
            encoding="utf-8")
        # 2026-10-21: lista de CECOs digitales (Agrupa Canales = Digitales) para que Comparación (snapshots crudos, que
        # traen físico + digital) respete el canal del filtro global igual que el consolidado.
        from build_digitales import cecos_digitales as _cd
        (OUT / "ceco_digitales.json").write_text(json.dumps(_cd()), encoding="utf-8")
        # 2026-08-24: TABLE en vez de VIEW — "consolidado" se lee con GROUP BY
        # completo ~25 veces (build_aggs x20 + build_cierres/_expansion,
        # movimiento, forecast_ceco, pdc_pospre, ceco_general/detalle,
        # cierres_arbol...). Con VIEW, el JOIN a ceco_nom + los 4 CASE WHEN se
        # recalculaban en CADA una de esas 25 consultas sobre toda la tabla;
        # materializarla una sola vez y dejar que el resto solo la escanee es
        # el mayor cuello de botella medido del pipeline.
        # 2026-09-01 (pedido del usuario): un CECO "No Clasificado" (sin
        # Dirección/División en el catálogo) que además no tiene NINGÚN
        # movimiento real (suma de monto_cons = 0 en todas sus filas/series)
        # no aporta nada al tablero y solo genera ruido visual -> se omite
        # por completo aquí, de una sola vez, para que ninguna vista aguas
        # abajo (dims, ceco, ceco_detalle, cierres, forecast...) lo muestre.
        # Un CECO No Clasificado que SÍ tiene movimiento (monto != 0) NO se
        # toca -> ocultar gasto real solo por no estar clasificado sería
        # peligroso (dinero real desaparecería del tablero sin aviso).
        # 2026-10-21: con `--solo duckdb` NO se materializa `consolidado` en RAM (≈17 GB: llevó la PC a 1.7 GB
        # libres); build_dashboard_db lo escribe directo en el archivo, desde consolidado_raw, con este mismo SQL.
        solo_db = solo is not None and solo == {"duckdb"}
        # 2026-10-29: `consolidado` es una VISTA sobre el parquet, no una tabla de ~17 GB en RAM. Medido en sandbox con las
        # 86.5 M filas: las 6 dimensiones, cierres_expansion, agrupadores y contraloria tardan 4x menos leyendo el parquet
        # (solo lee las columnas que cada consulta usa) y desaparecen 28 s de materializar y el derrame a disco; solo
        # cierres_arbol va 2x más lento (108 s contra 55 s). Con `--solo duckdb` ya era así.
        if not solo_db and not usar_series:   # en modo por serie `consolidado` ya es una vista lazy
            con.execute(f"CREATE OR REPLACE VIEW consolidado AS {_sql_consolidado_final()}")

        PROGRESS.step("Detectando cortes semanales")
        if solo_db:
            meta = json.loads((OUT / "_meta.json").read_text(encoding="utf-8"))   # no se recalcula ni se reescribe
        else:
            meta = detect_cortes(con)
        log(f"Cortes: sem_max_real={meta['sem_max_real']} sem_max_plan={meta['sem_max_plan']}")
        if args.forzar_semana_real is not None and args.forzar_semana_real != meta["sem_max_real"]:
            log(f"Semana real forzada manualmente a {args.forzar_semana_real} "
                f"(auto-detectada: {meta['sem_max_real']}) — el tablero se queda en la semana "
                f"pedida aunque ya haya datos de semanas posteriores en consolidado.")
            meta["sem_max_real"] = args.forzar_semana_real

        if solo is not None:
            log(f"--solo activo: se regeneran únicamente [{', '.join(sorted(solo))}]; "
                f"el resto de aggs se deja intacto.")

        if _dims_solo is None or _dims_solo:
            build_aggs(con, meta, resume=args.resume, solo_dims=_dims_solo)

        if _corre("cierres_expansion"):
            PROGRESS.step("Generando cierres_expansion.parquet")
            build_cierres_expansion(con)

        if _corre("forecast_cuenta"):
            PROGRESS.step("Generando forecast_cuenta.parquet")
            try:
                build_forecast_cuenta()
            except Exception as e:
                log(f"  [AVISO] build_forecast_cuenta falló (Excel FORECAST mal formado): {e}")

        if _corre("fcst_srv"):
            PROGRESS.step("Generando fcst_srv_cuenta.parquet")
            try:
                build_fcst_srv(con)
            except Exception as e:
                log(f"  [AVISO] build_fcst_srv falló: {e}")

        if _corre("agrupadores_reales"):
            PROGRESS.step("Generando agrupadores_reales.parquet")
            build_agrupadores_reales(con)

        if _corre("contraloria"):
            PROGRESS.step("Generando contraloria.parquet")
            build_contraloria(con)

        if _corre("cierres_arbol"):
            PROGRESS.step("Generando cierres_arbol.parquet")
            build_cierres_arbol(con, resume=args.resume)

        if _corre("pdc_cecos"):
            PROGRESS.step("Generando pdc_cecos.json")
            build_pdc_cecos(con)

        if _corre("orden_gpo"):
            PROGRESS.step("Generando orden de grupos")
            build_orden_gpo()

        if _corre("responsables"):
            PROGRESS.step("Generando responsables de grupos")
            build_responsables_gpo()

        if _corre("pdc_ids"):
            PROGRESS.step("Generando pdc_ids.json")
            build_pdc_ids()

        if _corre("pdv_agrupacion"):
            PROGRESS.step("Generando pdv_nombres.json y pdv_cecos.json")
            build_pdv_agrupacion()

        if _corre("riesgos"):
            PROGRESS.step("Generando riesgos.json")
            build_riesgos()

        if _corre("duckdb"):
            PROGRESS.step("Generando dashboard.duckdb")
            if not solo_db and SERIES_CTX is None:
                # 2026-10-22: con la tabla `consolidado` (~17 GB) en memoria y memory_limit=20GB, escribir el
                # archivo la derramaba a disco (medido: 39.7 GB en _ddb_tmp, CPU casi en cero, >14 min sin
                # terminar). Se libera la tabla y se escribe en streaming desde el parquet (el camino de
                # `--solo duckdb`, que usa poca RAM). Los aggs ya están listos; `validar` solo lee parquet.
                gc.collect()
                build_dashboard_db(con, desde_raw=True)
            else:
                build_dashboard_db(con, desde_raw=solo_db)

        PROGRESS.step("Validando totales e integridad")
        if not solo_db:
            (OUT / "_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        # Con --solo, validar() compararía aggs recién hechos contra otros que
        # no se tocieron (de un corte anterior) -> falsos FALLA. Se valida solo
        # que los archivos regenerados existan y tengan filas.
        ok = validar(con, meta) if solo is None else True
    finally:
        con.close()
        for p in (BASE / "_build.duckdb", BASE / "_build.duckdb.wal"):
            try:
                p.unlink()
            except OSError:
                pass
        if PROGRESS is not None:
            PROGRESS.close(ok=ok)
        _registrar_tiempo_build(ok, time.time() - t0, args)

    # 2026-08-26: dashboard.duckdb (la BD que usa la búsqueda de CECO de
    # "Detalle CECO" vía SQL, ver _DDB_PATH en app.py) se construye desde
    # _consolidado.parquet. Antes esto era un SEGUNDO COMANDO MANUAL que solo
    # se mencionaba en un texto de ayuda -> si se olvidaba tras una corrida de
    # 20-48 min, los parquets traían la semana nueva pero esa búsqueda seguía
    # sirviendo la semana vieja, en silencio y sin aviso. Ahora se encadena
    # aquí, y solo cuando de verdad hace falta (consolidado más nuevo que la BD).
    # 2026-08-27: FIX — esta llamada apuntaba a una función que nunca se
    # definió en este archivo (solo existía su gemela en actualizar.py, sin
    # el guion bajo inicial) -> NameError al final de CADA --rebuild, después
    # de los 40+ min del pipeline completo. dashboard.duckdb se quedaba
    # desactualizado en silencio (exit code 1 sin haber avisado la causa real).
    # 2026-10-20: ya no se encadena (15 min de los 48); correr aparte con MENU_DASH opción 6,
    # o pasar --con-duckdb para el comportamiento anterior.
    if ok and getattr(args, "con_duckdb", False):
        _regenerar_dashboard_db_si_hace_falta()
    elif ok and not _corre("duckdb"):   # 2026-10-29: con la etapa `duckdb` (parte de `semanal`) la BD ya se regeneró arriba
        log("dashboard.duckdb NO regenerado: corre MENU_DASH opción 6 (o generate_dashboard_db.py) "
            "para que 'Detalle CECO' vea la semana nueva.")

    log(f"{'[OK] LISTO' if ok else '[!] TERMINÓ CON FALLAS'} en {(time.time()-t0)/60:.1f} min -> {OUT}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()


