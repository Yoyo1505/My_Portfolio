import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
AGGS = ROOT / "aggs"
AGGS.mkdir(exist_ok=True)
rng = np.random.default_rng(11)
SEM_REAL = 42

DIVISIONES = {
    "Division Norte": ["Territorio Baja California", "Territorio Bajio", "Territorio Chihuahua", "Territorio Jalisco"],
    "Division Centro": ["Territorio Metro Norte", "Territorio Metro Sur", "Territorio Queretaro"],
    "Division Sur": ["Territorio Chiapas", "Territorio Oaxaca", "Territorio Veracruz", "Territorio Yucatan"],
    "Corporativo": ["Territorio Corporativo"],
}
GRUPOS = {
    "Nomina": {"Sueldos y salarios": 9.0, "Tiempo extra": 1.5, "Prestaciones": 3.2},
    "Renta, Luz y Telefono": {"Renta": 6.0, "Energia electrica": 2.2, "Telefono": 0.9},
    "Mantenimiento": {"Mtto. preventivo": 1.0, "Mtto. correctivo": 0.9, "Limpieza": 1.2},
    "Logistica": {"Fletes": 2.3, "Combustibles": 1.1, "Arrendamiento de flotilla": 1.3},
    "Mercadotecnia": {"Medios": 1.7, "Promociones": 1.4, "Anuncios": 0.5},
    "Tecnologia": {"Licencias y software": 1.2, "Servicios en la nube": 1.3, "Comunicaciones": 0.8},
    "Seguridad": {"Vigilancia": 1.4, "Monitoreo de alarmas": 0.4, "Traslado de valores": 0.7},
}
PREFIJOS = ["Tienda", "Modulo", "Distribuidor"]
SUBTIPOS = ["Fisico", "Fisico", "Fisico", "Mixto"]


def build():
    cuentas = []
    cid = 6000100000
    for g, items in GRUPOS.items():
        for name, base in items.items():
            cid += 10
            cuentas.append(dict(ID_CONCEPTO_CUENTA_NIV3=str(cid), cat_Grupo_de_Cuentas=g, cat_Cuentas=name,
                                cat_Agrupador_Reales=g, cat_CtaMayor_Nombre=name, cat_PosPre="PP" + str(cid)[-4:],
                                cat_Denom_PosPre=name + " (presupuesto)", base=base,
                                cat_Contraloria=g, cat_Contraloria_6=g[:6]))
    cuentas = pd.DataFrame(cuentas)
    cecos, ceco_id, eco = [], 100000, 5000
    for div, terrs in DIVISIONES.items():
        for t in terrs:
            for z in range(1, 3):
                zona = f"{t.replace('Territorio ', '')} Zona {z}"
                for r in range(1, 3):
                    region = f"{zona} Region {r}"
                    for p in range(int(rng.integers(3, 6))):
                        ceco_id += 1
                        eco += 1
                        city = rng.choice(["Centro", "Plaza Norte", "Plaza Sur", "Mercado", "Avenida", "Paseo", "Galerias", "Portal"])
                        cecos.append(dict(ID_CENTRO_COSTOS=str(ceco_id), ECO_PDV=eco, cat_Prefijo=str(rng.choice(PREFIJOS)),
                                          cat_Direccion_Division=div, cat_Subdireccion_Territorio=t, cat_Subdireccion_Zona=zona,
                                          cat_Subdireccion_Region=region, cat_PDC=f"{city} {eco}", cat_Agrupa1="Canales Fisicos",
                                          cat_Agrupa2=div, cat_Agrupa3=t, cat_Subtipo=str(rng.choice(SUBTIPOS)),
                                          size=float(np.clip(rng.lognormal(0, 0.35), 0.5, 2.5))))
    cecos = pd.DataFrame(cecos)
    weeks = np.arange(1, 54)
    month = ((pd.Timestamp("2025-12-29") + pd.to_timedelta((weeks - 1) * 7 + 3, unit="D")).month).astype(int)
    frames = []
    over = set(rng.choice(cecos["ID_CENTRO_COSTOS"], 10, replace=False))
    for c in cecos.itertuples():
        for a in cuentas.itertuples():
            base = a.base * c.size * 20000
            season = 1 + 0.08 * np.sin(2 * np.pi * (weeks - 8) / 52) + np.where(weeks >= 44, 0.1, 0.0)
            bias = rng.normal(1, 0.06)
            plan = base * season * rng.normal(1, 0.01, 53)
            r25 = base * season * 0.95 * bias * rng.normal(1, 0.06, 53)
            r26 = base * season * 1.02 * bias * rng.normal(1, 0.06, 53)
            if c.ID_CENTRO_COSTOS in over and a.cat_Grupo_de_Cuentas in ("Mantenimiento", "Logistica"):
                r26 = r26 * np.minimum(np.where(weeks >= 18, 1 + 0.012 * (weeks - 17), 1.0), 1.4)
            for serie, vals in (("R25", r25), ("P26", plan), ("R26", r26)):
                keep = weeks <= SEM_REAL if serie == "R26" else np.ones(53, bool)
                frames.append(pd.DataFrame({"sem": weeks[keep].astype("int32"), "Serie": serie, "monto_cons": np.round(vals[keep], 2),
                                            "monto_loc": np.round(vals[keep], 2), "MES": month[keep],
                                            "ID_CENTRO_COSTOS": c.ID_CENTRO_COSTOS, "ID_CONCEPTO_CUENTA_NIV3": a.ID_CONCEPTO_CUENTA_NIV3}))
    df = pd.concat(frames, ignore_index=True)
    spikes = []
    idx_r26 = df.index[df["Serie"] == "R26"]
    keys = df.loc[idx_r26, ["ID_CENTRO_COSTOS", "ID_CONCEPTO_CUENTA_NIV3"]].drop_duplicates().reset_index(drop=True)
    pick = keys.sample(40, random_state=5)
    for k, row in enumerate(pick.itertuples()):
        week = int(rng.integers(36, 43)) if k < 15 else int(rng.integers(14, 36))
        m = (df["Serie"] == "R26") & (df["ID_CENTRO_COSTOS"] == row.ID_CENTRO_COSTOS) & (df["ID_CONCEPTO_CUENTA_NIV3"] == row.ID_CONCEPTO_CUENTA_NIV3) & (df["sem"] == week)
        factor = float(rng.uniform(4.0, 7.0))
        df.loc[m, ["monto_cons", "monto_loc"]] = (df.loc[m, ["monto_cons", "monto_loc"]] * factor).round(2).values
        spikes.append((row.ID_CENTRO_COSTOS, row.ID_CONCEPTO_CUENTA_NIV3, week, round(factor, 2)))
    (ROOT / "datos").mkdir(exist_ok=True)
    (ROOT / "datos" / "picos_inyectados.json").write_text(json.dumps(spikes), encoding="utf-8")
    ce = cecos.drop(columns="size")
    cu = cuentas.drop(columns=["base", "cat_Contraloria", "cat_Contraloria_6"])
    df = df.merge(cu, on="ID_CONCEPTO_CUENTA_NIV3").merge(ce, on="ID_CENTRO_COSTOS")
    ctr = cuentas[["ID_CONCEPTO_CUENTA_NIV3", "cat_Contraloria", "cat_Contraloria_6"]]
    df = df.merge(ctr, on="ID_CONCEPTO_CUENTA_NIV3")
    df["TRIMESTRE"] = np.select([df["sem"] <= 13, df["sem"] <= 26, df["sem"] <= 39], ["Q1", "Q2", "Q3"], "Q4")
    order = ["sem", "Serie", "monto_cons", "monto_loc", "ID_CENTRO_COSTOS", "ID_CONCEPTO_CUENTA_NIV3", "ECO_PDV", "MES",
             "cat_Grupo_de_Cuentas", "cat_Cuentas", "cat_Agrupador_Reales", "cat_CtaMayor_Nombre", "cat_PosPre", "cat_Denom_PosPre",
             "cat_Prefijo", "cat_Direccion_Division", "cat_Subdireccion_Territorio", "cat_Subdireccion_Zona", "cat_Subdireccion_Region",
             "cat_PDC", "cat_Agrupa1", "cat_Agrupa2", "cat_Agrupa3", "cat_Subtipo", "cat_Contraloria", "cat_Contraloria_6", "TRIMESTRE"]
    df = df[order]
    datos = ROOT / "datos"
    datos.mkdir(exist_ok=True)
    for serie, name in (("R25", "r25"), ("P26", "p26"), ("R26", "r26")):
        s = df[df["Serie"] == serie]
        raw = pd.DataFrame({"ID_CENTRO_COSTOS": s["ID_CENTRO_COSTOS"], "ID_CONCEPTO_CUENTA_NIV3": s["ID_CONCEPTO_CUENTA_NIV3"],
                            "SEMANA_ARG": s["sem"], "SUM_MONTO_MNX_CONS_(SUMA)": s["monto_cons"], "Monto_Local": s["monto_loc"], "MES": s["MES"]})
        raw.to_parquet(datos / f"{name}_sem{SEM_REAL}.parquet", index=False)
    est = pd.DataFrame({"CECO_1": ce["ID_CENTRO_COSTOS"], "Eco PDV": ce["ECO_PDV"], "Eco": ce["ECO_PDV"],
                        "Dirección / División": ce["cat_Direccion_Division"], "Subdirección / Territorio": ce["cat_Subdireccion_Territorio"],
                        "Subdirección / Zona": ce["cat_Subdireccion_Zona"], "Subdirección / Región": ce["cat_Subdireccion_Region"],
                        "PDC": ce["cat_PDC"], "Agrupa 1": ce["cat_Agrupa1"], "Agrupa 2": ce["cat_Agrupa2"], "Agrupa 3": ce["cat_Agrupa3"],
                        "Subtipo": ce["cat_Subtipo"], "Prefijo": ce["cat_Prefijo"], "Contraloria": "Contraloria Detalle",
                        "Contraloria ": "Contraloria Resumen", "Descripción": ce["cat_PDC"], "Agrupa Canales": "Fisicos"})
    est.columns = [c.strip() for c in est.columns]
    with pd.ExcelWriter(datos / "Catálogo de estructura.xlsx") as xw:
        est.to_excel(xw, sheet_name="Estructura", index=False)
    cat = pd.DataFrame({"Cta Mayor": cuentas["ID_CONCEPTO_CUENTA_NIV3"], "Grupo de Cuentas": cuentas["cat_Grupo_de_Cuentas"],
                        "Cuentas": cuentas["cat_Cuentas"], "Agrupador Reales": cuentas["cat_Agrupador_Reales"],
                        "Denom.Cta. Mayor": cuentas["cat_CtaMayor_Nombre"], "PosPre": cuentas["cat_PosPre"],
                        "Denom.PosPre": cuentas["cat_Denom_PosPre"], "Orden presentación Gpo. Cta": cuentas["cat_Grupo_de_Cuentas"].rank(method="dense").astype(int),
                        "Responsable Interno": "Demo Owner", "Lider": "Demo Lead"})
    cat.to_excel(datos / "Catálogo grupo cuentas.xlsx", index=False)
    fc = df[df["Serie"] == "R26"].copy()
    fc40 = fc[fc["sem"] <= 40]
    mk = lambda s: pd.DataFrame({"ID_CENTRO_COSTOS": s["ID_CENTRO_COSTOS"], "ID_CONCEPTO_CUENTA_NIV3": s["ID_CONCEPTO_CUENTA_NIV3"],
                                 "SEMANA_ARG": s["sem"], "SUM_MONTO_MNX_CONS_(SUMA)": s["monto_cons"], "Monto_Local": s["monto_loc"], "MES": s["MES"]})
    mk(fc40).to_parquet(datos / "r26_sem40.parquet", index=False)
    pl_ = df[(df["Serie"] == "P26") & (df["sem"] > 40)].copy()
    pl_["monto_cons"] = (pl_["monto_cons"] * 1.015).round(2)
    pl_["monto_loc"] = pl_["monto_cons"]
    mk(pl_).to_parquet(datos / f"fcst_sem{SEM_REAL}.parquet", index=False)
    wide = df[(df["Serie"] == "P26")].groupby(["ID_CONCEPTO_CUENTA_NIV3", "sem"])["monto_cons"].sum().unstack("sem")
    rows = [["Forecast Finanzas"], [], ["CECO_N7", "Cta de Mayor"] + list(range(1, 54))]
    for cta, r in wide.iterrows():
        rows.append(["Red Fisica", int(cta)] + [float(round(v * 1.01, 2)) for v in r.values])
    pd.DataFrame(rows).to_excel(datos / "Forecast Finanzas 2026.xlsx", sheet_name="Sheet 1", index=False, header=False)
    import hashlib
    salt = "demo-salt-not-secret"
    (ROOT / "archivos_proyecto").mkdir(exist_ok=True)
    (ROOT / "archivos_proyecto" / ".env").write_text("SALT_PASSWORD=" + salt + chr(10), encoding="utf-8")
    h = lambda pw: hashlib.sha256((salt + pw).encode()).hexdigest()
    users = {"admin1": {"nombre_completo": "Demo Admin", "rol": "admin", "password": h("admin123"), "permite_descargar": True},
             "analista1": {"nombre_completo": "Demo Analyst", "rol": "analista", "password": h("analista123"), "permite_descargar": False}}
    (datos / "usuarios.json").write_text(json.dumps(users, indent=1), encoding="utf-8")
    print(f"rows={len(df):,} cecos={len(cecos)} cuentas={len(cuentas)}")


if __name__ == "__main__":
    build()
