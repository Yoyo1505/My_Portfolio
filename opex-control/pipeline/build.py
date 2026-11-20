import json
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from monitoring.anomalies import combine, psi  # noqa: E402
from pipeline.generate_data import CUTOFF_WEEK  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
WH = ROOT / "data" / "warehouse"
DB = WH / "opex.duckdb"


def build():
    t0 = time.time()
    WH.mkdir(parents=True, exist_ok=True)
    fact = pd.read_parquet(RAW / "fact_weekly.parquet")
    stores = pd.read_parquet(RAW / "dim_store.parquet")
    accounts = pd.read_parquet(RAW / "dim_account.parquet")
    cutoff = int(fact.loc[fact["is_actual"], "week"].max())
    anomalies = combine(fact, cutoff)
    prior = fact[(fact["week"] <= cutoff - 8) & fact["is_actual"]]
    recent = fact[(fact["week"] > cutoff - 8) & fact["is_actual"]]
    drift = psi((prior["actual"] / prior["plan"]).to_numpy(), (recent["actual"] / recent["plan"]).to_numpy())
    if DB.exists():
        DB.unlink()
    con = duckdb.connect(str(DB))
    con.register("fact_df", fact)
    con.register("stores_df", stores)
    con.register("accounts_df", accounts)
    con.register("anom_df", anomalies)
    con.execute("CREATE TABLE dim_store AS SELECT * FROM stores_df")
    con.execute("CREATE TABLE dim_account AS SELECT * FROM accounts_df")
    con.execute("CREATE TABLE fact AS SELECT * FROM fact_df ORDER BY week, store_id, account_id")
    con.execute("CREATE TABLE anomalies AS SELECT * FROM anom_df")
    con.execute("""
        CREATE VIEW v_fact AS
        SELECT f.*, s.store_name, s.division, s.region, s.zone, a.account, a.account_group, a.owner
        FROM fact f JOIN dim_store s USING (store_id) JOIN dim_account a USING (account_id)
    """)
    con.execute("""
        CREATE TABLE roll_division_week AS
        SELECT division, week, SUM(plan) plan, SUM(actual_prev) actual_prev, SUM(actual) actual, SUM(forecast) forecast
        FROM v_fact GROUP BY 1, 2
    """)
    con.close()
    meta = {"cutoff_week": cutoff, "rows": int(len(fact)), "stores": int(len(stores)), "accounts": int(len(accounts)),
            "anomalies": int(len(anomalies)), "psi_actual_vs_plan": round(drift, 4),
            "built_seconds": round(time.time() - t0, 1)}
    (WH / "_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(meta)


if __name__ == "__main__":
    build()
