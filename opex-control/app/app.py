import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, ctx, dash_table, dcc, html, no_update
from dash.dash_table.Format import Format, Scheme

from assistant.cashi import answer
from core import queries as Q
from core.auth import authenticate, can
from core.db import catalogs, meta

app = Dash(__name__, suppress_callback_exceptions=True, title="Opex Control Dashboard",
           assets_folder=str(Path(__file__).resolve().parent / "assets"))
server = app.server
M = meta()
CAT = catalogs()
CUT = M["cutoff_week"]
LEVELS = ["division", "region", "zone", "store"]
LEVEL_COLS = {"division": "division", "region": "region", "zone": "zone", "store": "store_name"}
COLORS = {"actual": "#2563eb", "plan": "#94a3b8", "prev": "#cbd5e1", "forecast": "#f59e0b", "bad": "#dc2626", "good": "#16a34a"}
TABS = [("summary", "Summary"), ("hierarchy", "Hierarchy"), ("accounts", "Accounts"), ("alerts", "Alerts"),
        ("timeline", "Timeline"), ("pivot", "Pivot"), ("assistant", "Cashi assistant")]
EXAMPLES = ["How much did the North division spend in Q2?", "Top 5 stores over plan in Logistics",
            "Variance vs plan for Maintenance in the last 8 weeks", "Weekly trend of Payroll in the South division",
            "Which anomalies happened in week 30?", "Forecast for the full year in Technology", "Who owns Cloud services?"]
MONEY_FMT = dash_table.FormatTemplate.money(0)
PCT_FMT = dash_table.FormatTemplate.percentage(1)


def flt(divs, regs, groups, weeks):
    weeks = weeks or [1, CUT]
    return Q.Filters(divisions=divs or [], regions=regs or [], groups=groups or [], week_from=weeks[0], week_to=weeks[1])


def fig_base(fig, height=340):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, system-ui, sans-serif", size=12, color="#475569"),
                      legend=dict(orientation="h", y=1.12, x=0), hovermode="x unified")
    fig.update_xaxes(gridcolor="rgba(148,163,184,.2)")
    fig.update_yaxes(gridcolor="rgba(148,163,184,.2)")
    return fig


def kpi(title, value, sub=None, tone=""):
    return html.Div([html.Div(title, className="kpi-title"), html.Div(value, className=f"kpi-value {tone}"),
                     html.Div(sub or "", className="kpi-sub")], className="kpi")


def card(title, child, cls=""):
    return html.Div([html.H3(title), child], className=f"card {cls}")


def table(df, id=None, columns=None, page_size=12, **kw):
    cols = columns or [{"name": c, "id": c} for c in df.columns]
    if id:
        kw["id"] = id
    return dash_table.DataTable(
        data=df.to_dict("records"), columns=cols, page_size=page_size, sort_action="native",
        style_table={"overflowX": "auto"}, style_header={"fontWeight": 600, "background": "var(--panel-2)", "border": "0"},
        style_cell={"padding": "8px 10px", "border": "0", "borderBottom": "1px solid var(--line)", "background": "transparent",
                    "color": "var(--text)", "fontFamily": "inherit", "fontSize": "13px", "textAlign": "left"}, **kw)


def money_col(name, id):
    return {"name": name, "id": id, "type": "numeric", "format": MONEY_FMT}


def pct_col(name, id):
    return {"name": name, "id": id, "type": "numeric", "format": PCT_FMT}


VAR_STYLES = [
    {"if": {"filter_query": "{var_pct} >= 0.08", "column_id": "var_pct"}, "color": COLORS["bad"], "fontWeight": 600},
    {"if": {"filter_query": "{var_pct} >= 0.03 && {var_pct} < 0.08", "column_id": "var_pct"}, "color": COLORS["forecast"]},
    {"if": {"filter_query": "{var_pct} <= -0.03", "column_id": "var_pct"}, "color": COLORS["good"]},
]


def login_view():
    return html.Div([html.Div([
        html.Div("Aurora Retail Group", className="brand-sm"), html.H1("Opex Control"),
        html.P("Weekly operating-expense tracking: actual vs plan vs prior year, with anomaly monitoring.", className="muted"),
        dcc.Input(id="login-user", placeholder="Username", type="text", value="", className="inp", n_submit=0),
        dcc.Input(id="login-pass", placeholder="Password", type="password", value="", className="inp", n_submit=0),
        html.Button("Sign in", id="login-btn", className="btn primary"),
        html.Div(id="login-msg", className="login-msg"),
        html.Div("Demo accounts: analyst / analyst123 (viewer) · admin / admin123 (admin)", className="hint"),
    ], className="login-card")], className="login-wrap")


def shell(session):
    regions = sorted(CAT["regions"]["region"].unique())
    return html.Div([
        html.Header([html.Div([html.Span("Aurora Retail Group", className="brand"), html.Span("Opex Control", className="product")]),
                     html.Div([html.Span(f"Cut-off: week {CUT} · {session['name']} ({session['role']})", className="chip"),
                               html.Button("Sign out", id="logout-btn", className="btn ghost")])], className="top"),
        html.Div([
            html.Div([html.Label("Division"), dcc.Dropdown(CAT["divisions"], [], multi=True, id="f-div", placeholder="All")], className="fi"),
            html.Div([html.Label("Region"), dcc.Dropdown(regions, [], multi=True, id="f-reg", placeholder="All")], className="fi"),
            html.Div([html.Label("Account group"), dcc.Dropdown(CAT["groups"], [], multi=True, id="f-grp", placeholder="All")], className="fi"),
            html.Div([html.Label("Weeks"), dcc.RangeSlider(1, 53, 1, value=[1, CUT], id="f-weeks", marks={1: "1", 13: "13", 26: "26", 39: "39", 53: "53"},
                                                           tooltip={"placement": "bottom"})], className="fi wide"),
        ], className="filters"),
        dcc.Tabs(id="tabs", value="summary", children=[dcc.Tab(label=lbl, value=v, className="tab", selected_className="tab-on") for v, lbl in TABS]),
        html.Div(id="tab-body", className="body"),
    ])


app.layout = html.Div([dcc.Store(id="session", storage_type="session"), html.Div(id="root")])


@app.callback(Output("root", "children"), Input("session", "data"))
def root(session):
    return shell(session) if session else login_view()


@app.callback(Output("session", "data"), Output("login-msg", "children"),
              Input("login-btn", "n_clicks"), Input("login-pass", "n_submit"),
              State("login-user", "value"), State("login-pass", "value"), prevent_initial_call=True)
def login(n_btn, n_sub, user, pw):
    s = authenticate(user, pw)
    return (s, "") if s else (no_update, "Invalid username or password")


@app.callback(Output("session", "data", allow_duplicate=True), Input("logout-btn", "n_clicks"), prevent_initial_call=True)
def logout(n):
    return None if n else no_update


@app.callback(Output("tab-body", "children"), Input("tabs", "value"), Input("session", "data"))
def render(tab, session):
    if tab == "summary":
        return html.Div([html.Div(id="kpis", className="kpis"),
                         html.Div([card("Weekly spend: actual, plan, prior year and forecast", dcc.Graph(id="g-weekly", config={"displayModeBar": False}), "span2"),
                                   card("Variance vs plan by division", dcc.Graph(id="g-div", config={"displayModeBar": False}))], className="grid"),
                         card("Variance vs plan by account group", dcc.Graph(id="g-grp", config={"displayModeBar": False}))])
    if tab == "hierarchy":
        return html.Div([html.Div([html.Div(id="crumbs", className="crumbs"), html.Button("Reset", id="h-reset", className="btn ghost"),
                                   html.Button("Download CSV", id="h-dl-btn", className="btn", disabled=not can(session, "download")),
                                   dcc.Download(id="h-dl")], className="toolbar"),
                         html.P("Click a row to drill down: Division > Region > Zone > Store.", className="muted"),
                         dcc.Store(id="h-path", data=[]), html.Div(id="h-table")])
    if tab == "accounts":
        return html.Div([card("Account groups", html.Div(id="a-groups")), card("Accounts", html.Div(id="a-accounts"))])
    if tab == "alerts":
        return html.Div([
            card("Overspend: store and account group above plan (>= 5% and >= $20K)", html.Div(id="al-over")),
            card("Atypical weeks (robust z-score on each store-account history; Isolation Forest as second opinion)", html.Div([
                html.Div([html.Label("Minimum |z|"), dcc.Slider(3.5, 30, 0.5, value=3.5, id="al-z", marks={3.5: "3.5", 10: "10", 20: "20", 30: "30"}),
                          dcc.Checklist([{"label": " Only where the model agrees", "value": "m"}], [], id="al-model", className="chk")], className="fi wide"),
                html.Div(id="al-anom"), dcc.Graph(id="al-series", config={"displayModeBar": False})]))])
    if tab == "timeline":
        return html.Div([card("Variance vs plan: account group x month", dcc.Graph(id="t-heat", config={"displayModeBar": False})),
                         card("Actual vs plan by quarter", dcc.Graph(id="t-q", config={"displayModeBar": False}))])
    if tab == "pivot":
        dims = [{"label": lbl, "value": v} for v, lbl in [("division", "Division"), ("region", "Region"), ("zone", "Zone"), ("account_group", "Account group"),
                                                           ("account", "Account"), ("owner", "Owner"), ("quarter", "Quarter"), ("month", "Month")]]
        return html.Div([html.Div([html.Div([html.Label("Rows"), dcc.Dropdown(dims, "division", id="p-rows", clearable=False)], className="fi"),
                                   html.Div([html.Label("Columns"), dcc.Dropdown(dims, "quarter", id="p-cols", clearable=False)], className="fi"),
                                   html.Div([html.Label("Measure"), dcc.Dropdown([{"label": "Actual", "value": "actual"}, {"label": "Plan", "value": "plan"},
                                                                                   {"label": "Prior year", "value": "prev"}, {"label": "Variance $", "value": "var"},
                                                                                   {"label": "Variance %", "value": "var_pct"}], "var", id="p-measure", clearable=False)], className="fi"),
                                   html.Button("Download CSV", id="p-dl-btn", className="btn", disabled=not can(session, "download")), dcc.Download(id="p-dl")], className="filters inner"),
                         html.Div(id="p-table")])
    if tab == "assistant":
        return html.Div([card("Cashi: ask about spend, variance, anomalies and forecasts (rule-based, no LLM)", html.Div([
            html.Div([dcc.Input(id="c-q", placeholder="Ask a question, e.g. Top 5 stores over plan in Logistics", className="inp grow", n_submit=0, debounce=False),
                      html.Button("Ask", id="c-send", className="btn primary")], className="ask"),
            html.Div([html.Button(e, id={"type": "c-ex", "i": i}, className="chip-btn", n_clicks=0) for i, e in enumerate(EXAMPLES)], className="examples"),
            dcc.Store(id="c-hist", data=[]), html.Div(id="c-thread", className="thread")]))])
    return ""


@app.callback(Output("kpis", "children"), Output("g-weekly", "figure"), Output("g-div", "figure"), Output("g-grp", "figure"),
              Input("f-div", "value"), Input("f-reg", "value"), Input("f-grp", "value"), Input("f-weeks", "value"))
def summary(divs, regs, groups, weeks):
    f = flt(divs, regs, groups, weeks)
    k = Q.kpis(f)
    cards = [kpi("Actual (YTD)", Q.money(k["actual"]), f"Plan {Q.money(k['plan'])}"),
             kpi("Variance vs plan", f"{k['var_plan_pct']:+.1%}", Q.money(k["var_plan"]), "bad" if k["var_plan_pct"] > 0.03 else "good" if k["var_plan_pct"] < -0.03 else ""),
             kpi("Variance vs last year", f"{k['var_prev_pct']:+.1%}", f"Prior {Q.money(k['prev'])}"),
             kpi("Full-year forecast", Q.money(k["forecast"]), f"Plan {Q.money(k['fy_plan'])}"),
             kpi("Forecast vs plan", f"{k['fy_var_pct']:+.1%}", "Run-rate based", "bad" if k["fy_var_pct"] > 0.03 else "")]
    wk = Q.weekly(f)
    fig = go.Figure()
    fig.add_scatter(x=wk["week"], y=wk["actual_prev"], name="Prior year", line=dict(color=COLORS["prev"], width=1.5))
    fig.add_scatter(x=wk["week"], y=wk["plan"], name="Plan", line=dict(color=COLORS["plan"], width=2, dash="dash"))
    fig.add_scatter(x=wk["week"], y=wk["actual"], name="Actual", line=dict(color=COLORS["actual"], width=2.5))
    fut = wk[wk["week"] >= CUT]
    fig.add_scatter(x=fut["week"], y=fut["forecast"], name="Forecast", line=dict(color=COLORS["forecast"], width=2, dash="dot"))
    fig.add_vline(x=CUT, line=dict(color="#64748b", width=1, dash="dot"), annotation_text="cut-off")
    d = Q.by_dim(f, "division").sort_values("var")
    fd = go.Figure(go.Bar(x=d["var"], y=d["entity"], orientation="h", marker_color=[COLORS["bad"] if v > 0 else COLORS["good"] for v in d["var"]],
                          text=[f"{p:+.1%}" for p in d["var_pct"]], hovertemplate="%{y}: %{x:$,.0f}<extra></extra>"))
    g = Q.by_dim(f, "account_group").sort_values("var_pct")
    fg = go.Figure(go.Bar(x=g["entity"], y=g["var_pct"], marker_color=[COLORS["bad"] if v > 0.03 else COLORS["good"] if v < -0.03 else "#94a3b8" for v in g["var_pct"]],
                          text=[f"{p:+.1%}" for p in g["var_pct"]], hovertemplate="%{x}: %{y:+.1%}<extra></extra>"))
    fg.update_yaxes(tickformat=".0%")
    return cards, fig_base(fig), fig_base(fd, 300), fig_base(fg, 300)


@app.callback(Output("h-path", "data"), Input("h-grid", "active_cell"), Input("h-reset", "n_clicks"),
              State("h-grid", "data"), State("h-path", "data"), prevent_initial_call=True)
def drill(cell, reset, data, path):
    if ctx.triggered_id == "h-reset":
        return []
    if cell and len(path) < len(LEVELS) - 1:
        return path + [data[cell["row"]]["entity"]]
    return no_update


@app.callback(Output("h-table", "children"), Output("crumbs", "children"), Input("h-path", "data"),
              Input("f-div", "value"), Input("f-reg", "value"), Input("f-grp", "value"), Input("f-weeks", "value"))
def hier(path, divs, regs, groups, weeks):
    path = path or []
    f = flt(divs, regs, groups, weeks)
    level = LEVELS[len(path)]
    where, params = [], []
    for lv, val in zip(LEVELS, path):
        where.append(f"{LEVEL_COLS[lv]} = ?")
        params.append(val)
    df = Q.by_dim(f, level, " AND ".join(where), params)
    df["status"] = df["var_pct"].map(Q.status)
    df = df.sort_values("var", ascending=False)
    cols = [{"name": level.capitalize(), "id": "entity"}, money_col("Plan", "plan"), money_col("Actual", "actual"), money_col("Prior year", "prev"),
            money_col("Var $", "var"), pct_col("Var %", "var_pct"), pct_col("vs PY %", "var_prev_pct"), {"name": "Status", "id": "status"}]
    grid = table(df, id="h-grid", columns=cols, page_size=15, style_data_conditional=VAR_STYLES + [
        {"if": {"filter_query": '{status} = "Over"', "column_id": "status"}, "color": COLORS["bad"], "fontWeight": 600}])
    crumbs = " > ".join(["All"] + path + ([""] if len(path) < len(LEVELS) - 1 else []))
    return grid, f"Level: {level.capitalize()}  |  {crumbs}"


@app.callback(Output("h-dl", "data"), Input("h-dl-btn", "n_clicks"), State("h-grid", "data"), State("session", "data"), prevent_initial_call=True)
def dl_hier(n, data, session):
    if not can(session, "download"):
        return no_update
    return dcc.send_data_frame(pd.DataFrame(data).to_csv, "hierarchy.csv", index=False)


@app.callback(Output("a-groups", "children"), Output("a-accounts", "children"),
              Input("f-div", "value"), Input("f-reg", "value"), Input("f-grp", "value"), Input("f-weeks", "value"))
def accounts(divs, regs, groups, weeks):
    f = flt(divs, regs, groups, weeks)
    cols = [{"name": "Name", "id": "entity"}, money_col("Plan", "plan"), money_col("Actual", "actual"), money_col("Prior year", "prev"),
            money_col("Var $", "var"), pct_col("Var %", "var_pct"), pct_col("vs PY %", "var_prev_pct")]
    g = Q.by_dim(f, "account_group").sort_values("var", ascending=False)
    a = Q.by_dim(f, "account").merge(CAT["accounts"].rename(columns={"account": "entity"}), on="entity").sort_values("var", ascending=False)
    a["entity"] = a["entity"] + "  (" + a["account_group"] + ", " + a["owner"] + ")"
    return table(g, columns=cols, page_size=10, style_data_conditional=VAR_STYLES), table(a.drop(columns=["account_group", "owner"]), columns=cols, page_size=12, style_data_conditional=VAR_STYLES)


@app.callback(Output("al-over", "children"), Output("al-anom", "children"),
              Input("f-div", "value"), Input("f-reg", "value"), Input("f-grp", "value"), Input("f-weeks", "value"), Input("al-z", "value"), Input("al-model", "value"))
def alerts(divs, regs, groups, weeks, z, model):
    f = flt(divs, regs, groups, weeks)
    o = Q.overspend(f, limit=25)
    ocols = [{"name": "Store", "id": "store_name"}, {"name": "Division", "id": "division"}, {"name": "Region", "id": "region"}, {"name": "Group", "id": "account_group"},
             money_col("Plan", "plan"), money_col("Actual", "actual"), money_col("Var $", "var"), pct_col("Var %", "var_pct")]
    an = Q.anomalies(f, min_abs_z=z or 3.5, only_model=bool(model), limit=100)
    an["model_agrees"] = an["model_agrees"].map({True: "yes", False: "no"})
    acols = [{"name": "Week", "id": "week"}, {"name": "Store", "id": "store_name"}, {"name": "Account", "id": "account"}, money_col("Actual", "actual"),
             money_col("Prior median", "median_prior"), {"name": "Robust z", "id": "robust_z", "type": "numeric", "format": Format(precision=1, scheme=Scheme.fixed)},
             {"name": "Model agrees", "id": "model_agrees"}]
    anom = table(an, id="al-grid", columns=acols, page_size=8, row_selectable=False, style_data_conditional=[
        {"if": {"filter_query": '{model_agrees} = "yes"', "column_id": "model_agrees"}, "color": COLORS["bad"], "fontWeight": 600}])
    return table(o, columns=ocols, page_size=8, style_data_conditional=VAR_STYLES), html.Div([html.P(f"{len(an)} atypical weeks. Click a row to see its series.", className="muted"), anom])


@app.callback(Output("al-series", "figure"), Input("al-grid", "active_cell"), State("al-grid", "data"), prevent_initial_call=True)
def anomaly_series(cell, data):
    if not cell:
        return go.Figure()
    full = Q.anomalies(Q.Filters(), min_abs_z=3.5, limit=2000)
    row = data[cell["row"]]
    m = full[(full["week"] == row["week"]) & (full["store_name"] == row["store_name"]) & (full["account"] == row["account"])].iloc[0]
    s = Q.series(m["store_id"], m["account_id"])
    fig = go.Figure()
    fig.add_scatter(x=s["week"], y=s["actual_prev"], name="Prior year", line=dict(color=COLORS["prev"]))
    fig.add_scatter(x=s["week"], y=s["plan"], name="Plan", line=dict(color=COLORS["plan"], dash="dash"))
    fig.add_scatter(x=s["week"], y=s["actual"], name="Actual", line=dict(color=COLORS["actual"], width=2.5))
    fig.add_scatter(x=[m["week"]], y=[m["actual"]], name="Flagged week", mode="markers", marker=dict(color=COLORS["bad"], size=12))
    fig.update_layout(title=f"{m['store_name']} - {m['account']}")
    return fig_base(fig, 320)


@app.callback(Output("t-heat", "figure"), Output("t-q", "figure"),
              Input("f-div", "value"), Input("f-reg", "value"), Input("f-grp", "value"), Input("f-weeks", "value"))
def timeline(divs, regs, groups, weeks):
    f = flt(divs, regs, groups, weeks)
    h = Q.heat_month_group(f)
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    fig = go.Figure(go.Heatmap(z=h.values, x=[names[m - 1] for m in h.columns], y=h.index, zmid=0, colorscale="RdYlGn_r",
                               text=[[f"{v:+.1%}" for v in r] for r in h.values], texttemplate="%{text}", hovertemplate="%{y} %{x}: %{z:+.1%}<extra></extra>"))
    qd = Q.by_dim(f, "quarter")
    fq = go.Figure()
    fq.add_bar(x=["Q" + str(int(e)) for e in qd["entity"]], y=qd["plan"], name="Plan", marker_color=COLORS["plan"])
    fq.add_bar(x=["Q" + str(int(e)) for e in qd["entity"]], y=qd["actual"], name="Actual", marker_color=COLORS["actual"])
    fq.update_layout(barmode="group")
    return fig_base(fig, 360), fig_base(fq, 300)


@app.callback(Output("p-table", "children"), Input("p-rows", "value"), Input("p-cols", "value"), Input("p-measure", "value"),
              Input("f-div", "value"), Input("f-reg", "value"), Input("f-grp", "value"), Input("f-weeks", "value"))
def pivot(rows, cols, measure, divs, regs, groups, weeks):
    if rows == cols:
        return html.P("Choose different dimensions for rows and columns.", className="muted")
    pv = Q.pivot(flt(divs, regs, groups, weeks), rows, cols, measure)
    pv.columns = [str(c) for c in pv.columns]
    pv = pv.reset_index().rename(columns={"r": rows})
    pv[rows] = pv[rows].astype(str)
    fmt = PCT_FMT if measure == "var_pct" else MONEY_FMT
    columns = [{"name": rows.replace("_", " ").title(), "id": rows}] + [{"name": c, "id": c, "type": "numeric", "format": fmt} for c in pv.columns[1:]]
    return table(pv, id="p-grid", columns=columns, page_size=20)


@app.callback(Output("p-dl", "data"), Input("p-dl-btn", "n_clicks"), State("p-grid", "data"), State("session", "data"), prevent_initial_call=True)
def dl_pivot(n, data, session):
    if not can(session, "download"):
        return no_update
    return dcc.send_data_frame(pd.DataFrame(data).to_csv, "pivot.csv", index=False)


@app.callback(Output("c-hist", "data"), Output("c-q", "value"),
              Input("c-send", "n_clicks"), Input("c-q", "n_submit"), Input({"type": "c-ex", "i": ALL}, "n_clicks"),
              State("c-q", "value"), State("c-hist", "data"), State("session", "data"), prevent_initial_call=True)
def chat(n, sub, ex, text, hist, session):
    trig = ctx.triggered_id
    if isinstance(trig, dict):
        if not any(ex):
            return no_update, no_update
        text = EXAMPLES[trig["i"]]
    if not text or not text.strip():
        return no_update, no_update
    a = answer(text)
    entry = {"q": text, "text": a.text, "intent": a.intent, "entities": a.entities, "confidence": a.confidence,
             "table": None if a.table is None else a.table.head(25).round(2).to_dict("records"),
             "sql": a.sql if can(session, "show_sql") else ""}
    return (hist or []) + [entry], ""


@app.callback(Output("c-thread", "children"), Input("c-hist", "data"))
def thread(hist):
    out = []
    for e in reversed(hist or []):
        body = [dcc.Markdown(e["text"])]
        if e["table"]:
            df = pd.DataFrame(e["table"])
            body.append(table(df, page_size=8))
        body.append(html.Div(f"intent: {e['intent']} · confidence {e['confidence']:.2f}", className="meta"))
        if e["sql"]:
            body.append(html.Details([html.Summary("SQL (admin)"), html.Pre(e["sql"])]))
        out.append(html.Div([html.Div(e["q"], className="bubble user"), html.Div(body, className="bubble bot")], className="turn"))
    return out


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8050))
    try:
        from waitress import serve
        serve(server, host="127.0.0.1", port=port, threads=8)
    except ImportError:
        app.run(host="127.0.0.1", port=port, debug=False)
