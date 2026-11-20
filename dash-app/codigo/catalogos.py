# -*- coding: utf-8 -*-
"""
catalogos.py — Conversión, optimización y caché de Catálogos Excel a Parquet.

Extrae EXCLUSIVAMENTE las columnas necesarias de:
  - Catálogo de estructura.xlsx (10 cols + ECO_PDV + ECO + Descripción de 71 columnas)
  - Catálogo grupo cuentas.xlsx (6 cols + Orden + Responsables + Lider de 17 columnas)
  - Conciliación PDC_*_S<sem>.xlsx (solo Eco/Nombre de la hoja '4.Detalle PDC',
    de 10 hojas y ~44 columnas — usada por build_data.py para dar nombre a los
    CECOs sin PDC en ceco_nombres()).

Proporciona detección de cambios en Excel, confirmación interactiva y lectura
ultrarrápida (~5-10ms vs 2-5 segundos por lectura de Excel).
"""
from __future__ import annotations

import sys
import time
import json
from pathlib import Path
import polars as pl

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).parent.resolve()
ROOT = BASE.parent
DATOS = ROOT / "datos"

CAT_ESTRUCT_XLSX = DATOS / "Catálogo de estructura.xlsx"
CAT_CUENTAS_XLSX = DATOS / "Catálogo grupo cuentas.xlsx"

CAT_ESTRUCT_PQ = DATOS / "cat_estructura.parquet"
CAT_CUENTAS_PQ = DATOS / "cat_cuentas.parquet"

# Conciliación PDC: un .xlsx nuevo cada semana (mismo directorio, nombre con
# el número de semana) — no se puede invalidar por mtime del "mismo" archivo
# como los 2 catálogos de arriba, porque cada semana ES un archivo distinto.
# Se cachea junto con un .meta.json que guarda QUÉ archivo se convirtió, y se
# reconvierte solo cuando _concil_mas_reciente() apunta a uno distinto.
CAT_CONCIL_DIR = (Path.home() / "SharedDrive"
                   / "Archivos de Analyst - Planeación Financiera"
                   / "99. Catálogos" / "3. Conciliación PDC")
CECO_CONCIL_PQ = DATOS / "ceco_nombres_concil.parquet"
CECO_CONCIL_META = DATOS / "ceco_nombres_concil.meta.json"

# 12 cuentas especiales que conservan ambas combinaciones Grupo+PosPre (split 1:2)
CUENTAS_SPLIT_POSPRE = [
    "1140999006", "1140999025", "1140999041", "1150090107", "6010100047",
    "6010600004", "6200501009", "6200600017", "6210100006", "6210119001",
    "6250100004", "6250100014",
]


def _read_excel_safe(path: Path) -> pl.DataFrame:
    for kw in ({"infer_schema_length": 0},
               {"read_csv_options": {"infer_schema_length": 0}},
               {"read_options": {"infer_schema_length": 0}}):
        try:
            return pl.read_excel(path, **kw)
        except TypeError:
            continue
    return pl.read_excel(path)


# Columnas de agrupación donde el Excel mezcla mayúsculas/minúsculas en la misma
# categoría (p. ej. "Cerrados y Cancelados" vs "Cerrados y cancelados"). Sin
# unificar, el dashboard las trata como grupos distintos y las sumas se parten.
COLS_AGRUPACION_CANON = ["cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3",
                         "cat_Direccion_Division", "cat_Subtipo", "cat_Contraloria"]


def _unificar_variantes(df: pl.DataFrame, cols: list[str]) -> pl.DataFrame:
    """Para cada columna, toda variante que difiera solo en mayúsculas se reemplaza
    por la forma más frecuente de ese grupo (empate: orden alfabético)."""
    for col in cols:
        if col not in df.columns:
            continue
        conteo = (df.group_by(col).len()
                    .with_columns(pl.col(col).str.to_lowercase().alias("_k"))
                    .sort(["_k", "len", col], descending=[False, True, False]))
        canon = conteo.group_by("_k").agg(pl.col(col).first().alias("_canon"))
        cambios = conteo.join(canon, on="_k").filter(pl.col(col) != pl.col("_canon"))
        if cambios.height:
            mapa = dict(zip(cambios[col].to_list(), cambios["_canon"].to_list()))
            print(f"  [Catálogos] {col}: unifico {len(mapa)} variante(s) de mayúsculas: {mapa}", flush=True)
            df = df.with_columns(pl.col(col).replace_strict(mapa, default=pl.col(col)).alias(col))
    return df


def convertir_cat_estructura(verbose: bool = True) -> pl.DataFrame:
    """Lee Catálogo de estructura.xlsx, filtra a solo las columnas indispensables,
    limpia nulos/espacios, deduplica por ID_CENTRO_COSTOS y guarda en Parquet."""
    t0 = time.time()
    if not CAT_ESTRUCT_XLSX.exists():
        raise FileNotFoundError(f"No existe el archivo de catálogo: {CAT_ESTRUCT_XLSX}")

    if verbose:
        print(f"[Catálogos] Procesando {CAT_ESTRUCT_XLSX.name} -> Parquet...")

    est = _read_excel_safe(CAT_ESTRUCT_XLSX)

    col_ceco = "CECO_1" if "CECO_1" in est.columns else ("CECO" if "CECO" in est.columns else None)
    col_eco_pdv = "Eco PDV" if "Eco PDV" in est.columns else ("Eco" if "Eco" in est.columns else None)
    col_eco = "Eco" if "Eco" in est.columns else None

    # El Excel fuente trae la columna "Contraloria" DOS VECES (detalle, ej.
    # "Contraloria BAZ Propios", y resumen, ej. "Contraloria BAZ"). polars
    # renombra el duplicado con un sufijo automático según su POSICIÓN de
    # columna (ej. "Contraloria_5") — ese sufijo no lo pone ninguna persona
    # y puede correrse (a "_4", "_6"...) si el Excel gana o pierde columnas
    # antes de esa posición en una futura entrega del catálogo. Se detecta
    # por posición real en vez de hardcodear el sufijo, para no depender de
    # que "Contraloria_6" siga llamándose así (ya causó que cat_Contraloria_6
    # quedara silenciosamente vacío en un rebuild).
    _idx_contraloria = [i for i, c in enumerate(est.columns) if c == "Contraloria" or c.startswith("Contraloria_")]
    col_contraloria_detalle = est.columns[_idx_contraloria[0]] if len(_idx_contraloria) >= 1 else None
    col_contraloria_general = est.columns[_idx_contraloria[1]] if len(_idx_contraloria) >= 2 else None

    # Mapeo exacto de las ÚNICAS columnas requeridas para el dashboard
    cols_map = {
        col_ceco: "ID_CENTRO_COSTOS",
        "Dirección / División": "cat_Direccion_Division",
        "Subdirección / Territorio": "cat_Subdireccion_Territorio",
        "Subdirección / Zona": "cat_Subdireccion_Zona",
        "Subdirección / Región": "cat_Subdireccion_Region",
        "PDC": "cat_PDC",
        "Agrupa 1": "cat_Agrupa1",
        "Agrupa 2": "cat_Agrupa2",
        "Agrupa 3": "cat_Agrupa3",
        "Subtipo": "cat_Subtipo",
        "Prefijo": "cat_Prefijo",
        col_contraloria_detalle: "cat_Contraloria",
        col_contraloria_general: "cat_Contraloria_6",
        "Descripción": "Descripción",
    }

    exprs = []
    for orig, dest in cols_map.items():
        if orig and orig in est.columns:
            _col_clean = (pl.col(orig).cast(pl.Utf8, strict=False)
                          .fill_null("No Clasificado").str.strip_chars())
            exprs.append(
                pl.when(_col_clean == "").then(pl.lit("No Clasificado")).otherwise(_col_clean).alias(dest)
            )
        else:
            print(f"  [AVISO] Columna fuente para '{dest}' no encontrada en el Excel "
                  f"(se esperaba '{orig}') -> se llena con 'No Clasificado'.", flush=True)
            exprs.append(pl.lit("No Clasificado").alias(dest))

    # Identificadores numéricos
    if col_eco_pdv and col_eco_pdv in est.columns:
        exprs.append(
            pl.col(col_eco_pdv).cast(pl.Utf8, strict=False).str.strip_chars()
              .cast(pl.Int64, strict=False).fill_null(0).alias("ECO_PDV")
        )
    else:
        exprs.append(pl.lit(0).cast(pl.Int64).alias("ECO_PDV"))

    if col_eco and col_eco in est.columns:
        exprs.append(
            pl.col(col_eco).cast(pl.Utf8, strict=False).str.strip_chars()
              .cast(pl.Int64, strict=False).fill_null(0).alias("ECO")
        )
    else:
        exprs.append(pl.lit(0).cast(pl.Int64).alias("ECO"))

    est_clean = est.select(exprs)
    # Deduplicar por ID_CENTRO_COSTOS
    n0 = est_clean.height
    est_clean = est_clean.unique(subset=["ID_CENTRO_COSTOS"], keep="first")
    est_clean = _unificar_variantes(est_clean, COLS_AGRUPACION_CANON)

    tmp = CAT_ESTRUCT_PQ.with_suffix(".parquet.tmp")
    est_clean.write_parquet(tmp, compression="zstd")
    tmp.replace(CAT_ESTRUCT_PQ)

    if verbose:
        kb = CAT_ESTRUCT_PQ.stat().st_size / 1024
        print(f"  [OK] {CAT_ESTRUCT_PQ.name} generado: {est_clean.height:,} CECOs (de {n0:,} filas, {est.width} cols -> {est_clean.width} cols, {kb:.1f} KB en {time.time()-t0:.2f}s)")

    return est_clean


def convertir_cat_cuentas(verbose: bool = True) -> pl.DataFrame:
    """Lee Catálogo grupo cuentas.xlsx, filtra a solo las columnas indispensables,
    limpia nulos/espacios, aplica el split 1:2 de PosPre y guarda en Parquet."""
    t0 = time.time()
    if not CAT_CUENTAS_XLSX.exists():
        raise FileNotFoundError(f"No existe el archivo de catálogo: {CAT_CUENTAS_XLSX}")

    if verbose:
        print(f"[Catálogos] Procesando {CAT_CUENTAS_XLSX.name} -> Parquet...")

    cta = _read_excel_safe(CAT_CUENTAS_XLSX)

    col_cta = "Cta Mayor" if "Cta Mayor" in cta.columns else "ID_CONCEPTO_CUENTA_NIV3"

    cols_map = {
        col_cta: "ID_CONCEPTO_CUENTA_NIV3",
        "Grupo de Cuentas": "cat_Grupo_de_Cuentas",
        "Cuentas": "cat_Cuentas",
        "Agrupador Reales": "cat_Agrupador_Reales",
        "Denom.Cta. Mayor": "cat_CtaMayor_Nombre",
        "PosPre": "cat_PosPre",
        "Denom.PosPre": "cat_Denom_PosPre",
        "Orden presentación Gpo. Cta": "Orden_Presentacion_Gpo",
        "Responsable Interno": "Responsable_Interno",
        "Lider": "Lider",
    }

    exprs = []
    for orig, dest in cols_map.items():
        if orig and orig in cta.columns:
            _col_clean = (pl.col(orig).cast(pl.Utf8, strict=False)
                          .fill_null("No Clasificado").str.strip_chars())
            exprs.append(
                pl.when(_col_clean == "").then(pl.lit("No Clasificado")).otherwise(_col_clean).alias(dest)
            )
        else:
            exprs.append(pl.lit("No Clasificado").alias(dest))

    cta_clean = cta.select(exprs)

    # Split de cuentas especiales (12 cuentas PosPre con fan-out 1:2)
    especiales = (cta_clean.filter(pl.col("ID_CONCEPTO_CUENTA_NIV3").is_in(CUENTAS_SPLIT_POSPRE))
                           .with_columns(pl.lit(True).alias("_es_split")))
    resto = (cta_clean.filter(~pl.col("ID_CONCEPTO_CUENTA_NIV3").is_in(CUENTAS_SPLIT_POSPRE))
                      .unique(subset=["ID_CONCEPTO_CUENTA_NIV3"], keep="first")
                      .with_columns(pl.lit(False).alias("_es_split")))

    cta_final = pl.concat([especiales, resto], how="vertical_relaxed")
    cta_final = _unificar_variantes(cta_final, ["cat_CtaMayor_Nombre"])

    tmp = CAT_CUENTAS_PQ.with_suffix(".parquet.tmp")
    cta_final.write_parquet(tmp, compression="zstd")
    tmp.replace(CAT_CUENTAS_PQ)

    if verbose:
        kb = CAT_CUENTAS_PQ.stat().st_size / 1024
        print(f"  [OK] {CAT_CUENTAS_PQ.name} generado: {cta_final.height:,} filas ({cta.width} cols -> {cta_final.width} cols, {kb:.1f} KB en {time.time()-t0:.2f}s)")

    return cta_final


def _concil_mas_reciente() -> Path:
    """El Conciliación PDC con el número de semana (_S<n>) más alto en la carpeta
    — igual que semana.parquet_mas_reciente() hace con los parquets de datos."""
    import re
    cands = []
    if CAT_CONCIL_DIR.exists():
        for p in CAT_CONCIL_DIR.glob("Conciliación PDC_*_S*.xlsx"):
            m = re.search(r"_S(\d+)\.xlsx$", p.name)
            if m:
                cands.append((int(m.group(1)), p))
    if not cands:
        raise FileNotFoundError(
            f"no encontré ningún 'Conciliación PDC_*_S<sem>.xlsx' en {CAT_CONCIL_DIR}")
    return max(cands)[1]


def convertir_ceco_nombres_concil(verbose: bool = True) -> pl.DataFrame:
    """Lee SOLO la hoja '4.Detalle PDC' (columnas Eco/Nombre) del Conciliación PDC
    más reciente — de 10 hojas y ~44 columnas, ignora las otras 9 hojas por
    completo. Guarda en Parquet + un .meta.json con el archivo fuente usado,
    para poder detectar la semana siguiente sin reabrir el Excel."""
    t0 = time.time()
    fp = _concil_mas_reciente()
    if verbose:
        print(f"[Catálogos] Procesando {fp.name} (hoja '4.Detalle PDC') -> Parquet...")

    from openpyxl import load_workbook
    wb = load_workbook(fp, read_only=True, data_only=True)
    try:
        ws = wb["4.Detalle PDC"]
        ecos, nombres = [], []
        en_datos = False
        for row in ws.iter_rows(values_only=True):
            if not en_datos:
                en_datos = row[2] == "Eco" and row[3] == "Nombre"
                continue
            if row[2] is not None and row[3]:
                ecos.append(str(row[2]).strip())
                nombres.append(str(row[3]).strip())
    finally:
        wb.close()

    df = (pl.DataFrame({"eco": ecos, "nombre": nombres},
                        schema={"eco": pl.Utf8, "nombre": pl.Utf8})
            .unique(subset=["eco"], keep="first"))

    tmp = CECO_CONCIL_PQ.with_suffix(".parquet.tmp")
    df.write_parquet(tmp, compression="zstd")
    tmp.replace(CECO_CONCIL_PQ)
    CECO_CONCIL_META.write_text(
        json.dumps({"fuente": fp.name, "mtime": fp.stat().st_mtime}, ensure_ascii=False),
        encoding="utf-8")

    if verbose:
        kb = CECO_CONCIL_PQ.stat().st_size / 1024
        print(f"  [OK] {CECO_CONCIL_PQ.name} generado: {df.height:,} ecos con nombre "
              f"(fuente {fp.name}, {kb:.1f} KB en {time.time()-t0:.2f}s)")
    return df


def ceco_nombres_concil_desactualizado() -> bool:
    """True si _concil_mas_reciente() apunta a un archivo distinto (u otro mtime,
    editado in-place) al que se usó para generar el Parquet cacheado."""
    try:
        fp = _concil_mas_reciente()
    except FileNotFoundError:
        return False
    if not CECO_CONCIL_PQ.exists() or not CECO_CONCIL_META.exists():
        return True
    try:
        meta = json.loads(CECO_CONCIL_META.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return True
    return meta.get("fuente") != fp.name or meta.get("mtime") != fp.stat().st_mtime


def obtener_ceco_nombres_concil(forzar: bool = False) -> pl.DataFrame:
    """Carga ceco_nombres_concil.parquet. Se reconvierte solo (sin preguntar,
    a diferencia de los 2 catálogos maestros) cuando aparece un archivo de
    semana nuevo — mismo criterio automático que usa el resto del pipeline
    para detectar el parquet de datos más reciente (semana.parquet_mas_reciente)."""
    if forzar or ceco_nombres_concil_desactualizado():
        return convertir_ceco_nombres_concil()
    return pl.read_parquet(CECO_CONCIL_PQ)


def estructura_desactualizada() -> bool:
    if not CAT_ESTRUCT_XLSX.exists():
        return False
    if not CAT_ESTRUCT_PQ.exists():
        return True
    return CAT_ESTRUCT_XLSX.stat().st_mtime > CAT_ESTRUCT_PQ.stat().st_mtime


def cuentas_desactualizado() -> bool:
    if not CAT_CUENTAS_XLSX.exists():
        return False
    if not CAT_CUENTAS_PQ.exists():
        return True
    return CAT_CUENTAS_XLSX.stat().st_mtime > CAT_CUENTAS_PQ.stat().st_mtime


def obtener_cat_estructura(forzar: bool = False, interactivo: bool = False) -> pl.DataFrame:
    """Carga cat_estructura.parquet. Si no existe o cambió el Excel y el usuario lo confirma
    o forzar=True, lo regenera."""
    if forzar or not CAT_ESTRUCT_PQ.exists():
        return convertir_cat_estructura()

    if estructura_desactualizada():
        if interactivo:
            print("\n" + "!" * 70)
            print(f"AVISO: Se detectó que '{CAT_ESTRUCT_XLSX.name}' fue modificado.")
            resp = input("¿Deseas actualizar el Parquet de estructura optimizado? [s/N] >>> ").strip().casefold()
            if resp == "s":
                return convertir_cat_estructura()
            else:
                print(f"Conservando {CAT_ESTRUCT_PQ.name} actual.")
        else:
            return convertir_cat_estructura()

    return pl.read_parquet(CAT_ESTRUCT_PQ)


def obtener_cat_cuentas(forzar: bool = False, interactivo: bool = False) -> pl.DataFrame:
    """Carga cat_cuentas.parquet. Si no existe o cambió el Excel y el usuario lo confirma
    o forzar=True, lo regenera."""
    if forzar or not CAT_CUENTAS_PQ.exists():
        return convertir_cat_cuentas()

    if cuentas_desactualizado():
        if interactivo:
            print("\n" + "!" * 70)
            print(f"AVISO: Se detectó que '{CAT_CUENTAS_XLSX.name}' fue modificado.")
            resp = input("¿Deseas actualizar el Parquet de cuentas optimizado? [s/N] >>> ").strip().casefold()
            if resp == "s":
                return convertir_cat_cuentas()
            else:
                print(f"Conservando {CAT_CUENTAS_PQ.name} actual.")
        else:
            return convertir_cat_cuentas()

    return pl.read_parquet(CAT_CUENTAS_PQ)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Conversión y gestión de catálogos Parquet")
    parser.add_argument("--force", action="store_true", help="Fuerza la reconversión de Excel a Parquet")
    parser.add_argument("--check", action="store_true", help="Verifica si los Excel están más recientes que los Parquets")
    args = parser.parse_args()

    if args.check:
        cambio_est = estructura_desactualizada()
        cambio_cta = cuentas_desactualizado()
        cambio_concil = ceco_nombres_concil_desactualizado()
        print(f"Catálogo Estructura desactualizado:    {'SÍ' if cambio_est else 'NO'}")
        print(f"Catálogo Cuentas desactualizado:       {'SÍ' if cambio_cta else 'NO'}")
        print(f"Conciliación PDC (ceco_nombres) desactualizado: {'SÍ' if cambio_concil else 'NO'}")
    else:
        convertir_cat_estructura()
        convertir_cat_cuentas()
        convertir_ceco_nombres_concil()
