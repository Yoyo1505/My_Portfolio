# -*- coding: utf-8 -*-
"""
Lectura de gastos semanales desde Excel de Planeación Financiera.
Busca el archivo más reciente en la carpeta de gastos semanales.
"""
import re
from pathlib import Path
import pandas as pd
from typing import Optional


GASTOS_SEMANAL_DIR = Path(
    r"<SHARED_DRIVE>\5. Presupuestos\3. Forecast 2026\Gasto semanal"
)


def obtener_archivo_gastos_reciente() -> Optional[Path]:
    """Busca el archivo Excel más reciente en la carpeta de gastos semanales.
    Evita archivos bloqueados (como los que está usando SharedDrive)."""
    if not GASTOS_SEMANAL_DIR.exists():
        return None

    archivos = sorted(
        GASTOS_SEMANAL_DIR.glob("Gastos Red Garg (sem*)_*.xlsx"),
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )

    # Intenta usar el primero disponible (que no esté bloqueado)
    for archivo in archivos:
        try:
            # Intenta abrir brevemente para validar acceso
            with open(archivo, 'rb'):
                pass
            return archivo
        except (PermissionError, IOError):
            continue

    return None


# 2026-09-07 (pedido del usuario): la hoja "Bridge " trae 4 tablas de
# variaciones curadas a mano por cada responsable de cuenta (Semanal vs Plan,
# Semanal vs AA, Acumulado/YTD vs Plan, Acumulado/YTD vs AA), una debajo de
# otra, cada una precedida por un rótulo ("Variaciones semanales vs Plan",
# etc.) y seguida de su propio encabezado "# Concepto ok Plan/AA % Responsable
# Comentarios". Antes solo se leía la primera tabla (semanal vs Plan) -> el
# Resumen no traía nada de vs AA ni de Acumulado. Se parsean las 4 con el
# mismo layout de columnas (0=#, 2=Concepto, 3=ok, 4=Monto, 5=%,
# 6=Responsable, 7=Comentarios); cada bloque termina en la primera fila con
# Concepto vacío (ahí empiezan las filas de relleno "0/0" y el resumen final).
_SECCIONES_BRIDGE = [
    ("semanal_plan", re.compile(r"variaciones\s+semanales\s+vs\s+plan", re.I)),
    ("semanal_aa", re.compile(r"variaciones\s+semanales\s+vs\s+aa", re.I)),
    ("acumulado_plan", re.compile(r"variaciones\s+acumuladas?\s+vs\s+plan", re.I)),
    ("acumulado_aa", re.compile(r"variaciones\s+acumuladas?\s+vs\s+aa", re.I)),
]

_COLS_BLOQUE = ["Concepto", "Monto", "%", "Responsable", "Comentarios"]


def _df_vacio_bloque() -> pd.DataFrame:
    return pd.DataFrame(columns=_COLS_BLOQUE)


def _buscar_marcador(df_raw: pd.DataFrame, patron: "re.Pattern", desde: int = 0) -> Optional[int]:
    for idx in range(desde, len(df_raw)):
        for val in df_raw.iloc[idx]:
            if isinstance(val, str) and patron.search(val):
                return idx
    return None


def _extraer_bloque(df_raw: pd.DataFrame, idx_marcador: int) -> pd.DataFrame:
    if df_raw.shape[1] <= 7:
        return _df_vacio_bloque()
    header_row = None
    for idx in range(idx_marcador, min(idx_marcador + 6, len(df_raw))):
        val = df_raw.iat[idx, 2]
        if isinstance(val, str) and "Concepto" in val:
            header_row = idx
            break
    if header_row is None:
        return _df_vacio_bloque()

    filas = []
    idx = header_row + 1
    while idx < len(df_raw):
        concepto = df_raw.iat[idx, 2]
        if not isinstance(concepto, str) or not concepto.strip():
            break
        filas.append(idx)
        idx += 1
    if not filas:
        return _df_vacio_bloque()

    # 2026-10-21: las columnas de Responsable/Comentarios se ubican por el texto del
    # encabezado dentro de este bloque (cols 2..7); la sem 42 quitó "Responsable" y movió
    # "Comentarios" de la col 7 a la 6, y con posición fija salían todos vacíos.
    def _col(nombre):
        for c in range(2, min(8, df_raw.shape[1])):
            v = df_raw.iat[header_row, c]
            if isinstance(v, str) and nombre in v.lower():
                return c
        return None

    c_resp, c_com = _col("responsable"), _col("comentario")
    sub = pd.DataFrame({
        "Concepto": df_raw.iloc[filas, 2].values,
        "Monto": pd.to_numeric(df_raw.iloc[filas, 4], errors="coerce").values,
        "%": pd.to_numeric(df_raw.iloc[filas, 5], errors="coerce").values,
        "Responsable": df_raw.iloc[filas, c_resp].values if c_resp is not None else "",
        "Comentarios": df_raw.iloc[filas, c_com].values if c_com is not None else "",
    })
    for c in ("Responsable", "Comentarios"):
        sub[c] = sub[c].fillna("").astype(str).str.strip()
    sub = sub[sub["Monto"].notna()]
    con_com = sub[sub["Comentarios"] != ""]
    if con_com.empty:
        # Planeación aún no comenta este bloque: se muestran las 8 mayores variaciones sin
        # comentario en vez de una tabla vacía.
        sub = sub[~sub["Concepto"].astype(str).str.contains(r"principales|total", case=False, regex=True)]
        con_com = sub.reindex(sub["Monto"].abs().sort_values(ascending=False).index).head(8)
    return con_com.reset_index(drop=True)


def leer_gastos_semanales_todos() -> Optional[dict[str, pd.DataFrame]]:
    """Lee las 4 tablas de variaciones curadas del Bridge semanal: {clave:
    DataFrame} con clave en (semanal_plan, semanal_aa, acumulado_plan,
    acumulado_aa). None si no hay archivo disponible; un bloque sale vacío
    (no None) si su rótulo no aparece en la hoja."""
    archivo = obtener_archivo_gastos_reciente()
    if not archivo:
        return None

    try:
        df_raw = pd.read_excel(archivo, sheet_name="Bridge ", header=None)
    except Exception as e:
        print(f"Error al leer archivo {archivo}: {e}")
        return None

    resultado: dict[str, pd.DataFrame] = {}
    cursor = 0
    for clave, patron in _SECCIONES_BRIDGE:
        idx = _buscar_marcador(df_raw, patron, desde=cursor)
        if idx is None:
            resultado[clave] = _df_vacio_bloque()
            continue
        resultado[clave] = _extraer_bloque(df_raw, idx)
        cursor = idx + 1
    return resultado


