# -*- coding: utf-8 -*-
"""
dash_app/services/duckdb_service.py
Capa de conexión nativa y directa a aggs/dashboard.duckdb para Dash App.
Conecta exclusivamente a la base DuckDB real (~9.3 GB, 82.9M filas) sin mock ni fallbacks simulados.
"""
import duckdb
import os
import threading
from pathlib import Path
import pandas as pd
import numpy as np

import json
from collections import OrderedDict

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
# DASH_DUCKDB permite probar contra otra base (p. ej. dashboard.duckdb.nuevo) sin renombrar archivos.
DDB_PATH = Path(os.environ["DASH_DUCKDB"]) if os.environ.get("DASH_DUCKDB") else BASE_DIR / "aggs" / "dashboard.duckdb"
DDB_DIG_PATH = BASE_DIR / "aggs" / "digitales.duckdb"
CANALES = ("Fisico", "Digital", "Ambos")
_CACHE_MAX = 256


def _cacheado(fn):
    """Caché LRU compartido por todos los usuarios del proceso para las consultas pesadas (CECO/PDV/Sobregasto).
    Misma consulta pedida por varios usuarios a la vez = UNA sola ejecución (los demás esperan su resultado).
    La base es de solo lectura y solo cambia al regenerarla (con reinicio), así que no hay invalidación.
    Devuelve copia: los llamadores modifican los DataFrame."""
    def envoltura(self, *a, **kw):
        try:
            clave = (fn.__name__, json.dumps([a, kw], sort_keys=True, default=str))
        except Exception:
            return fn(self, *a, **kw)
        with self._cache_lock:
            if clave in self._cache_q:
                self._cache_q.move_to_end(clave)
                return self._cache_q[clave].copy()
            cerrojo = self._en_vuelo.setdefault(clave, threading.Lock())
        with cerrojo:
            with self._cache_lock:
                if clave in self._cache_q:
                    self._cache_q.move_to_end(clave)
                    return self._cache_q[clave].copy()
            df = fn(self, *a, **kw)
            with self._cache_lock:
                self._cache_q[clave] = df
                while len(self._cache_q) > _CACHE_MAX:
                    self._cache_q.popitem(last=False)
                self._en_vuelo.pop(clave, None)
            return df.copy()
    envoltura.__name__ = fn.__name__
    envoltura.__doc__ = fn.__doc__
    return envoltura


class DuckDBService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DuckDBService, cls).__new__(cls)
            cls._instance._init_con()
        return cls._instance

    def _init_con(self):
        if not DDB_PATH.exists():
            raise FileNotFoundError(f"No existe la base DuckDB en {DDB_PATH}. Corre generate_dashboard_db.py primero.")

        # Conexión persistente de solo lectura. NO se usa directamente para
        # queries: Dash corre un hilo por sesión de usuario en el mismo proceso,
        # y un objeto DuckDBPyConnection compartido no es seguro para queries
        # concurrentes desde varios hilos a la vez (con 50+ usuarios chocan y
        # la app se traba/corrompe). Cada query real usa self._cursor(), que
        # crea una conexión hija (con.cursor()) independiente por llamada —
        # patrón recomendado por DuckDB para uso multi-hilo.
        self.con = duckdb.connect(str(DDB_PATH), read_only=True)
        # Multiusuario (2026-10-21): DuckDB usa UN pool de hilos y UN buffer de memoria para todas las
        # conexiones/cursores del proceso. Sin tope, cada consulta pelea por todos los núcleos y una
        # consulta pesada de un usuario degrada a los demás. Ajustables sin tocar código:
        #   DUCKDB_THREADS (def. 8) · DUCKDB_MEMORY (def. 8GB)
        self.con.execute(f"SET threads={int(os.environ.get('DUCKDB_THREADS', '8'))}")
        self.con.execute(f"SET memory_limit='{os.environ.get('DUCKDB_MEMORY', '8GB')}'")
        if DDB_DIG_PATH.exists() and DDB_PATH.name == "dashboard.duckdb":
            self.con.execute(f"ATTACH '{DDB_DIG_PATH.as_posix()}' AS dg (READ_ONLY)")
        self._lock = threading.RLock()
        self._cache_lock = threading.Lock()
        self._cache_q = OrderedDict()
        self._en_vuelo = {}
        try:
            self._valid_cols = set(r[0] for r in self._cursor().execute("DESCRIBE consolidado").fetchall())
        except Exception:
            self._valid_cols = {
                "cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Direccion_Division", 
                "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona", "cat_Subdireccion_Region",
                "cat_PDC", "ID_CENTRO_COSTOS", "cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3",
                "cat_Subtipo", "cat_Contraloria", "cat_Contraloria_6", "MES", "ECO_PDV"
            }
        self._init_aggs()

    def _cursor(self):
        """Conexión hija independiente por query. RLock serializa la creación
        del cursor para prevenir el APPCRASH 0xc0000005 bajo carga concurrente
        (mismo patrón que el fix de 2026-09-08 en el proyecto Streamlit hermano)."""
        with self._lock:
            return self.con.cursor()

    # 2026-09-15: mismo principio que los preagregados de "Spend Legacy"
    # (Streamlit) -- tablas rollup de menor cardinalidad dentro del propio
    # dashboard.duckdb (generadas por codigo/build_data.py, etapa duckdb), para
    # las combinaciones de columnas más comunes al navegar los árboles de
    # PDV/CECO. Reducen entre 4x y 100x filas vs `consolidado` (83.3M filas),
    # medido antes de implementar. Si la agrupación/filtro pedido no cabe
    # completo en ninguna, se cae a `consolidado` -- mismo resultado siempre
    # (los rollups solo pre-suman por Serie/sem, no descartan filas).
    _ROLLUPS = {
        "rollup_pdv_general": frozenset({"cat_Agrupa1", "cat_Direccion_Division",
                                         "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona",
                                         "cat_Subdireccion_Region", "ECO_PDV", "MES"}),
        "rollup_pdv_general_gpo": frozenset({"cat_Agrupa1", "cat_Direccion_Division",
                                             "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona",
                                             "cat_Subdireccion_Region", "ECO_PDV",
                                             "cat_Grupo_de_Cuentas", "MES"}),
        "rollup_pdv_general_prefijo": frozenset({"cat_Agrupa1", "cat_Direccion_Division",
                                                 "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona",
                                                 "cat_Subdireccion_Region", "ECO_PDV",
                                                 "cat_Prefijo", "MES"}),
        "rollup_pdv_porcuenta": frozenset({"cat_Grupo_de_Cuentas", "cat_Cuentas",
                                           "cat_Agrupador_Reales", "ECO_PDV", "MES"}),
        "rollup_ceco_general": frozenset({"cat_Agrupa1", "cat_Direccion_Division",
                                          "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona",
                                          "cat_Subdireccion_Region", "ID_CENTRO_COSTOS", "MES"}),
        "rollup_ceco_general_gpo": frozenset({"cat_Agrupa1", "cat_Direccion_Division",
                                              "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona",
                                              "cat_Subdireccion_Region", "ID_CENTRO_COSTOS",
                                              "cat_Grupo_de_Cuentas", "MES"}),
        "rollup_mes_contraloria": frozenset({"cat_Grupo_de_Cuentas", "cat_Cuentas",
                                             "cat_Contraloria", "cat_Contraloria_6",
                                             "cat_Subtipo", "MES"}),
    }

    def _tabla_para(self, cols_necesarias: set) -> str:
        """Devuelve el rollup MÁS CHICO (menos columnas -> menor cardinalidad)
        cuyas columnas cubran `cols_necesarias` completas, o "consolidado" si
        ninguno alcanza. `cols_necesarias` = columnas de agrupación + columnas
        de todos los filtros activos en esta consulta (una columna que el
        rollup ya sumó no puede filtrarse ahí sin cambiar el resultado)."""
        candidatos = sorted(
            (nombre for nombre, disponibles in self._ROLLUPS.items()
             if cols_necesarias <= disponibles),
            key=lambda n: len(self._ROLLUPS[n])
        )
        return candidatos[0] if candidatos else "consolidado"

    @_cacheado
    def query_ceco_summary(self, cols, si=1, sf=36, filters=None, tipo_monto="Monto Constante",
                            exclude_zero_cols=None, text_search=None):
        """Consulta transaccional agregada directa sobre la tabla consolidado en DuckDB.
        Genérica para cualquier árbol (CECO, PDV, etc), no solo CECO pese al nombre —
        agrupa por la lista de columnas jerárquicas que se le pase.
        `exclude_zero_cols`: columnas que deben excluir el valor 0 (ej ECO_PDV=0 = sin
        PDV oficial asignado, regla siempre activa en el árbol).
        `text_search`: (columna, texto) para búsqueda parcial case-insensitive (ILIKE)."""
        m_col = "monto_loc" if tipo_monto == "Monto Local" else "monto_cons"
        group_cols = ", ".join(cols)

        where = [f"sem BETWEEN {si} AND {sf}"]
        valid_cols = {
            "cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Direccion_Division",
            "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona", "cat_Subdireccion_Region",
            "cat_PDC", "ID_CENTRO_COSTOS", "cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3",
            "cat_Subtipo", "cat_Contraloria", "cat_Contraloria_6", "MES", "ECO_PDV",
            "cat_Agrupador_Reales", "cat_Prefijo"
        }
        cols_necesarias = set(cols)
        if filters:
            for k, v in filters.items():
                if k in valid_cols and v:
                    vals = [str(x).replace("'", "''") for x in (v if isinstance(v, (list, tuple)) else [v])]
                    val_str = ", ".join(f"'{x}'" for x in vals)
                    where.append(f"{k} IN ({val_str})")
                    cols_necesarias.add(k)
        if exclude_zero_cols:
            for col in exclude_zero_cols:
                if col in valid_cols:
                    where.append(f"{col} != 0")
                    cols_necesarias.add(col)
        if text_search:
            col, txt = text_search
            if col in valid_cols and txt:
                cols_necesarias.add(col)
                txt_esc = str(txt).replace("'", "''")
                where.append(f"{col} ILIKE '%{txt_esc}%'")
        where_str = " AND ".join(where)
        tabla = self._tabla_para(cols_necesarias)

        query = f"""
            SELECT
                {group_cols},
                ROUND(SUM(CASE WHEN Serie = 'R25' THEN {m_col} ELSE 0 END), 2) AS Real_2025,
                ROUND(SUM(CASE WHEN Serie = 'P26' THEN {m_col} ELSE 0 END), 2) AS Plan_2026,
                ROUND(SUM(CASE WHEN (Serie = 'R26' AND sem <= {int(self.smr)}) OR (Serie = 'P26' AND sem > {int(self.smr)}) THEN {m_col} ELSE 0 END), 2) AS Real_2026
            FROM {self.fuente_cons(tabla, (filters or {}).get('canal'))}
            WHERE {where_str}
            GROUP BY {group_cols}
            ORDER BY Real_2026 DESC
        """
        return self._cursor().execute(query).fetchdf()

    def get_unique_values(self, column, limite=500):
        """Obtiene lista de valores únicos para filtros dinámicos"""
        if column not in self._valid_cols:
            return []
        try:
            # rollup más chico que traiga la columna (mismos valores, mucho menos filas que consolidado)
            return self._cursor().execute(f"SELECT DISTINCT {column} FROM {self._tabla_para({column})} WHERE {column} IS NOT NULL ORDER BY 1 LIMIT {int(limite)}").fetchdf()[column].tolist()
        except Exception:
            return []

    @_cacheado
    def query_tree_children(self, parent_col, parent_val, target_col, si=1, sf=36, filters=None, tipo_monto="Monto Constante"):
        """Drill-down ultra-rápido (<10ms) entre cualquier columna padre e hijo en DuckDB"""
        m_col = "monto_loc" if tipo_monto == "Monto Local" else "monto_cons"
        
        where = [f"sem BETWEEN {si} AND {sf}"]
        cols_necesarias = {target_col}
        if parent_col and parent_val:
            safe_val = str(parent_val).replace("'", "''")
            where.append(f"{parent_col} = '{safe_val}'")
            cols_necesarias.add(parent_col)

        if filters:
            valid_cols = self._valid_cols
            alias_map = {
                "fg_resp": "cat_Responsable_Grupo",
                "fg_gpo": "cat_Grupo_de_Cuentas",
                "fg_cta": "cat_Cuentas",
                "fg_subtipo": "cat_Subtipo",
                "eco_pdv_in": "ECO_PDV",
                "id_centro_costos_in": "ID_CENTRO_COSTOS"
            }
            for k, v in filters.items():
                col_name = alias_map.get(k, k)
                if v and col_name in valid_cols and col_name != parent_col:
                    vals = [str(x).replace("'", "''") for x in (v if isinstance(v, (list, tuple)) else [v])]
                    if vals:
                        val_str = ", ".join(f"'{x}'" for x in vals)
                        where.append(f"{col_name} IN ({val_str})")
                        cols_necesarias.add(col_name)
            # Búsqueda de PDV por nombre parcial (cat_PDC ILIKE)
            if filters.get("cat_pdc_query") and "cat_PDC" != parent_col:
                txt = str(filters["cat_pdc_query"]).replace("'", "''")
                where.append(f"cat_PDC ILIKE '%{txt}%'")
                cols_necesarias.add("cat_PDC")
            # Excluir ECO_PDV=0 (sin PDV oficial asignado) — regla siempre activa en el árbol
            if filters.get("eco_pdv_exclude_zero") and "ECO_PDV" != parent_col:
                where.append("ECO_PDV != 0")
                cols_necesarias.add("ECO_PDV")

        where_str = " AND ".join(where)
        tabla = self._tabla_para(cols_necesarias)

        query = f"""
            SELECT
                {target_col} AS Concepto,
                ROUND(SUM(CASE WHEN Serie = 'R25' THEN {m_col} ELSE 0 END), 2) AS Real_2025,
                ROUND(SUM(CASE WHEN Serie = 'P26' THEN {m_col} ELSE 0 END), 2) AS Plan_2026,
                ROUND(SUM(CASE WHEN (Serie = 'R26' AND sem <= {int(self.smr)}) OR (Serie = 'P26' AND sem > {int(self.smr)}) THEN {m_col} ELSE 0 END), 2) AS Real_2026
            FROM {self.fuente_cons(tabla, (filters or {}).get('canal'))}
            WHERE {where_str}
            GROUP BY {target_col}
            ORDER BY Real_2026 DESC
        """
        df = self._cursor().execute(query).fetchdf()
        if df.empty:
            return pd.DataFrame()

        tot_r25 = df["Real_2025"].sum()
        tot_p26 = df["Plan_2026"].sum()
        tot_r26 = df["Real_2026"].sum()

        df["Pct_r25"] = (df["Real_2025"] / tot_r25 * 100) if tot_r25 else 0.0
        df["Pct_p26"] = (df["Plan_2026"] / tot_p26 * 100) if tot_p26 else 0.0
        df["Pct_r26"] = (df["Real_2026"] / tot_r26 * 100) if tot_r26 else 0.0

        df["Vs_Plan_Abs"] = df["Real_2026"] - df["Plan_2026"]
        df["Vs_Plan_Pct"] = np.where(df["Plan_2026"] != 0, (df["Vs_Plan_Abs"] / df["Plan_2026"].abs()) * 100, 0.0)
        df["Vs_AA_Abs"] = df["Real_2026"] - df["Real_2025"]
        df["Vs_AA_Pct"] = np.where(df["Real_2025"] != 0, (df["Vs_AA_Abs"] / df["Real_2025"].abs()) * 100, 0.0)

        return df

    @staticmethod
    def _where_sobregasto(filters):
        """Condiciones WHERE (sin la de semana) de la pestaña Alertas de
        Sobregasto. Usado por query_sobregasto."""
        where = []
        if not filters:
            return where
        valid_cols = {
            "cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Direccion_Division",
            "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona", "cat_Subdireccion_Region",
            "cat_PDC", "ID_CENTRO_COSTOS", "cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3",
            "cat_Subtipo", "cat_Contraloria", "cat_Contraloria_6", "MES", "ECO_PDV"
        }        # Filtro global de responsable -> cat_Cuentas
        if filters.get("fg_resp"):
            ctas_resp = []
            for r in (filters["fg_resp"] if isinstance(filters["fg_resp"], list) else [filters["fg_resp"]]):
                from dash_app.services.data_service import DataService
                ctas_resp.extend(DataService().responsables_cta.get(r, []))
            if ctas_resp:
                val_str = ", ".join(f"'{str(x).replace("'", "''")}'" for x in set(ctas_resp))
                where.append(f"cat_Cuentas IN ({val_str})")

        # Filtro global de grupo
        if filters.get("fg_gpo"):
            gpos = filters["fg_gpo"] if isinstance(filters["fg_gpo"], list) else [filters["fg_gpo"]]
            val_str = ", ".join(f"'{str(x).replace("'", "''")}'" for x in gpos)
            where.append(f"cat_Grupo_de_Cuentas IN ({val_str})")

        # Filtro global de cuenta (2026-09-29: antes se ignoraba en esta pestaña)
        if filters.get("fg_cta"):
            ctas = filters["fg_cta"] if isinstance(filters["fg_cta"], list) else [filters["fg_cta"]]
            val_str = ", ".join(f"'{str(x).replace("'", "''")}'" for x in ctas)
            where.append(f"cat_Cuentas IN ({val_str})")

        # Filtro global de subtipo
        if filters.get("fg_subtipo"):
            subs = filters["fg_subtipo"] if isinstance(filters["fg_subtipo"], list) else [filters["fg_subtipo"]]
            val_str = ", ".join(f"'{str(x).replace("'", "''")}'" for x in subs)
            where.append(f"cat_Subtipo IN ({val_str})")

        # Cualquier otra columna válida
        for k, v in filters.items():
            if k in valid_cols and v:
                vals = [str(x).replace("'", "''") for x in (v if isinstance(v, (list, tuple)) else [v])]
                val_str = ", ".join(f"'{x}'" for x in vals)
                where.append(f"{k} IN ({val_str})")

        # Filtro local de división
        if filters.get("filtro_div_local"):
            divs = filters["filtro_div_local"]
            if isinstance(divs, list) and len(divs) > 0:
                val_str = ", ".join(f"'{str(x).replace("'", "''")}'" for x in divs if x != "(Todas)")
                if val_str:
                    where.append(f"cat_Direccion_Division IN ({val_str})")
            elif isinstance(divs, str) and divs != "(Todas)":
                div_val = divs.replace("'", "''")
                where.append(f"cat_Direccion_Division = '{div_val}'")
        return where

    @_cacheado
    def query_sobregasto(self, group_by="ceco", si=1, sf=36, filters=None, tipo_monto="Monto Constante", top_n=200):
        """Consulta ultra-rápida de CECOs, PDVs, Cuentas o Divisiones con sobregasto (Real 2026 > Plan 2026)"""
        m_col = "monto_loc" if tipo_monto == "Monto Local" else "monto_cons"

        if group_by == "ceco":
            group_col = "ID_CENTRO_COSTOS"
            extra_select = "cat_Direccion_Division AS Division"
            extra_group = "cat_Direccion_Division"
        elif group_by == "pdv":
            # ECO_PDV (no cat_PDC): cat_PDC viene vacío ('0'/'No Clasificado')
            # desde el 17-sep y colapsaba todos los PDV en 2 filas.
            group_col = "ECO_PDV"
            extra_select = "cat_Direccion_Division AS Division"
            extra_group = "cat_Direccion_Division"
        elif group_by == "division":
            group_col = "cat_Direccion_Division"
            extra_select = "'Corporativo' AS Division"
            extra_group = ""
        elif group_by == "cuenta":
            group_col = "cat_Cuentas"
            extra_select = "cat_Grupo_de_Cuentas AS Division"
            extra_group = "cat_Grupo_de_Cuentas"
        else:
            group_col = "cat_Grupo_de_Cuentas"
            extra_select = "cat_Direccion_Division AS Division"
            extra_group = "cat_Direccion_Division"

        where = [f"sem BETWEEN {si} AND {sf}"] + self._where_sobregasto(filters)
        where_str = " AND ".join(where)
        group_by_clause = f"{group_col}" + (f", {extra_group}" if extra_group else "")

        query = f"""
            SELECT
                {group_col} AS Concepto_Id,
                {extra_select},
                ROUND(SUM(CASE WHEN Serie = 'R25' THEN {m_col} ELSE 0 END), 2) AS Real_2025,
                ROUND(SUM(CASE WHEN Serie = 'P26' THEN {m_col} ELSE 0 END), 2) AS Plan_2026,
                ROUND(SUM(CASE WHEN Serie = 'R26' THEN {m_col} ELSE 0 END), 2) AS Real_2026,
                ROUND(SUM(CASE WHEN Serie = 'R26' THEN {m_col} ELSE 0 END) - SUM(CASE WHEN Serie = 'P26' THEN {m_col} ELSE 0 END), 2) AS Sobregasto_Abs,
                ROUND(
                    CASE WHEN SUM(CASE WHEN Serie = 'P26' THEN {m_col} ELSE 0 END) != 0 
                         THEN ((SUM(CASE WHEN Serie = 'R26' THEN {m_col} ELSE 0 END) - SUM(CASE WHEN Serie = 'P26' THEN {m_col} ELSE 0 END)) / ABS(SUM(CASE WHEN Serie = 'P26' THEN {m_col} ELSE 0 END))) * 100 
                         ELSE 0 END, 2) AS Sobregasto_Pct
            FROM {self.fuente_cons('consolidado', (filters or {}).get('canal'))}
            WHERE {where_str}
            GROUP BY {group_by_clause}
            HAVING (SUM(CASE WHEN Serie = 'R26' THEN {m_col} ELSE 0 END) - SUM(CASE WHEN Serie = 'P26' THEN {m_col} ELSE 0 END)) > 0
            ORDER BY Sobregasto_Abs DESC
            LIMIT {top_n}
        """
        return self._cursor().execute(query).fetchdf()

    # ======================================================================================================
    # Fase 2 (2026-10-21): los aggs se sirven desde dashboard.duckdb (tablas agg_<nombre> y dig_<nombre>, que
    # copia build_data.build_dashboard_db). Un solo motor para todo el tablero; los valores de filtros viajan
    # como PARÁMETROS (nada de texto pegado al SQL). Misma semántica que el antiguo DataService con Polars.
    # ======================================================================================================
    def _init_aggs(self):
        cur = self._cursor()
        self._tablas = {r[0] for r in cur.execute("SELECT table_name FROM duckdb_tables() WHERE database_name <> 'dg'").fetchall()}
        # Canal Digital (build_digitales_db.py): consolidado_dig + dig_* faltantes, en un archivo aparte y chico.
        self._dig_db = {r[0] for r in cur.execute("SELECT table_name FROM duckdb_tables() WHERE database_name = 'dg'").fetchall()}
        self._cols_agg = {}
        self._cols_tabla = {}
        self._cols_dig = ({r[0] for r in cur.execute("DESCRIBE dg.consolidado_dig").fetchall()}
                          if "consolidado_dig" in self._dig_db else set())
        self.smr, self.smp = 42, 53
        try:
            meta = json.loads((BASE_DIR / "aggs" / "_meta.json").read_text(encoding="utf-8"))
            self.smr, self.smp = int(meta["sem_max_real"]), int(meta["sem_max_plan"])
        except Exception:
            pass
        self.tiene_aggs = any(t.startswith("agg_") for t in self._tablas)

    def tiene_agg(self, nombre):
        return f"agg_{nombre}" in self._tablas

    def columnas_agg(self, nombre):
        if nombre not in self._cols_agg:
            if not self.tiene_agg(nombre):
                raise FileNotFoundError(f"dashboard.duckdb no trae agg_{nombre}: regenera con MENU_DASH opción 6.")
            self._cols_agg[nombre] = {r[0] for r in self._cursor().execute(f"DESCRIBE agg_{nombre}").fetchall()}
        return self._cols_agg[nombre]

    def fuente_agg(self, nombre, canal="Fisico"):
        """FROM del agg según el canal del filtro global: Fisico (agg_), Digital (dig_; vacío si no existe) o Ambos."""
        fis = f"agg_{nombre}"
        dig = f"dig_{nombre}" if f"dig_{nombre}" in self._tablas else (f"dg.dig_{nombre}" if f"dig_{nombre}" in self._dig_db else None)
        self.columnas_agg(nombre)   # valida que exista
        canal = canal if canal in CANALES else "Fisico"
        if canal == "Digital":
            return dig or f"(SELECT * FROM {fis} WHERE FALSE)"
        if canal == "Ambos" and dig:
            return f"(SELECT * FROM {fis} UNION ALL BY NAME SELECT * FROM {dig})"
        return fis

    def fuente_cons(self, tabla, canal="Fisico"):
        """FROM de las consultas transaccionales (CECO/PDV/Tabla Dinámica/Sobregasto) según el canal: `tabla` es el
        consolidado o su rollup; Digital lee consolidado_dig; Ambos une ambos con las columnas de `tabla`."""
        canal = canal if canal in CANALES else "Fisico"
        if canal == "Fisico" or "consolidado_dig" not in self._dig_db:
            return tabla if canal != "Digital" else f"(SELECT * FROM {tabla} WHERE FALSE)"
        if canal == "Digital":
            return "dg.consolidado_dig"
        if tabla not in self._cols_tabla:
            self._cols_tabla[tabla] = [r[0] for r in self._cursor().execute(f"DESCRIBE {tabla}").fetchall()]
        cols = [c for c in self._cols_tabla[tabla] if c in self._cols_dig]
        sel = ", ".join(cols)
        return f"(SELECT {sel} FROM {tabla} UNION ALL SELECT {sel} FROM dg.consolidado_dig)"

    @staticmethod
    def canal_de(filters):
        c = (filters or {}).get("canal") or "Fisico"
        return c if c in CANALES else "Fisico"

    def where_agg(self, cols, filters=None, extra=None, sem=True):
        """(lista de condiciones, parámetros) con los filtros globales + `extra` {columna: valores} + búsquedas.
        Cada filtro se aplica solo si el agg tiene la columna (igual que antes), con una excepción deliberada:
        Cuenta sin cat_Cuentas se traduce a IDs (fg_cta_ids) y, si no hay, no devuelve filas (no 'sin filtro')."""
        f = filters or {}
        w, p = [], []

        def en(col, vals):
            w.append(f"list_contains(?, CAST({col} AS VARCHAR))")
            p.append([str(v) for v in vals])

        if sem and "sem" in cols:
            w.append("sem BETWEEN ? AND ?")
            p += [int(f.get("si", 1)), int(f.get("sf", self.smr))]
        if f.get("fg_gpo") and "cat_Grupo_de_Cuentas" in cols:
            en("cat_Grupo_de_Cuentas", f["fg_gpo"])
        if f.get("fg_cta"):
            if "cat_Cuentas" in cols:
                en("cat_Cuentas", f["fg_cta"])
            elif "ID_CONCEPTO_CUENTA_NIV3" in cols:
                ids = f.get("fg_cta_ids") or []
                if ids:
                    en("ID_CONCEPTO_CUENTA_NIV3", ids)
                else:
                    w.append("FALSE")
        if f.get("fg_subtipo") and "cat_Subtipo" in cols:
            en("cat_Subtipo", f["fg_subtipo"])
        for col, vals in (extra or {}).items():
            if not vals:
                continue
            if col == "cat_cuenta_query":
                if "cat_Cuentas" in cols:
                    w.append("contains(lower(cat_Cuentas), ?)")
                    p.append(str(vals).lower())
            elif col == "cat_pdc_query":
                if "cat_PDC" in cols:
                    w.append("contains(lower(cat_PDC), ?)")
                    p.append(str(vals).lower())
            elif col in cols:
                en(col, vals if isinstance(vals, (list, tuple, set)) else [vals])
        return w, p

    def medidas_agg(self, cols, tipo_monto="Monto Constante"):
        suf = "loc" if tipo_monto == "Monto Local" else "cons"

        def pick(base):
            for c in (f"{base}_{suf}", f"{base}_cons", base):
                if c in cols:
                    return c
            return None
        return pick("Real_2025"), pick("Plan_2026"), pick("Real_2026")

    def agg(self, nombre, group_cols=(), filters=None, tipo_monto="Monto Constante", extra=None,
            sem=True, hibrido=True, canal=None):
        """Real_2025 / Plan_2026 / Real_2026 por `group_cols` sobre agg_<nombre>, con todos los filtros.
        hibrido=True: Real_2026 = Real hasta la semana de corte y Plan después (regla del tablero);
        False: Real_2026 crudo (p. ej. para series semanales, que lo resuelven aparte)."""
        cols = self.columnas_agg(nombre)
        src = self.fuente_agg(nombre, canal or self.canal_de(filters))
        r25, p26, r26 = self.medidas_agg(cols, tipo_monto)
        if hibrido and "sem" in cols and p26 and r26:
            r26_sql = f"SUM(CASE WHEN sem <= {int(self.smr)} THEN {r26} ELSE {p26} END)"
        else:
            r26_sql = f"SUM({r26})" if r26 else "CAST(0 AS DOUBLE)"
        g = ", ".join(group_cols)
        w, p = self.where_agg(cols, filters, extra, sem=sem)
        sql = (f"SELECT {g + ', ' if g else ''}"
               f"{'SUM(' + r25 + ')' if r25 else 'CAST(0 AS DOUBLE)'} AS Real_2025, "
               f"{'SUM(' + p26 + ')' if p26 else 'CAST(0 AS DOUBLE)'} AS Plan_2026, {r26_sql} AS Real_2026 "
               f"FROM {src}" + (" WHERE " + " AND ".join(w) if w else "") + (f" GROUP BY {g}" if g else ""))
        return self._cursor().execute(sql, p).fetchdf()

    def distintos_agg(self, nombre, col, filters=None, extra=None, sem=False, canal=None):
        """Valores distintos de una columna del agg (listas de opciones, conteos)."""
        cols = self.columnas_agg(nombre)
        if col not in cols:
            return []
        src = self.fuente_agg(nombre, canal or self.canal_de(filters))
        w, p = self.where_agg(cols, filters, extra, sem=sem)
        sql = f"SELECT DISTINCT {col} FROM {src}" + (" WHERE " + " AND ".join(w) if w else "")
        return [r[0] for r in self._cursor().execute(sql, p).fetchall()]

    def valores_distintos(self, columna):
        """Valores distintos de una columna de `consolidado`, leídos del rollup más chico que la trae."""
        if columna not in self._valid_cols:
            return []
        tabla = self._tabla_para({columna})
        return [r[0] for r in self._cursor().execute(f"SELECT DISTINCT {columna} FROM {tabla}").fetchall()]

    def sql_agg(self, sql, params=None):
        """Consulta libre (ya con parámetros) en cursor propio; para casos que agg() no cubre (forecast, etc.)."""
        return self._cursor().execute(sql, params or []).fetchdf()
