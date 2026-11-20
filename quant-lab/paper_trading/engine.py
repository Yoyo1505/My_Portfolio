import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
STATE = DATA / "state.json"
TRADES = DATA / "trades.csv"
EQUITY = DATA / "equity.csv"
EXPORT = BASE.parent.parent / "docs" / "paper-trading" / "data.json"

CONFIG = {
    "tickers": ["META", "NVDA", "DBO", "SPY", "QQQ", "CEG", "SCHD"],
    "benchmark": "SPY",
    "capital": 100_000.0,
    "fast": 20,
    "slow": 100,
    "slippage": 0.0005,
    "commission": 0.0,
}


def load_prices(tickers, years=3):
    px = yf.download(tickers, period=f"{years}y", interval="1d", auto_adjust=True, progress=False)["Close"]
    return px[tickers].dropna(how="all").ffill()


def new_state():
    return {
        "config": CONFIG,
        "cash": CONFIG["capital"],
        "positions": {t: 0.0 for t in CONFIG["tickers"]},
        "pending": {},
        "last_date": None,
        "started": None,
    }


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return new_state()


def signals(px):
    fast = px.rolling(CONFIG["fast"]).mean()
    slow = px.rolling(CONFIG["slow"]).mean()
    return (fast > slow).astype(int).where(slow.notna())


def mark(state, prices_row):
    return state["cash"] + sum(q * float(prices_row[t]) for t, q in state["positions"].items())


def process_day(state, day, px, sig, mode, trades):
    row = px.loc[day]
    for t, side in list(state["pending"].items()):
        price = float(row[t])
        if side == "BUY" and state["positions"][t] == 0:
            fill = price * (1 + CONFIG["slippage"])
            slot = CONFIG["capital"] / len(CONFIG["tickers"])
            qty = round(min(slot, state["cash"]) / fill, 4)
            if qty > 0:
                cost = qty * fill + CONFIG["commission"]
                state["cash"] -= cost
                state["positions"][t] = qty
                trades.append({"date": str(day.date()), "ticker": t, "side": "BUY", "qty": qty,
                               "price": round(fill, 4), "ref_price": round(price, 4), "mode": mode})
        elif side == "SELL" and state["positions"][t] > 0:
            fill = price * (1 - CONFIG["slippage"])
            qty = state["positions"][t]
            state["cash"] += qty * fill - CONFIG["commission"]
            state["positions"][t] = 0.0
            trades.append({"date": str(day.date()), "ticker": t, "side": "SELL", "qty": qty,
                           "price": round(fill, 4), "ref_price": round(price, 4), "mode": mode})
    state["pending"] = {}
    for t in CONFIG["tickers"]:
        s = sig.loc[day, t]
        if pd.isna(s):
            continue
        holding = state["positions"][t] > 0
        if s == 1 and not holding:
            state["pending"][t] = "BUY"
        elif s == 0 and holding:
            state["pending"][t] = "SELL"
    state["last_date"] = str(day.date())
    return mark(state, row)


def run(backfill_days):
    DATA.mkdir(exist_ok=True)
    px = load_prices(CONFIG["tickers"])
    sig = signals(px)
    state = load_state()
    trades, eq_rows = [], []
    today = px.index[-1]

    if state["last_date"] is None:
        start_idx = len(px) - 1 - backfill_days if backfill_days > 0 else len(px) - 1
        days = list(px.index[start_idx:])
        state["started"] = str(days[0].date())
        live_from = today
        eq_old = pd.DataFrame(columns=["date", "equity", "cash", "mode"])
    else:
        last = pd.Timestamp(state["last_date"])
        days = [d for d in px.index if d > last]
        live_from = pd.Timestamp(state["started_live"]) if "started_live" in state else days[0] if days else today
        eq_old = pd.read_csv(EQUITY)

    if not days:
        print("Sin dias nuevos. Ultima fecha procesada:", state["last_date"])
    for day in days:
        mode = "backfill" if day < live_from else "live"
        if mode == "live" and "started_live" not in state:
            state["started_live"] = str(day.date())
        eq = process_day(state, day, px, sig, mode, trades)
        eq_rows.append({"date": str(day.date()), "equity": round(eq, 2), "cash": round(state["cash"], 2), "mode": mode})

    if eq_rows or trades:
        eq_df = pd.concat([eq_old, pd.DataFrame(eq_rows)], ignore_index=True).drop_duplicates("date", keep="last")
        eq_df.to_csv(EQUITY, index=False)
        if trades:
            tr_new = pd.DataFrame(trades)
            if TRADES.exists():
                tr_new = pd.concat([pd.read_csv(TRADES), tr_new], ignore_index=True)
            tr_new.to_csv(TRADES, index=False)
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    export(px, state)
    report(px, state)


def export(px, state):
    eq = pd.read_csv(EQUITY)
    tr = pd.read_csv(TRADES) if TRADES.exists() else pd.DataFrame()
    bench = px[CONFIG["benchmark"]].reindex(pd.to_datetime(eq["date"])).values
    bench_curve = (bench / bench[0] * eq["equity"].iloc[0]).round(2).tolist()
    last_row = px.iloc[-1]
    pos = []
    for t, q in state["positions"].items():
        if q > 0:
            pos.append({"ticker": t, "qty": q, "price": round(float(last_row[t]), 2),
                        "value": round(q * float(last_row[t]), 2)})
    out = {
        "config": CONFIG,
        "started": state["started"],
        "started_live": state.get("started_live"),
        "last_date": state["last_date"],
        "cash": round(state["cash"], 2),
        "pending": state["pending"],
        "positions": pos,
        "equity": eq.to_dict(orient="list"),
        "benchmark": bench_curve,
        "trades": tr.to_dict(orient="records"),
    }
    EXPORT.parent.mkdir(parents=True, exist_ok=True)
    EXPORT.write_text(json.dumps(out), encoding="utf-8")


def report(px, state):
    eq = pd.read_csv(EQUITY)
    r = eq["equity"].pct_change().dropna()
    total = eq["equity"].iloc[-1] / eq["equity"].iloc[0] - 1
    bench = px[CONFIG["benchmark"]].reindex(pd.to_datetime(eq["date"]))
    bench_total = bench.iloc[-1] / bench.iloc[0] - 1
    sharpe = r.mean() / r.std() * np.sqrt(252) if len(r) > 2 and r.std() > 0 else float("nan")
    dd = (eq["equity"] / eq["equity"].cummax() - 1).min()
    print(f"Periodo: {eq['date'].iloc[0]} a {eq['date'].iloc[-1]} ({len(eq)} dias)")
    print(f"Capital final: {eq['equity'].iloc[-1]:,.2f} | Retorno: {total:.2%} | {CONFIG['benchmark']} buy&hold: {bench_total:.2%}")
    print(f"Sharpe anualizado: {sharpe:.2f} | Max drawdown: {dd:.2%}")
    print("Posiciones:", {t: q for t, q in state["positions"].items() if q > 0})
    print("Ordenes pendientes para el proximo dia:", state["pending"])


def reset():
    for f in (STATE, TRADES, EQUITY):
        if f.exists():
            f.unlink()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0)
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if a.reset:
        reset()
    run(a.backfill)
