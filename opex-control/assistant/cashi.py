import difflib
import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from core import queries as Q
from core.db import catalogs, meta, query

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
QUARTERS = {1: (1, 13), 2: (14, 26), 3: (27, 39), 4: (40, 53)}
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

INTENT_PATTERNS = [
    ("help", r"\b(help|what can you|how do i use|examples?)\b"),
    ("anomalies", r"\b(anomal\w*|unusual|outliers?|spikes?|atypical|abnormal|strange)\b"),
    ("owner", r"\b(who owns|owner|responsible|accountable|in charge)\b"),
    ("forecast", r"\b(forecast|full[- ]year|year[- ]end|projected|projection|will close|close the year|end of (the )?year|expected to close)\b"),
    ("top_overspend", r"\b(overspend\w*|exceed\w*|biggest|largest|highest|worst|top \d+|top (one|two|three|four|five|six|seven|eight|nine|ten))\b"),
    ("trend", r"\b(trend|weekly|week by week|by week|evolution|over time|per week)\b"),
    ("vs_prior", r"\b(last year|prior year|previous year|yoy|year over year|vs\.? 2025|compared to 2025)\b"),
    ("vs_plan", r"\b(over (plan|budget)|vs\.? plan|versus plan|against plan|variance|budget|over plan|under plan|compared to plan)\b"),
    ("list_entities", r"\b(list|which stores|stores in|how many stores|show me the stores)\b"),
    ("total", r"\b(spend|spent|spending|cost|costs|expense|expenses|total|how much|actual)\b"),
]


@dataclass
class Parsed:
    intent: str
    filters: Q.Filters
    top_n: int = 10
    dimension: Optional[str] = None
    entities: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class Answer:
    text: str
    table: Optional[pd.DataFrame] = None
    sql: str = ""
    intent: str = ""
    entities: dict = field(default_factory=dict)
    confidence: float = 1.0
    suggestions: list = field(default_factory=list)


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", s.lower())


def _find_names(q, names, cutoff=0.86):
    ql = _norm(q)
    hits = []
    for n in sorted(set(names), key=len, reverse=True):
        nl = _norm(n).strip()
        if nl and re.search(rf"\b{re.escape(nl)}\b", ql):
            hits.append(n)
            ql = ql.replace(nl, " ")
    return hits, ql


def _extract_weeks(q, cutoff):
    ql = q.lower()
    m = re.search(r"last (\d+|%s) weeks?" % "|".join(WORD_NUM), ql)
    if m:
        n = WORD_NUM.get(m.group(1)) or int(m.group(1))
        return max(1, cutoff - n + 1), cutoff, f"last {n} weeks"
    m = re.search(r"weeks? (\d+)\s*(?:to|through|-|and)\s*(\d+)", ql)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return a, b, f"weeks {a}-{b}"
    m = re.search(r"\bweek (\d+)\b", ql)
    if m:
        w = int(m.group(1))
        return w, w, f"week {w}"
    m = re.search(r"\bq([1-4])\b", ql)
    if m:
        a, b = QUARTERS[int(m.group(1))]
        return a, min(b, 53), f"Q{m.group(1)}"
    for name, num in MONTHS.items():
        if re.search(rf"\b{name}\b", ql):
            r = query("SELECT MIN(week) a, MAX(week) b FROM fact WHERE month = ?", [num]).iloc[0]
            return int(r["a"]), int(r["b"]), name.capitalize()
    return 1, None, ""


def _top_n(q):
    m = re.search(r"top (\d+)", q.lower())
    if m:
        return max(1, min(50, int(m.group(1))))
    m = re.search(r"top (%s)" % "|".join(WORD_NUM), q.lower())
    return WORD_NUM[m.group(1)] if m else 10


def _dimension(q, intent):
    ql = q.lower()
    table = (("account group", "account_group"), ("groups", "account_group"), ("stores", "store"), ("zones", "zone"),
             ("regions", "region"), ("divisions", "division"), ("accounts", "account"), ("owners", "owner"),
             ("store", "store"), ("zone", "zone"), ("region", "region"), ("division", "division"),
             ("account", "account"), ("group", "account_group"), ("owner", "owner"))
    if intent == "top_overspend":
        for key, dim in table:
            if re.search(rf"\b{key}\b", ql):
                return dim
        return None
    m = re.search(r"\b(?:by|per|each|across|breakdown of|broken down by)\s+(account group|store|zone|region|division|account|group|owner)s?\b", ql)
    if m:
        return dict(table)[m.group(1)]
    return None


def parse(question):
    cat = catalogs()
    cutoff = meta()["cutoff_week"]
    q = question.strip()
    intent, conf = None, 1.0
    if re.search(r"^(which|what) (stores|zones|regions|divisions|accounts|account groups|groups|owners)"+r"\b", q.strip(), re.I) and re.search(r"over (plan|budget)", q, re.I):
        intent = "top_overspend"
    for name, pat in ([] if intent else INTENT_PATTERNS):
        if re.search(pat, q, re.I):
            intent = name
            break
    if intent is None:
        intent, conf = "total", 0.4
    f = Q.Filters()
    notes, ents = [], {}
    f.divisions, _ = _find_names(q, cat["divisions"])
    regions, _ = _find_names(q, cat["regions"]["region"].tolist())
    f.regions = regions
    zones, _ = _find_names(q, cat["zones"])
    f.zones = zones
    groups, _ = _find_names(q, cat["groups"])
    f.groups = groups
    accounts, _ = _find_names(q, cat["accounts"]["account"].tolist())
    f.accounts = accounts
    stores, _ = _find_names(q, cat["stores"]["store_name"].tolist())
    for sid in re.findall(r"\b(1\d{3})\b", q):
        row = cat["stores"][cat["stores"]["store_id"] == int(sid)]
        if len(row):
            stores.append(row.iloc[0]["store_name"])
    f.stores = list(dict.fromkeys(stores))
    if f.stores:
        f.zones, f.regions, f.divisions = [], [], []
    f.week_from, f.week_to, label = _extract_weeks(q, cutoff)
    if label:
        ents["period"] = label
    for attr in ("divisions", "regions", "zones", "stores", "groups", "accounts"):
        if getattr(f, attr):
            ents[attr] = getattr(f, attr)
    if not any(k in ents for k in ("divisions", "regions", "zones", "stores", "groups", "accounts")):
        words = [w for w in _norm(q).split() if len(w) > 4]
        pool = cat["divisions"] + cat["regions"]["region"].tolist() + cat["groups"] + cat["accounts"]["account"].tolist()
        for w in words:
            close = difflib.get_close_matches(w, [p.lower() for p in pool], n=1, cutoff=0.8)
            if close:
                notes.append(f"Did you mean '{[p for p in pool if p.lower() == close[0]][0]}'?")
                conf = min(conf, 0.6)
                break
    return Parsed(intent, f, _top_n(q), _dimension(q, intent), ents, notes, conf)


def _fmt_sql(sql, params):
    out = " ".join(sql.split())
    for p in params:
        out = out.replace("?", repr(p) if isinstance(p, str) else str(p), 1)
    return out


def _scope_label(p):
    parts = []
    for key in ("stores", "zones", "regions", "divisions", "groups", "accounts"):
        if key in p.entities:
            parts.append(", ".join(p.entities[key]))
    label = " / ".join(parts) if parts else "the whole company"
    if "period" in p.entities:
        label += f" ({p.entities['period']})"
    return label


def _period_text(p, cutoff):
    hi = min(p.filters.week_to or cutoff, cutoff)
    return f"weeks {p.filters.week_from}-{hi}"


def answer(question):
    p = parse(question)
    cutoff = meta()["cutoff_week"]
    f, scope = p.filters, _scope_label(p)
    w, params = f.where()
    ans = Answer("", intent=p.intent, entities=p.entities, confidence=p.confidence, suggestions=p.notes)
    if p.intent == "help":
        ans.text = ("I answer questions about spend against plan and last year. Try:\n"
                    "- How much did the North division spend in Q2?\n"
                    "- Top 5 stores over plan in Logistics\n"
                    "- Variance vs plan for Maintenance in the last 8 weeks\n"
                    "- Weekly trend of Payroll in the South division\n"
                    "- Which anomalies happened in week 30?\n"
                    "- Forecast for the full year in Technology\n"
                    "- Who owns Cloud services?")
        return ans
    if p.intent == "total":
        sql = f"SELECT SUM(actual) actual FROM v_fact WHERE {w}"
        v = float(query(sql, params).iloc[0, 0] or 0)
        ans.sql = _fmt_sql(sql, params)
        ans.text = f"**{Q.money(v)}** actual spend for {scope}, {_period_text(p, cutoff)}."
        if p.dimension:
            ans.table = _breakdown(f, p.dimension)
            ans.text += f" Breakdown by {p.dimension.replace('_', ' ')} below."
    elif p.intent in ("vs_plan", "vs_prior"):
        k = Q.kpis(f)
        sql = f"SELECT SUM(actual), SUM(plan), SUM(actual_prev) FROM v_fact WHERE {w}"
        ans.sql = _fmt_sql(sql, params)
        if p.intent == "vs_plan":
            ans.text = (f"{scope}, {_period_text(p, cutoff)}: actual **{Q.money(k['actual'])}** vs plan **{Q.money(k['plan'])}**, "
                        f"a variance of **{Q.money(k['var_plan'])} ({k['var_plan_pct']:+.1%})** — {Q.status(k['var_plan_pct']).lower()}.")
        else:
            ans.text = (f"{scope}, {_period_text(p, cutoff)}: actual **{Q.money(k['actual'])}** vs last year **{Q.money(k['prev'])}** "
                        f"({k['var_prev_pct']:+.1%}).")
        if p.dimension:
            ans.table = _breakdown(f, p.dimension)
    elif p.intent == "forecast":
        k = Q.kpis(Q.Filters(**{**f.__dict__, "week_from": 1, "week_to": None}))
        ans.sql = "SELECT SUM(forecast), SUM(plan) FROM v_fact WHERE <filters>"
        ans.text = (f"Full-year forecast for {scope}: **{Q.money(k['forecast'])}** vs plan **{Q.money(k['fy_plan'])}** "
                    f"({k['fy_var_pct']:+.1%}). Weeks after {cutoff} use the plan scaled by the last 8-week run rate.")
    elif p.intent == "top_overspend":
        dim = p.dimension or "store"
        if dim in ("store", "zone", "region"):
            df = Q.overspend(f, level=dim, min_pct=0.0, min_amount=0, limit=p.top_n)
            df = df.rename(columns={df.columns[0]: "entity"})[["entity", "account_group", "plan", "actual", "var", "var_pct"]] if len(df) else df
            sql_note = "variance by entity and account group"
        else:
            df = _breakdown(f, dim).sort_values("var", ascending=False).head(p.top_n)
            sql_note = "variance by " + dim
        ans.table, ans.sql = df, f"-- top {p.top_n}, {sql_note}\nSELECT ..., SUM(actual)-SUM(plan) var FROM v_fact WHERE {_fmt_sql(w, params)} GROUP BY ... ORDER BY var DESC"
        ans.text = f"Top {len(df)} {dim.replace('_', ' ')}s over plan for {scope}, {_period_text(p, cutoff)}."
    elif p.intent == "trend":
        wk = Q.weekly(f)
        wk = wk[wk["week"] <= cutoff]
        ans.table = wk.assign(var=wk["actual"] - wk["plan"])
        ans.sql = f"SELECT week, SUM(plan), SUM(actual) FROM v_fact WHERE {_fmt_sql(w, params)} GROUP BY week"
        if len(wk):
            last = wk.iloc[-1]
            ans.text = (f"Weekly trend for {scope}: week {int(last['week'])} was **{Q.money(last['actual'])}** vs plan {Q.money(last['plan'])}. "
                        f"Average weekly actual in the period: {Q.money(wk['actual'].mean())}.")
        else:
            ans.text = f"No data for {scope} in that period."
    elif p.intent == "anomalies":
        df = Q.anomalies(f, limit=15)
        ans.table = df[["week", "store_name", "account", "actual", "median_prior", "robust_z", "model_agrees"]] if len(df) else df
        ans.sql = "SELECT ... FROM anomalies JOIN v_fact ... WHERE ABS(robust_z) >= 3.5 ORDER BY ABS(deviation) DESC"
        ans.text = (f"{len(df)} atypical store-account weeks for {scope}, {_period_text(p, cutoff)} "
                    f"(robust z >= 3.5 and deviation >= $4,000; 'model_agrees' = Isolation Forest also flags it)."
                    if len(df) else f"No atypical weeks found for {scope}, {_period_text(p, cutoff)}.")
    elif p.intent == "owner":
        cat = catalogs()["accounts"]
        sel = cat
        if f.accounts:
            sel = cat[cat["account"].isin(f.accounts)]
        elif f.groups:
            sel = cat[cat["account_group"].isin(f.groups)]
        ans.table = sel
        ans.sql = "SELECT account, account_group, owner FROM dim_account"
        ans.text = ("Account owners:" if len(sel) < len(cat) else "I could not tell which account you mean; here are all owners:")
    elif p.intent == "list_entities":
        st = catalogs()["stores"]
        for key, col in (("divisions", "division"), ("regions", "region"), ("zones", "zone")):
            if getattr(f, key):
                st = st[st[col].isin(getattr(f, key))]
        ans.table = st
        ans.sql = "SELECT * FROM dim_store"
        ans.text = f"{len(st)} stores in {scope}."
    ans.table = ans.table.reset_index(drop=True) if ans.table is not None else None
    if p.confidence < 0.5:
        ans.text += "\n\n_I was not sure what you meant, so I assumed total actual spend. Type `help` for examples._"
    return ans


def _breakdown(f, dim):
    df = Q.by_dim(f, dim)
    return df.sort_values("actual", ascending=False).reset_index(drop=True)
