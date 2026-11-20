# -*- coding: utf-8 -*-
"""
build_series.py — Consolidado, aggs y rollups POR SERIE (R25, P26, R26) con caché incremental.

Por qué (medido en PLAN_AGGS_POR_SERIE_2026-10-22.md): en la actualización semanal solo cambia Real 2026
(27.7% de las filas). Antes el build recalculaba las tres series completas; ahora cada serie se reconstruye
solo si cambió su crudo o el catálogo, y los aggs/rollups salen de cachés por serie unidos con UNION ALL.

Estructura de aggs/series/:
  c_<S>.parquet            consolidado crudo de la serie (misma forma que _consolidado.parquet, sin `monto`)
  agg_<nombre>_<S>.parquet agg por serie (llaves + sem + m_cons + m_loc)
  roll_<nombre>_<S>.parquet rollup por serie (llaves + sem + Serie + monto_cons + monto_loc)
  _firmas.json             firma de cada caché (crudo, catálogo, CECOs excluidos, columnas, versión)

Una caché se reutiliza solo si TODAS sus firmas coinciden; si algo cambia se reconstruye (nunca se mezcla).
Un cambio de catálogo invalida las tres series (las columnas cat_* viven dentro del consolidado).

Regla heredada del build: un CECO "No Clasificado" cuya suma de monto_cons es 0 en TODAS las series se omite.
Esa regla cruza series, así que se calcula una vez sobre las series ya construidas (`ceco_excl`) y su firma
invalida los aggs/rollups por serie si el conjunto cambia (es raro: solo con CECOs sin clasificar).

Usa el módulo `build_data` solo a través de lo que recibe por parámetro (sin importarlo): evita ciclos.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

SERIES = ("R25", "P26", "R26")
VERSION = "series-v1"      # súbelo si cambias el SQL de este módulo: invalida todas las cachés


def _md5(*partes) -> str:
    h = hashlib.md5()
    for p in partes:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


class SeriesBuild:
    def __init__(self, out: Path, log):
        self.out = Path(out)
        self.dir = self.out / "series"
        self.firmas_path = self.dir / "_firmas.json"
        self.log = log
        self.firmas = self._leer()
        self.excl_sig = None
        self.ids_excl = 0

    # ------------------------------------------------------------------ estado
    def _leer(self) -> dict:
        try:
            d = json.loads(self.firmas_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        d.setdefault("series", {})
        d.setdefault("aggs", {})
        return d

    def _guardar(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.firmas_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.firmas, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.firmas_path)

    def ruta_c(self, s: str) -> Path:
        return self.dir / f"c_{s}.parquet"

    def existe(self) -> bool:
        return self.firmas_path.exists() and all(self.ruta_c(s).exists() for s in SERIES)

    def glob_c(self) -> str:
        return "[" + ", ".join(f"'{self.ruta_c(s).as_posix()}'" for s in SERIES) + "]"

    # ------------------------------------------------------------------ firmas
    @staticmethod
    def firma_crudo(con, ruta: str) -> dict:
        n, c, l = con.execute(f"""
            SELECT COUNT(*), ROUND(SUM(CAST("SUM_MONTO_MNX_CONS_(SUMA)" AS DOUBLE)), 2),
                   ROUND(SUM(CAST(COALESCE(TRY_CAST("Monto_Local" AS DOUBLE), "SUM_MONTO_MNX_CONS_(SUMA)") AS DOUBLE)), 2)
            FROM read_parquet('{ruta}')""").fetchone()
        return {"n": int(n), "c": c, "l": l}

    @staticmethod
    def firma_catalogo(rutas_catalogo: list[Path], ceco_dig: list[str]) -> str:
        """Huella del CONTENIDO de los catálogos (no de su fecha: reconvertir sin cambios no invalida nada)."""
        partes = []
        for r in rutas_catalogo:
            r = Path(r)
            partes.append(hashlib.md5(r.read_bytes()).hexdigest() if r.exists() else "falta")
        partes.append(_md5(*sorted(ceco_dig)))
        return _md5(VERSION, *partes)

    # ------------------------------------------------------------------ F1: consolidado por serie
    def construir(self, con, fuentes: dict, sel: str, cat_sig: str, forzar: bool = False) -> list[str]:
        """Reconstruye solo las series cuyo crudo o catálogo cambió. Requiere en `con` las tablas
        registradas `cat_c`, `cat_e` y `ceco_dig` (igual que build_consolidado). Devuelve las series cambiadas."""
        self.dir.mkdir(parents=True, exist_ok=True)
        cambiadas = []
        for s in SERIES:
            t0 = time.time()
            crudo = self.firma_crudo(con, fuentes[s])
            ant = self.firmas["series"].get(s, {})
            archivo = self.ruta_c(s)
            igual = (archivo.exists() and ant.get("crudo") == crudo and ant.get("cat") == cat_sig
                     and ant.get("version") == VERSION)
            if igual and not forzar:
                self.log(f"  serie {s}: sin cambios ({crudo['n']:,} filas del crudo) — se reutiliza c_{s}.parquet")
                continue
            motivo = ("primera vez" if not ant else "cambió el crudo" if ant.get("crudo") != crudo
                      else "cambió el catálogo" if ant.get("cat") != cat_sig else "forzado/versión")
            self.log(f"  serie {s}: reconstruyendo ({motivo})...")
            tmp = archivo.with_suffix(".parquet.tmp")
            con.execute(f"""
                COPY (
                  WITH du AS (
                    SELECT CAST(ID_CENTRO_COSTOS AS VARCHAR) AS ID_CENTRO_COSTOS,
                           CAST(ID_CONCEPTO_CUENTA_NIV3 AS VARCHAR) AS ID_CONCEPTO_CUENTA_NIV3,
                           SEMANA_ARG, '{s}' AS Serie,
                           CAST("SUM_MONTO_MNX_CONS_(SUMA)" AS DOUBLE) AS monto_cons,
                           CAST(COALESCE(TRY_CAST("Monto_Local" AS DOUBLE), "SUM_MONTO_MNX_CONS_(SUMA)") AS DOUBLE) AS monto_loc,
                           TRY_CAST(MES AS INTEGER) AS MES
                    FROM read_parquet('{fuentes[s]}')
                  )
                  SELECT CAST(d.SEMANA_ARG AS INTEGER) AS sem, d.Serie, d.monto_cons, d.monto_loc,
                         d.ID_CENTRO_COSTOS, d.ID_CONCEPTO_CUENTA_NIV3, COALESCE(e."ECO_PDV", 0) AS "ECO_PDV",
                         d.MES AS "MES", {sel},
                         CASE WHEN CAST(d.SEMANA_ARG AS INTEGER)<=13 THEN 'Q1'
                              WHEN CAST(d.SEMANA_ARG AS INTEGER)<=26 THEN 'Q2'
                              WHEN CAST(d.SEMANA_ARG AS INTEGER)<=39 THEN 'Q3'
                              ELSE 'Q4' END AS TRIMESTRE
                  FROM du d
                  LEFT JOIN cat_c c ON d.ID_CONCEPTO_CUENTA_NIV3 = c.ID_CONCEPTO_CUENTA_NIV3
                  LEFT JOIN cat_e e ON d.ID_CENTRO_COSTOS = e.ID_CENTRO_COSTOS
                  WHERE COALESCE(TRIM(d.ID_CENTRO_COSTOS) IN (SELECT ID_CENTRO_COSTOS FROM ceco_dig), FALSE) = FALSE
                ) TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
            tmp.replace(archivo)
            n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{archivo.as_posix()}')").fetchone()[0]
            self.firmas["series"][s] = {"crudo": crudo, "cat": cat_sig, "version": VERSION, "filas": int(n),
                                        "fuente": Path(fuentes[s]).name,
                                        "construido": datetime.now().isoformat(timespec="seconds")}
            self._guardar()
            self.log(f"  serie {s}: {n:,} filas, {archivo.stat().st_size/1e6:,.0f} MB en {time.time()-t0:,.1f} s")
            cambiadas.append(s)
        return cambiadas

    # ------------------------------------------------------------------ vistas y exclusión entre series
    def vistas(self, con) -> None:
        """consolidado_raw (unión de las series, con `monto`) y consolidado (sin los CECOs excluidos), ambas LAZY."""
        con.execute(f"CREATE OR REPLACE VIEW consolidado_raw AS "
                    f"SELECT *, monto_cons AS monto FROM read_parquet({self.glob_c()})")
        t0 = time.time()
        con.execute("""CREATE OR REPLACE TEMP TABLE ceco_excl AS
                       SELECT ID_CENTRO_COSTOS FROM consolidado_raw
                       WHERE cat_Direccion_Division = 'No Clasificado'
                       GROUP BY ID_CENTRO_COSTOS HAVING SUM(monto_cons) = 0""")
        ids = sorted(r[0] for r in con.execute("SELECT ID_CENTRO_COSTOS FROM ceco_excl").fetchall())
        self.ids_excl = len(ids)
        self.excl_sig = _md5(*ids)
        con.execute("CREATE OR REPLACE VIEW consolidado AS SELECT r.* FROM consolidado_raw r "
                    "WHERE r.ID_CENTRO_COSTOS NOT IN (SELECT ID_CENTRO_COSTOS FROM ceco_excl)")
        self.log(f"  CECOs 'No Clasificado' sin movimiento (excluidos): {self.ids_excl:,} ({time.time()-t0:,.1f} s)")

    def exportar_consolidado(self, con, destino: Path) -> None:
        """_consolidado.parquet (formato de siempre, sin filtrar: lo leen Cashi como respaldo y el menú por su fecha)."""
        t0 = time.time()
        tmp = Path(destino).with_suffix(".parquet.tmp")
        con.execute(f"COPY (SELECT * FROM read_parquet({self.glob_c()})) TO '{tmp.as_posix()}' "
                    f"(FORMAT PARQUET, COMPRESSION ZSTD)")
        tmp.replace(destino)
        self.log(f"  {Path(destino).name}: {Path(destino).stat().st_size/1e6:,.0f} MB en {time.time()-t0:,.1f} s")

    # ------------------------------------------------------------------ cachés por serie (F2 y F3)
    def _vigente(self, tipo: str, nombre: str, s: str, gcols: list[str]) -> bool:
        sig = self._sig(s, gcols)
        p = self.dir / f"{tipo}_{nombre}_{s}.parquet"
        return p.exists() and self.firmas["aggs"].get(f"{tipo}_{nombre}", {}).get(s) == sig

    def _sig(self, s: str, gcols: list[str]) -> str:
        ser = self.firmas["series"][s]
        return _md5(VERSION, ser["crudo"], ser["cat"], self.excl_sig, *gcols)

    def _marcar(self, tipo: str, nombre: str, s: str, gcols: list[str]) -> None:
        self.firmas["aggs"].setdefault(f"{tipo}_{nombre}", {})[s] = self._sig(s, gcols)
        self._guardar()

    def _cache_serie(self, con, tipo: str, nombre: str, s: str, gcols: list[str]) -> bool:
        """Devuelve True si hubo que reconstruirla."""
        if self._vigente(tipo, nombre, s, gcols):
            return False
        g = ", ".join(gcols)
        p = self.dir / f"{tipo}_{nombre}_{s}.parquet"
        tmp = p.with_suffix(".parquet.tmp")
        if tipo == "agg":
            sel = f"{g}, sem, SUM(monto_cons) AS m_cons, SUM(monto_loc) AS m_loc"
        else:
            sel = f"{g}, sem, '{s}' AS Serie, SUM(monto_cons) AS monto_cons, SUM(monto_loc) AS monto_loc"
        con.execute(f"""
            COPY (SELECT {sel} FROM read_parquet('{self.ruta_c(s).as_posix()}')
                  WHERE ID_CENTRO_COSTOS NOT IN (SELECT ID_CENTRO_COSTOS FROM ceco_excl)
                  GROUP BY {g}, sem) TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
        tmp.replace(p)
        self._marcar(tipo, nombre, s, gcols)
        return True

    def agg_ancho(self, con, nombre: str, gcols: list[str], destino: Path) -> tuple[int, list[str]]:
        """Agg ancho (Real_2025/Plan_2026/Real_2026, cons y loc) = unión de aggs por serie, sumados por llave.
        Igual que el GROUP BY de antes: una llave existe si tiene filas en alguna serie."""
        rehechas = [s for s in SERIES if self._cache_serie(con, "agg", nombre, s, gcols)]
        g = ", ".join(gcols)
        z = "CAST(0.0 AS DOUBLE)"
        partes = []
        cols = {"R25": ("m_cons", z, z, "m_loc", z, z), "P26": (z, "m_cons", z, z, "m_loc", z),
                "R26": (z, z, "m_cons", z, z, "m_loc")}
        for s in SERIES:
            c = cols[s]
            partes.append(f"SELECT {g}, sem, {c[0]} AS r25c, {c[1]} AS p26c, {c[2]} AS r26c, "
                          f"{c[3]} AS r25l, {c[4]} AS p26l, {c[5]} AS r26l "
                          f"FROM read_parquet('{(self.dir / f'agg_{nombre}_{s}.parquet').as_posix()}')")
        tmp = Path(str(destino) + ".tmp")
        con.execute(f"""
            COPY (SELECT {g}, sem,
                    COALESCE(SUM(r25c), 0.0) AS Real_2025_cons, COALESCE(SUM(p26c), 0.0) AS Plan_2026_cons,
                    COALESCE(SUM(r26c), 0.0) AS Real_2026_cons, COALESCE(SUM(r25l), 0.0) AS Real_2025_loc,
                    COALESCE(SUM(p26l), 0.0) AS Plan_2026_loc, COALESCE(SUM(r26l), 0.0) AS Real_2026_loc
                  FROM ({' UNION ALL '.join(partes)}) GROUP BY {g}, sem
            ) TO '{tmp.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)""")
        tmp.replace(destino)
        n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{Path(destino).as_posix()}')").fetchone()[0]
        return int(n), rehechas

    def rollup(self, con, nombre: str, gcols: list[str]) -> tuple[list[str], list[str]]:
        """Asegura los rollups por serie; devuelve (rutas de los 3 parquet, series rehechas)."""
        rehechas = [s for s in SERIES if self._cache_serie(con, "roll", nombre, s, gcols)]
        return [(self.dir / f"roll_{nombre}_{s}.parquet").as_posix() for s in SERIES], rehechas
