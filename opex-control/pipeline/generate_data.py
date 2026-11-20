import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
YEAR_START = date(2025, 12, 29)
CUTOFF_WEEK = 42
N_WEEKS = 53

DIVISIONS = {
    "North": ["Pacific North", "Highlands", "Border"],
    "Central": ["Capital", "Bajio", "Gulf Central"],
    "South": ["Gulf South", "Pacific South", "Peninsula"],
    "Corporate": ["HQ Operations", "Shared Services", "Regional Offices"],
}
CITIES = ["Alder", "Brook", "Cedar", "Dunmore", "Elmwood", "Fairview", "Glenrock", "Harbor", "Ironwood", "Juniper",
          "Kingsley", "Lakeside", "Maplewood", "Northgate", "Oakridge", "Pinecrest", "Quarry", "Riverton", "Stonebridge",
          "Thornfield", "Union", "Valley", "Westbrook", "Yarrow", "Zenith"]
GROUPS = {
    "Payroll": {"Base salaries": 9.0, "Overtime": 1.6, "Benefits": 3.4},
    "Occupancy": {"Rent": 6.5, "Utilities": 2.2, "Property tax": 1.1},
    "Maintenance": {"Preventive maintenance": 1.0, "Corrective maintenance": 0.9, "Cleaning": 1.2},
    "Logistics": {"Freight": 2.4, "Fuel": 1.1, "Fleet leasing": 1.3},
    "Marketing": {"Media": 1.8, "Promotions": 1.5, "Signage": 0.5},
    "Technology": {"Software licenses": 1.2, "Cloud services": 1.4, "Telecom": 0.8},
    "Security": {"Guard services": 1.5, "Alarm monitoring": 0.4, "Cash transport": 0.7},
    "Administration": {"Insurance": 0.9, "Professional fees": 0.8, "Office supplies": 0.4},
}
OWNERS = ["A. Rivera", "B. Castillo", "C. Mendez", "D. Ortega", "E. Salazar", "F. Navarro", "G. Pineda", "H. Lozano"]


def build_dims(rng):
    stores, sid = [], 1000
    for div, regions in DIVISIONS.items():
        for reg in regions:
            for z in range(1, 4):
                n = 3 if div == "Corporate" else int(rng.integers(6, 10))
                for _ in range(n):
                    sid += 1
                    city = CITIES[int(rng.integers(0, len(CITIES)))]
                    stores.append({
                        "store_id": sid,
                        "store_name": f"{city} {'Office' if div == 'Corporate' else 'Plaza'} {sid}",
                        "division": div, "region": reg, "zone": f"{reg} Z{z}",
                        "size": float(np.clip(rng.lognormal(0, 0.35), 0.5, 2.6)) * (0.6 if div == "Corporate" else 1.0),
                    })
    accounts, aid = [], 0
    for gi, (grp, accts) in enumerate(GROUPS.items()):
        for name, base in accts.items():
            aid += 1
            accounts.append({"account_id": 100 + aid, "account": name, "account_group": grp,
                             "base": base, "owner": OWNERS[gi % len(OWNERS)]})
    return pd.DataFrame(stores), pd.DataFrame(accounts)


def seasonality(week_idx, group, account):
    w = week_idx
    s = 1 + 0.08 * np.sin(2 * np.pi * (w - 8) / 52)
    s = s + np.where(w >= 44, 0.12 if group in ("Marketing", "Logistics") else 0.04, 0.0)
    if account == "Utilities":
        s = s + 0.18 * np.cos(2 * np.pi * (w - 30) / 52)
    if account == "Rent":
        s = np.ones_like(s, dtype=float)
    if account == "Overtime":
        s = s + np.where(w >= 44, 0.4, 0.0)
    return s


def generate(seed=7):
    rng = np.random.default_rng(seed)
    stores, accounts = build_dims(rng)
    weeks = np.arange(1, N_WEEKS + 1)
    rows = []
    overspend_stores = set(rng.choice(stores["store_id"], 14, replace=False))
    overspend_groups = ["Maintenance", "Logistics", "Technology"]
    anomalies = []
    for st in stores.itertuples():
        for ac in accounts.itertuples():
            base = ac.base * st.size * 1000
            season = seasonality(weeks, ac.account_group, ac.account)
            bias = rng.normal(1.0, 0.07)
            plan = base * season * 1.0 * rng.normal(1.0, 0.01, N_WEEKS)
            prev = base * season * 0.95 * bias * rng.normal(1.0, 0.06, N_WEEKS)
            act = base * season * 1.02 * bias * rng.normal(1.0, 0.06, N_WEEKS)
            if st.store_id in overspend_stores and ac.account_group in overspend_groups:
                drift = np.where(weeks >= 18, 1 + 0.012 * (weeks - 17), 1.0)
                act = act * np.minimum(drift, 1.45)
            if rng.random() < 0.012:
                w = int(rng.integers(12, CUTOFF_WEEK + 1))
                factor = float(rng.uniform(3.0, 6.0))
                act[w - 1] *= factor
                anomalies.append({"store_id": int(st.store_id), "account_id": int(ac.account_id),
                                  "week": w, "factor": round(factor, 2)})
            for w, p, pr, a in zip(weeks, plan, prev, act):
                rows.append((st.store_id, ac.account_id, int(w), round(p, 2), round(pr, 2), round(a, 2)))
    fact = pd.DataFrame(rows, columns=["store_id", "account_id", "week", "plan", "actual_prev", "actual_full"])
    week_start = {int(w): YEAR_START + timedelta(days=7 * (int(w) - 1)) for w in weeks}
    fact["week_start"] = pd.to_datetime(fact["week"].map(week_start))
    fact["month"] = (fact["week_start"] + pd.Timedelta(days=3)).dt.month
    fact["quarter"] = ((fact["month"] - 1) // 3 + 1).astype("int8")
    fact["is_actual"] = fact["week"] <= CUTOFF_WEEK
    fact["actual"] = np.where(fact["is_actual"], fact["actual_full"], np.nan)
    last8 = fact[(fact["week"] > CUTOFF_WEEK - 8) & fact["is_actual"]].groupby(["store_id", "account_id"])["actual"].median()
    plan_last8 = fact[(fact["week"] > CUTOFF_WEEK - 8) & fact["is_actual"]].groupby(["store_id", "account_id"])["plan"].median()
    ratio = (last8 / plan_last8).rename("run_rate")
    fact = fact.merge(ratio, on=["store_id", "account_id"], how="left")
    fact["forecast"] = np.where(fact["is_actual"], fact["actual"], fact["plan"] * fact["run_rate"])
    fact = fact.drop(columns=["actual_full", "run_rate"])
    for c in ("plan", "actual_prev", "actual", "forecast"):
        fact[c] = fact[c].round(2)
    return fact, stores.drop(columns="size"), accounts.drop(columns="base"), anomalies


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    fact, stores, accounts, anomalies = generate()
    fact.to_parquet(RAW / "fact_weekly.parquet", index=False)
    stores.to_parquet(RAW / "dim_store.parquet", index=False)
    accounts.to_parquet(RAW / "dim_account.parquet", index=False)
    (RAW / "injected_anomalies.json").write_text(json.dumps(anomalies, indent=1), encoding="utf-8")
    print(f"fact={len(fact):,} stores={len(stores)} accounts={len(accounts)} injected_anomalies={len(anomalies)}")


if __name__ == "__main__":
    main()
