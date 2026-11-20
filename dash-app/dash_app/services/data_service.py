# -*- coding: utf-8 -*-
"""
dash_app/services/data_service.py
Servicio central de datos y cálculos para Dash App: consultas DuckDB sobre aggs/dashboard.duckdb + JSONs reales.
Sin datos simulados ni fallbacks mock: lee y procesa datos 100% reales.
"""
from pathlib import Path
import json
import re
import threading
import unicodedata
from functools import lru_cache
import pandas as pd
import numpy as np

from dash_app.services.duckdb_service import DuckDBService

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
AGGS_DIR = BASE_DIR / "aggs"
AGGS_DIG_DIR = BASE_DIR / "aggs_digitales"   # lo genera codigo/build_digitales.py
CANALES = ("Fisico", "Digital", "Ambos")


def _normalizar_orden(s) -> str:
    """Sin acentos ni mayúsculas, para ordenar alfabéticamente como en un índice
    (mismo criterio que `_normalizar_texto` del Streamlit)."""
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii").strip().lower()

# 2026-10-09: paleta oficial copiada de "<LEGACY_PROJECT>"
# (app.py, COLOR): Real 2025 gris, Real 2026 rojo claro, Plan 2026 rojo Aurora,
# Forecast gris azulado. Espejo de las variables CSS en custom.css (--serie-*).
COLOR = {
    "Real_2025": "#ccd1d9",
    "Real_2026": "#f1948a",
    "Plan_2026": "#a93226",
    "Forecast_Cierre": "#8a93a0",
}

def M(v):
    if v is None or pd.isna(v):
        return "—"
    return f"{v / 1e6:,.1f} MDP"

def MC(v):
    if v is None or pd.isna(v):
        return "—"
    val = v / 1e6
    if val > 0:
        return f"({val:,.1f} MDP)"
    return f"{abs(val):,.1f} MDP"

def P(v):
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.1f}%"

class DataService:
    _instance = None
    aggs_dir = AGGS_DIR   # las subclases (p. ej. canales digitales) apuntan a otra carpeta de aggs

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DataService, cls).__new__(cls)
            cls._instance._init_service()
        return cls._instance

    def _init_service(self):
        meta_p = self.aggs_dir / "_meta.json"
        if not meta_p.exists():
            raise FileNotFoundError(f"Falta el archivo de metadatos en {meta_p}")

        self.meta = json.loads(meta_p.read_text(encoding="utf-8"))
        self.smr = int(self.meta.get("sem_max_real", 36))
        self.smp = int(self.meta.get("sem_max_plan", 53))

        # Cargar catálogos JSON
        def _leer_json(fn):
            p = self.aggs_dir / fn
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

        self.orden_gpo = _leer_json("orden_gpo.json")
        self.responsables_gpo = _leer_json("responsables_gpo.json")
        self.responsables_cta = _leer_json("responsables_cta.json")
        self.ceco_nombres = _leer_json("ceco_nombres.json")
        self.pdv_nombres = _leer_json("pdv_nombres.json")
        self.riesgos = _leer_json("riesgos.json")

        self._cache = {}
        # 2026-09-15: `DataService` es singleton por proceso y waitress sirve
        # esta app con 32 hilos reales (threaded=True) -- sin lock, dos
        # usuarios pidiendo la misma vista a la vez entraban al check-then-
        # write de `self._cache` y al `.collect()` de Polars concurrentemente
        # (motor propio multihilo de Polars, reentrante desde varios hilos
        # Python a la vez sin sincronización) -- mismo tipo de bug que
        # `bitacora.py` en el proyecto Streamlit hermano, causa más probable
        # de los crashes nativos 0xc0000005 vistos en _polars_runtime.pyd.
        # Solo protege el dict; el cómputo (.collect()) corre SIN el lock
        # para no serializar el trabajo pesado entre hilos -- en un cache-miss
        # concurrente dos hilos pueden recalcular lo mismo una vez (se pisan
        # al escribir, gana el último) pero eso es solo trabajo duplicado, no
        # corrupción. No cambia ningún resultado calculado.
        self._cache_lock = threading.Lock()
        self._init_filter_options()

    def _init_filter_options(self):
        # 2026-10-21 (fase 2): todo sale de dashboard.duckdb (tablas agg_*), sin Polars ni parquets sueltos.
        self.ddb = DuckDBService()
        d = self.ddb
        if not d.tiene_aggs:
            raise FileNotFoundError("aggs/dashboard.duckdb no trae las tablas agg_*: regenera con MENU_DASH opción 6 "
                                    "(generate_dashboard_db.py).")
        self.responsables = sorted(
            [r for r in self.responsables_gpo.keys() if str(r).strip().lower() != "revisar"],
            key=lambda r: (1 if r == "Otro" else 0, r)
        )
        _no = ("(Sin dato)", "Sin dato", "No Clasificado", "nan")
        self.grupos_cuentas = sorted(
            [x for x in d.distintos_agg("grupo_cuentas", "cat_Grupo_de_Cuentas") if x and str(x) not in _no],
            key=lambda x: (self.orden_gpo.get(x, 999), str(x)))
        self.grupos = self.grupos_cuentas
        self.cuentas = sorted([x for x in d.distintos_agg("cuentas", "cat_Cuentas") if x and str(x) not in _no])
        self.subtipos = sorted([x for x in d.distintos_agg("global", "cat_Subtipo")
                                if x and str(x) not in ("(Sin dato)", "Sin dato", "nan")])
        a1 = [x for x in d.distintos_agg("cuentas", "cat_Agrupa1") if x and str(x) not in ("(Sin dato)", "Sin dato", "nan")]
        self.agrupa1 = sorted(a1) or ["Corporativo", "Geografía", "Supervisión"]
        # Cuenta -> su Grupo de Cuentas, para ordenar árboles que empiezan directo en Cuenta (ver orden_cuenta_por_gpo_key).
        pares = d.sql_agg("SELECT DISTINCT cat_Cuentas, cat_Grupo_de_Cuentas FROM agg_cuentas")
        self.cta_a_gpo = dict(zip(pares["cat_Cuentas"].tolist(), pares["cat_Grupo_de_Cuentas"].tolist()))

    def agg(self, nombre, group_cols=(), filters=None, tipo_monto=None, extra=None, **kw):
        """Real_2025/Plan_2026/Real_2026 por `group_cols` sobre agg_<nombre> con los filtros globales
        (ver DuckDBService.agg). Reemplaza a scan()/_aplicar_filtros_lazy() de la versión con Polars."""
        if tipo_monto is None:
            tipo_monto = (filters or {}).get("tipo_monto", "Monto Constante")
        return self.ddb.agg(nombre, group_cols, filters, tipo_monto, extra=extra, **kw)

    @staticmethod
    def canal_de(filters):
        """Canal activo del filtro global: 'Fisico' (default), 'Digital' o 'Ambos'."""
        c = (filters or {}).get("canal") or "Fisico"
        return c if c in CANALES else "Fisico"

    def orden_gpo_key(self, nombre_grupo):
        """Clave de sort para Grupo de Cuentas: primero el nº de
        "Orden presentación Gpo. Cta" del catálogo (orden_gpo.json); grupos
        sin orden asignado quedan al final."""
        return (self.orden_gpo.get(nombre_grupo, 999), str(nombre_grupo))

    def orden_cuenta_por_gpo_key(self, nombre_cuenta):
        """Clave de sort para una Cuenta usando el orden de SU Grupo de
        Cuentas — para árboles cuyo primer nivel es Cuenta directo, no Grupo
        (ej. Contraloria en dirección "Cuenta -> Contraloria")."""
        grupo = self.cta_a_gpo.get(nombre_cuenta)
        return self.orden_gpo_key(grupo) + (_normalizar_orden(nombre_cuenta), str(nombre_cuenta))

    def ordenar_hijos(self, df, target_col):
        """2026-09-24 (paridad con _ordenar_resultado del Streamlit): Grupos de Cuentas por
        "Orden presentación Gpo. Cta" y Cuentas alfabéticas (sin acentos ni mayúsculas)
        dentro de su Grupo, en vez de |Real_2026| desc. Otras dimensiones no se tocan."""
        if df is None or df.empty or "Concepto" not in df.columns:
            return df
        if target_col == "cat_Cuentas":
            clave = df["Concepto"].map(self.orden_cuenta_por_gpo_key)
        elif target_col == "cat_Grupo_de_Cuentas":
            clave = df["Concepto"].map(self.orden_gpo_key)
        else:
            return df
        return df.reindex(clave.sort_values(kind="stable").index).reset_index(drop=True)

    def _forecast_cruzado(self, source, filters, tipo_monto, group_col=None):
        """Forecast Finanzas (Excel -> agg_forecast_cuenta, ya prorrateado por semana en el build) cruzado contra el
        universo de cuentas activo en `source` bajo los mismos filtros que el KPI/tabla. El Forecast SIEMPRE es un
        pronóstico independiente por cuenta, nunca el Plan (reportado 2026-09-17). Cuentas del Excel con Forecast pero
        SIN Real/Plan en global (p. ej. 6245010008) se suman igual, respetando Grupo/Cuenta (2026-09-21).
        Devuelve DataFrame (group_col..., Forecast) o, sin group_col, un float."""
        d = self.ddb
        nombre = Path(str(source)).stem
        canal = d.canal_de(filters)
        gcs = [group_col] if isinstance(group_col, str) else list(group_col or [])
        scols = d.columnas_agg(nombre)
        if "ID_CONCEPTO_CUENTA_NIV3" not in scols:
            return pd.DataFrame({**{c: [] for c in gcs}, "Forecast": []}) if gcs else 0.0

        sel = ["ID_CONCEPTO_CUENTA_NIV3"] + gcs
        sel_sql = ", ".join(sel)
        f = filters or {}
        fcols = d.columnas_agg("forecast_cuenta")
        fsrc = d.fuente_agg("forecast_cuenta", canal)
        w_u, p_u = d.where_agg(scols, filters)                    # semanas + Grupo/Cuenta/Subtipo, como antes
        cte = [f"univ AS (SELECT DISTINCT {sel_sql} FROM {d.fuente_agg(nombre, canal)}"
               + (" WHERE " + " AND ".join(w_u) if w_u else "") + ")"]
        params = list(p_u)
        base = "univ"
        if all(c in fcols for c in sel):
            conds = [f"ID_CONCEPTO_CUENTA_NIV3 NOT IN (SELECT DISTINCT ID_CONCEPTO_CUENTA_NIV3 FROM {d.fuente_agg('global', canal)})"]
            if f.get("fg_gpo") and "cat_Grupo_de_Cuentas" in fcols:
                conds.append("list_contains(?, cat_Grupo_de_Cuentas)")
                params.append([str(x) for x in f["fg_gpo"]])
            if f.get("fg_cta") and "cat_Cuentas" in fcols:
                conds.append("list_contains(?, cat_Cuentas)")
                params.append([str(x) for x in f["fg_cta"]])
            cte.append(f"h AS (SELECT DISTINCT {sel_sql} FROM {fsrc} WHERE " + " AND ".join(conds) + ")")
            base = "(SELECT * FROM univ UNION SELECT * FROM h)"
        w_f, p_f = d.where_agg(fcols, filters)
        cte.append(f"fcta AS (SELECT ID_CONCEPTO_CUENTA_NIV3, SUM(Forecast) AS Forecast FROM {fsrc}"
                   + (" WHERE " + " AND ".join(w_f) if w_f else "") + " GROUP BY 1)")
        params += p_f
        if gcs:
            g = ", ".join(gcs)
            sql = (f"WITH {', '.join(cte)} SELECT {g}, SUM(fcta.Forecast) AS Forecast "
                   f"FROM {base} b JOIN fcta USING (ID_CONCEPTO_CUENTA_NIV3) GROUP BY {g}")
            return d.sql_agg(sql, params)
        sql = (f"WITH {', '.join(cte)} SELECT SUM(fcta.Forecast) AS Forecast "
               f"FROM {base} b JOIN fcta USING (ID_CONCEPTO_CUENTA_NIV3)")
        v = d.sql_agg(sql, params)["Forecast"]
        return float(v.iloc[0]) if len(v) and pd.notna(v.iloc[0]) else 0.0

    def _fcst_srv(self, filters, group_col, tipo_monto="Monto Constante"):
        """FCST PLAN (tabla FCST del servidor; Real sem 1-40 + FCST sem 41+, como en Spend Legacy) por Grupo o
        Cuenta, sumado en el rango de semanas del filtro. Sale de agg_fcst_srv_cuenta (build_data, etapa fcst_srv).
        DataFrame (group_col..., FCST); vacío si el canal no trae la tabla."""
        d = self.ddb
        gcs = [group_col] if isinstance(group_col, str) else list(group_col)
        vacio = pd.DataFrame({**{c: pd.Series(dtype=object) for c in gcs}, "FCST": pd.Series(dtype=float)})
        if not d.tiene_agg("fcst_srv_cuenta"):
            return vacio
        cols = d.columnas_agg("fcst_srv_cuenta")
        if not all(c in cols for c in gcs):
            return vacio
        med = "FCST_loc" if tipo_monto == "Monto Local" else "FCST_cons"
        w, p = d.where_agg(cols, filters)
        g = ", ".join(gcs)
        sql = (f"SELECT {g}, SUM({med}) AS FCST FROM {d.fuente_agg('fcst_srv_cuenta', d.canal_de(filters))}"
               + (" WHERE " + " AND ".join(w) if w else "") + f" GROUP BY {g}")
        return d.sql_agg(sql, p)

    def _con_fcst_srv(self, df, fs, key):
        """Agrega FCST | Vs_FCST | Vs_FCST_Pct (Real 2026 vs FCST) al DataFrame `df` cruzando por `key`."""
        fs = fs.rename(columns={key: "Concepto"}) if len(fs) else pd.DataFrame(columns=["Concepto", "FCST"])
        df = df.merge(fs, on="Concepto", how="left")
        df["FCST"] = df["FCST"].fillna(0.0).astype(float)
        df["Vs_FCST"] = df["Real_2026"] - df["FCST"]
        df["Vs_FCST_Pct"] = np.where(df["FCST"] != 0, (df["Vs_FCST"] / df["FCST"].abs()) * 100, 0.0)
        return df

    def compute_forecast_weekly(self, filters=None):
        """Forecast Finanzas por semana, con los filtros de Grupo/Cuenta/Subtipo del filtro global (sin filtrar semanas).
        Incluye las cuentas sin Real ni Plan. Sirve para Temporalidad."""
        d = self.ddb
        cols = d.columnas_agg("forecast_cuenta")
        w, p = d.where_agg(cols, filters, sem=False)
        sql = (f"SELECT sem, SUM(Forecast) AS Forecast FROM {d.fuente_agg('forecast_cuenta', d.canal_de(filters))}"
               + (" WHERE " + " AND ".join(w) if w else "") + " GROUP BY sem ORDER BY sem")
        return d.sql_agg(sql, p)

    def _filter_cache_key(self, prefix, filters):
        if not filters:
            return f"{prefix}_default"
        try:
            items = []
            for k in sorted(filters.keys()):
                v = filters[k]
                if isinstance(v, (list, tuple)):
                    items.append((k, tuple(sorted(v))))
                else:
                    items.append((k, v))
            return f"{prefix}_{hash(tuple(items))}"
        except Exception:
            return f"{prefix}_{str(filters)}"

    def compute_summary_kpis(self, filters=None):
        cache_key = self._filter_cache_key("kpis", filters)
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
        res = self.agg("global", [], filters, tm)

        def _f(x):
            return 0.0 if x is None or pd.isna(x) else float(x)

        r25, p26, r26 = (_f(res["Real_2025"].iloc[0]), _f(res["Plan_2026"].iloc[0]), _f(res["Real_2026"].iloc[0])) if len(res) else (0.0, 0.0, 0.0)
        fc = self._forecast_cruzado("global", filters, tm)

        vs_aa = r26 - r25
        vs_plan = r26 - p26
        vs_fc = r26 - fc

        out = {
            "Real_2025": r25,
            "Plan_2026": p26,
            "Real_2026": r26,
            "Forecast_Cierre": fc,
            "Vs_AA_Abs": vs_aa,
            "Vs_AA_Pct": (vs_aa / abs(r25) * 100) if r25 else 0.0,
            "Vs_Plan_Abs": vs_plan,
            "Vs_Plan_Pct": (vs_plan / abs(p26) * 100) if p26 else 0.0,
            "Vs_Forecast_Abs": vs_fc,
            "Vs_Forecast_Pct": (vs_fc / abs(fc) * 100) if fc else 0.0,
            "Cumplimiento_Plan": (r26 / p26 * 100) if p26 else 0.0
        }
        with self._cache_lock:
            self._cache[cache_key] = out
        return out

    def compute_by_dimension(self, dimension="division", filters=None):
        cache_key = self._filter_cache_key(f"dim_{dimension}", filters)
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        dim_col = "cat_Direccion_Division" if dimension == "division" else "cat_Grupo_de_Cuentas"
        nombre = "division" if dimension == "division" else "grupo_cuentas"

        tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
        grouped = self.agg(nombre, [dim_col], filters, tm)

        if grouped.empty:
            return pd.DataFrame(columns=["Concepto", "Real_2025", "Pct_r25", "Plan_2026", "Pct_p26", "Real_2026", "Pct_r26", "Vs_Plan_Abs", "Vs_Plan_Pct", "Vs_AA_Abs", "Vs_AA_Pct", "Forecast", "Vs_Forecast", "Vs_Forecast_Pct"])

        df = grouped.rename(columns={dim_col: "Concepto"})
        df = df[df["Concepto"].notna() & (df["Concepto"] != "nan") & (df["Concepto"] != "None")].copy()

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

        fc = self._forecast_cruzado(nombre, filters, tm, group_col=dim_col)
        fc_pd = fc.rename(columns={dim_col: "Concepto"}) if len(fc) else pd.DataFrame(columns=["Concepto", "Forecast"])
        df = df.merge(fc_pd, on="Concepto", how="left")
        df["Forecast"] = df["Forecast"].fillna(0.0).astype(float)   # astype: sin Forecast (digitales) la columna llega object
        df["Vs_Forecast"] = df["Real_2026"] - df["Forecast"]
        df["Vs_Forecast_Pct"] = np.where(df["Forecast"] != 0, (df["Vs_Forecast"] / df["Forecast"].abs()) * 100, 0.0)
        if dimension != "division":   # FCST del servidor solo existe por Cuenta (no por División)
            df = self._con_fcst_srv(df, self._fcst_srv(filters, dim_col, tm), dim_col)

        if dimension == "division":
            ORDEN_DIV = {
                "Division Norte": 0, "Division Centro": 1, "Division Sur": 2,
                "Concentrador": 3, "Cerrados y Cancelados": 4, "Corporativo": 5,
                "Expansión": 6, "Fuerza Proactiva": 7, "Otros": 8
            }
            df["_ord"] = df["Concepto"].map(lambda x: ORDEN_DIV.get(x, 99))
            df = df.sort_values(["_ord", "Real_2026"], ascending=[True, False]).drop(columns=["_ord"])
        else:
            df["_ord"] = df["Concepto"].map(lambda x: self.orden_gpo.get(x, 999))
            df = df.sort_values(["_ord", "Real_2026"], ascending=[True, False]).drop(columns=["_ord"])

        res_df = df.reset_index(drop=True)
        with self._cache_lock:
            self._cache[cache_key] = res_df
        return res_df

    def compute_weekly_series(self, filters=None):
        cache_key = self._filter_cache_key("weekly", filters)
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        tm = filters.get("tipo_monto", "Monto Constante") if filters else "Monto Constante"
        # Real_2026 CRUDO por semana (hibrido=False); el híbrido Real/Plan se aplica abajo, como antes.
        df = self.agg("global", ["sem"], filters, tm, sem=bool(filters), hibrido=False)
        df = df.sort_values("sem").reset_index(drop=True)
        full_sems = pd.DataFrame({"sem": list(range(1, 54))})
        df = pd.merge(full_sems, df, on="sem", how="left").fillna(0.0)

        # 2026-09-21: Real 2026 = Real hasta SMR; después el Plan 2026 (igual que weekly() del Streamlit).
        df.loc[df["sem"] > self.smr, "Real_2026"] = df.loc[df["sem"] > self.smr, "Plan_2026"]
        with self._cache_lock:
            self._cache[cache_key] = df
        return df

    def get_variaciones_riesgos_oportunidades(self):
        """Retorna las 4 tablas de variaciones parseadas desde aggs/riesgos.json"""
        data = self.riesgos
        _NOTA_RE = re.compile(r"^\d+\.\s*(?P<cuenta>.+?)\s+(?P<monto>\(?[\d.,]+\s*mdp\)?)\s*/\s*(?P<pct>\(?[\d.,]+%\)?)\s*(?:·\s*(?P<com>.*))?$")

        def _parsear(lista):
            secciones = {}
            actual = None
            for t_ in lista:
                m = re.match(r"^[—-]+\s*(.+?)\s*[—-]+$", t_.strip())
                if m:
                    actual = m.group(1).strip()
                    secciones[actual] = []
                elif actual:
                    secciones[actual].append(t_)

            res = {}
            for sec, notas in secciones.items():
                filas = []
                for t_ in notas:
                    m = _NOTA_RE.match(t_.strip())
                    if m:
                        filas.append({
                            "Cuenta": m.group("cuenta").strip(),
                            "Monto": m.group("monto").replace(" ", ""),
                            "%": m.group("pct"),
                            "Comentario": (m.group("com") or "").strip()
                        })
                    else:
                        filas.append({"Cuenta": t_.strip(), "Monto": "", "%": "", "Comentario": ""})
                res[sec] = pd.DataFrame(filas)
            return res

        parsed_riesgos = _parsear(data.get("riesgos", []))
        parsed_oportunidades = _parsear(data.get("oportunidades", []))

        # 4 Tablas estándar del Resumen:
        return {
            "pos_aa": parsed_oportunidades.get("Variaciones Positivas vs AA (YTD)", pd.DataFrame()),
            "neg_aa": parsed_riesgos.get("Variaciones Negativas vs AA (YTD)", pd.DataFrame()),
            "pos_plan": parsed_oportunidades.get("Variaciones Positivas vs Plan (YTD)", pd.DataFrame()),
            "neg_plan": parsed_riesgos.get("Variaciones Negativas vs Plan (YTD)", pd.DataFrame()),
        }

    @lru_cache(maxsize=128)
    def get_tree_children_cuentas(self, grupo_cuenta: str, si: int, sf: int, tipo_monto: str = "Monto Constante", canal: str = "Fisico",
                                  cuentas: tuple = (), subtipos: tuple = ()):
        """Drill-down de Grupo de Cuentas -> Cuentas desde agg_cuentas. `cuentas`/`subtipos` (tuplas, por el lru_cache)
        = filtros globales de Cuenta y Subtipo."""
        extra = {"cat_Grupo_de_Cuentas": [grupo_cuenta], "cat_Cuentas": list(cuentas), "cat_Subtipo": list(subtipos)}
        grouped = self.agg("cuentas", ["cat_Cuentas"], {"si": si, "sf": sf, "canal": canal}, tipo_monto, extra=extra)
        if grouped.empty:
            return pd.DataFrame()

        df = grouped.rename(columns={"cat_Cuentas": "Concepto"})
        tot_r25 = df["Real_2025"].sum()
        tot_p26 = df["Plan_2026"].sum()
        tot_r26 = df["Real_2026"].sum()

        df["Pct_r25"] = (df["Real_2025"] / tot_r25 * 100) if tot_r25 else 0.0
        df["Pct_p26"] = (df["Plan_2026"] / tot_p26 * 100) if tot_p26 else 0.0
        df["Pct_r26"] = (df["Real_2026"] / tot_r26 * 100) if tot_r26 else 0.0
        df["Vs_Plan_Abs"] = df["Real_2026"] - df["Plan_2026"]
        df["Vs_Plan_Pct"] = np.where(df["Plan_2026"] != 0, (df["Vs_Plan_Abs"] / df["Plan_2026"].abs()) * 100, 0.0)
        df = self._con_fcst_srv(
            df, self._fcst_srv({"si": si, "sf": sf, "fg_gpo": [grupo_cuenta], "fg_cta": list(cuentas), "canal": canal}, "cat_Cuentas", tipo_monto),
            "cat_Cuentas")
        return self.ordenar_hijos(df.reset_index(drop=True), "cat_Cuentas")

    def query_tree_children_fast(self, tree_key: str, parent_col: str, parent_val: str, target_col: str, si: int = 1, sf: int = 36, tipo_monto: str = "Monto Constante", filters: dict = None):
        """Drill-down de árboles. Todo va por DuckDB (dashboard.duckdb): aggs chicos agg_* para Resumen/Detalle Cuenta/
        Contraloría/Cierres, y `consolidado` + rollups para CECO/PDV (ver DuckDBService)."""
        d = self.ddb
        f = filters or {}
        canal = d.canal_de(filters)

        # Árbol de Resumen (Grupo de Cuentas -> Cuentas), con Forecast Finanzas y FCST PLAN.
        if tree_key == "resumen-gc" and target_col == "cat_Cuentas":
            _ctas = tuple(f.get("fg_cta") or ())
            _subs = tuple(f.get("fg_subtipo") or ())
            df = self.get_tree_children_cuentas(parent_val, si, sf, tipo_monto, canal, _ctas, _subs)
            if not df.empty:   # en Digital _forecast_cruzado devuelve vacío (sin Forecast)
                fc = self._forecast_cruzado(
                    "cuentas",
                    {"si": si, "sf": sf, "fg_gpo": [parent_val], "fg_cta": list(_ctas), "fg_subtipo": list(_subs), "canal": canal},
                    tipo_monto, group_col="cat_Cuentas"
                )
                fc_pd = fc.rename(columns={"cat_Cuentas": "Concepto"}) if len(fc) else pd.DataFrame(columns=["Concepto", "Forecast"])
                df = df.merge(fc_pd, on="Concepto", how="left")
                df["Forecast"] = df["Forecast"].fillna(0.0).astype(float)
                df["Vs_Forecast"] = df["Real_2026"] - df["Forecast"]
                df["Vs_Forecast_Pct"] = np.where(df["Forecast"] != 0, (df["Vs_Forecast"] / df["Forecast"].abs()) * 100, 0.0)
            return df

        flt = dict(f)
        flt.update({"si": si, "sf": sf})

        # Árbol de Detalle Cuenta (Agrupa1->Agrupa2->Agrupa3->Agrupador Reales): un único agg para cualquier nivel.
        if "agrup-real" in tree_key:
            if not d.tiene_agg("agrupadores_reales"):
                return pd.DataFrame()
            extra = {}
            if parent_col and parent_val:
                extra[parent_col] = [parent_val]
            if f.get("cat_cuenta_query"):                    # buscador de cuenta de la pestaña
                extra["cat_cuenta_query"] = f["cat_cuenta_query"]
            for k in ("cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3"):   # ancestros de la ruta: sin ellos se mezclan ramas
                if f.get(k) and k != parent_col:
                    extra[k] = f[k]
            df = d.agg("agrupadores_reales", [target_col], flt, tipo_monto, extra=extra)
            df = df.rename(columns={target_col: "Concepto"}).sort_values("Real_2026", ascending=False).reset_index(drop=True)
            return df

        # CECO / PDV / Cuenta (cuando el objetivo no es Agrupa2/3): consolidado + rollups.
        va_por_duckdb = ("ceco" in tree_key or "pdv" in tree_key or "cuenta" in tree_key) \
            and target_col not in ("cat_Agrupa2", "cat_Agrupa3")
        if va_por_duckdb:
            return self.ordenar_hijos(
                d.query_tree_children(parent_col, parent_val, target_col, si=si, sf=sf, filters=filters, tipo_monto=tipo_monto),
                target_col)

        # Resto: agg según el árbol.
        if target_col == "cat_Agrupa2":
            nombre = "agrupa2"
        elif target_col == "cat_Agrupa3":
            nombre = "agrupa3"
        elif "cierre" in tree_key:
            nombre = "cierres_arbol"
        elif "ctrl" in tree_key:
            nombre = "contraloria"
        else:
            nombre = "cuentas"
        if not d.tiene_agg(nombre):
            nombre = "cuentas"
        cols = d.columnas_agg(nombre)

        if not target_col or target_col not in cols or (parent_col and parent_col not in cols):
            try:
                return d.query_tree_children(parent_col, parent_val, target_col, si=si, sf=sf, filters=filters, tipo_monto=tipo_monto)
            except Exception:
                return pd.DataFrame()

        extra = {}
        if parent_col and parent_val:
            extra[parent_col] = [parent_val]
        if f.get("eco_pdv_in"):
            extra["ECO_PDV"] = f["eco_pdv_in"]
        if f.get("cat_pdc_query"):
            extra["cat_pdc_query"] = f["cat_pdc_query"]
        if f.get("eco_pdv_exclude_zero"):
            extra["eco_pdv_exclude_zero"] = True
        if f.get("id_centro_costos_in"):
            extra["ID_CENTRO_COSTOS"] = f["id_centro_costos_in"]
        # Filtros de ancestros del árbol (toggle_tree_branch agrega (columna, valor) de la ruta): cualquier clave que
        # sea columna real del agg y no una de las reconocidas arriba, para no mezclar ramas que comparten valor.
        _conocidas = {"fg_resp", "fg_gpo", "fg_subtipo", "fg_cta", "fg_cta_ids", "eco_pdv_in", "cat_pdc_query",
                      "eco_pdv_exclude_zero", "id_centro_costos_in", "si", "sf", "tipo_monto", "canal", "modo",
                      "meses", "trimestres", "cat_cuenta_query"}
        for k, v in f.items():
            if v and k not in _conocidas and k != parent_col and k in cols and k not in extra:
                extra[k] = v

        df = d.agg(nombre, [target_col], flt, tipo_monto, extra=extra)
        if df.empty:
            try:
                return d.query_tree_children(parent_col, parent_val, target_col, si=si, sf=sf, filters=filters, tipo_monto=tipo_monto)
            except Exception:
                return pd.DataFrame()

        df = df.rename(columns={target_col: "Concepto"})
        # 2026-11-05: antes se descartaban los valores vacíos y se cortaba a 120 filas, y los hijos no sumaban el padre
        # (Cierres, Agrupador -> PDC: 877 vs 4,693 MDP). Los vacíos se muestran como "(Sin dato)" y no se corta: la
        # pantalla ya pagina de 7 en 7 ("Mostrar 7 más").
        df["Concepto"] = df["Concepto"].where(df["Concepto"].notna() & (df["Concepto"].astype(str) != "nan"), "(Sin dato)")
        df = df.groupby("Concepto", as_index=False)[["Real_2025", "Plan_2026", "Real_2026"]].sum()

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

        df = df.sort_values("Real_2026", ascending=False).reset_index(drop=True)
        return self.ordenar_hijos(df, target_col)
