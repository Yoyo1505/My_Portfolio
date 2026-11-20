from dataclasses import dataclass, field
from typing import List, Optional

from .db import meta, query

DIM_COLUMNS = {
    "division": "division", "region": "region", "zone": "zone", "store": "store_name",
    "account_group": "account_group", "account": "account", "owner": "owner",
    "month": "month", "quarter": "quarter", "week": "week",
}
FILTER_COLUMNS = (("division", "divisions"), ("region", "regions"), ("zone", "zones"),
                  ("store_name", "stores"), ("account_group", "groups"), ("account", "accounts"))


@dataclass
class Filters:
    divisions: List[str] = field(default_factory=list)
    regions: List[str] = field(default_factory=list)
    zones: List[str] = field(default_factory=list)
    stores: List[str] = field(default_factory=list)
    groups: List[str] = field(default_factory=list)
    accounts: List[str] = field(default_factory=list)
    week_from: int = 1
    week_to: Optional[int] = None

    def where(self, only_actual=True, prefix=""):
        clauses, params = [], []
        for col, attr in FILTER_COLUMNS:
            vals = getattr(self, attr)
            if vals:
                clauses.append(f"{prefix}{col} IN ({','.join('?' * len(vals))})")
                params += list(vals)
        hi = self.week_to if self.week_to is not None else 53
        if only_actual:
            hi = min(hi, meta()["cutoff_week"])
        clauses += [f"{prefix}week >= ?", f"{prefix}week <= ?"]
        params += [int(self.week_from), int(hi)]
        return " AND ".join(clauses), params


def kpis(f):
    w, p = f.where()
    ytd = query(f"SELECT COALESCE(SUM(actual),0) actual, COALESCE(SUM(plan),0) plan, COALESCE(SUM(actual_prev),0) prev FROM v_fact WHERE {w}", p).iloc[0]
    w2, p2 = Filters(**{**f.__dict__, "week_from": 1, "week_to": 53}).where(only_actual=False)
    fy = query(f"SELECT COALESCE(SUM(forecast),0) forecast, COALESCE(SUM(plan),0) plan FROM v_fact WHERE {w2}", p2).iloc[0]
    a, pl, pr = float(ytd["actual"]), float(ytd["plan"]), float(ytd["prev"])
    return {
        "actual": a, "plan": pl, "prev": pr, "var_plan": a - pl,
        "var_plan_pct": (a / pl - 1) if pl else 0.0, "var_prev_pct": (a / pr - 1) if pr else 0.0,
        "forecast": float(fy["forecast"]), "fy_plan": float(fy["plan"]),
        "fy_var_pct": (float(fy["forecast"]) / float(fy["plan"]) - 1) if fy["plan"] else 0.0,
    }


def weekly(f):
    w, p = Filters(**{**f.__dict__, "week_from": 1, "week_to": 53}).where(only_actual=False)
    return query(f"""SELECT week, SUM(plan) plan, SUM(actual_prev) actual_prev, SUM(actual) actual, SUM(forecast) forecast
                     FROM v_fact WHERE {w} GROUP BY week ORDER BY week""", p)


def add_variance(df):
    df = df.copy()
    df["var"] = df["actual"] - df["plan"]
    df["var_pct"] = (df["actual"] / df["plan"].where(df["plan"] != 0) - 1).fillna(0)
    df["var_prev_pct"] = (df["actual"] / df["prev"].where(df["prev"] != 0) - 1).fillna(0)
    return df


def by_dim(f, dim, extra_where="", extra_params=None):
    col = DIM_COLUMNS[dim]
    w, p = f.where()
    if extra_where:
        w += " AND " + extra_where
        p = p + list(extra_params or [])
    df = query(f"""SELECT {col} AS entity, SUM(plan) plan, SUM(actual) actual, SUM(actual_prev) prev
                   FROM v_fact WHERE {w} GROUP BY 1 ORDER BY 1""", p)
    return add_variance(df)


def status(var_pct, warn=0.03, bad=0.08):
    if var_pct >= bad:
        return "Over"
    if var_pct >= warn:
        return "Watch"
    return "On plan" if var_pct > -warn else "Under"


def overspend(f, level="store", min_pct=0.05, min_amount=20000, limit=50):
    cols = {"store": ["store_name", "division", "region"], "zone": ["zone", "division", "region"],
            "region": ["region", "division"]}[level]
    w, p = f.where()
    sel = ", ".join(cols)
    df = query(f"""SELECT {sel}, account_group, SUM(plan) plan, SUM(actual) actual
                   FROM v_fact WHERE {w} GROUP BY {sel}, account_group""", p)
    df["var"] = df["actual"] - df["plan"]
    df["var_pct"] = df["actual"] / df["plan"] - 1
    df = df[(df["var_pct"] >= min_pct) & (df["var"] >= min_amount)]
    return df.sort_values("var", ascending=False).head(limit).reset_index(drop=True)


def anomalies(f, min_abs_z=3.5, only_model=False, limit=200):
    w, p = f.where(prefix="v.")
    extra = " AND a.model_agrees" if only_model else ""
    return query(f"""
        SELECT a.week, v.store_name, v.division, v.account_group, v.account, a.actual, a.median_prior,
               a.deviation, a.robust_z, a.method, a.iforest_score, a.model_agrees, a.store_id, a.account_id
        FROM anomalies a
        JOIN v_fact v ON v.store_id = a.store_id AND v.account_id = a.account_id AND v.week = a.week
        WHERE {w} AND ABS(a.robust_z) >= ?{extra}
        ORDER BY ABS(a.deviation) DESC LIMIT {int(limit)}""", p + [min_abs_z])


def series(store_id, account_id):
    return query("SELECT week, plan, actual_prev, actual FROM fact WHERE store_id = ? AND account_id = ? ORDER BY week",
                 [int(store_id), int(account_id)])


MEASURES = {
    "actual": "SUM(actual)", "plan": "SUM(plan)", "prev": "SUM(actual_prev)",
    "var": "SUM(actual) - SUM(plan)", "var_pct": "SUM(actual) / NULLIF(SUM(plan), 0) - 1",
}


def pivot(f, rows, cols, measure):
    w, p = f.where()
    df = query(f"""SELECT {DIM_COLUMNS[rows]} AS r, {DIM_COLUMNS[cols]} AS c, {MEASURES[measure]} AS v
                   FROM v_fact WHERE {w} GROUP BY 1, 2""", p)
    return df.pivot(index="r", columns="c", values="v").fillna(0)


def heat_month_group(f):
    w, p = f.where()
    df = query(f"SELECT account_group, month, SUM(actual) actual, SUM(plan) plan FROM v_fact WHERE {w} GROUP BY 1, 2", p)
    df["var_pct"] = df["actual"] / df["plan"] - 1
    return df.pivot(index="account_group", columns="month", values="var_pct")


def money(x, short=True):
    x = float(x)
    if short:
        for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(x) >= div:
                return f"${x / div:,.2f}{suf}"
    return f"${x:,.0f}"
