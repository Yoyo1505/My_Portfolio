# Fintech Analytics & AI Demos

Applied finance projects with live, browser-only demos (GitHub Pages) and the source behind each one.

| Demo | What it shows | Live page | Source |
|------|---------------|-----------|--------|
| Territory Dashboard | KPIs, territorial drill-down, account trees, closings, quarters (synthetic data) | `docs/dashboard/` | `dashboard/` (Streamlit) |
| Cashi RAG chatbot | Retrieval over docs and code chunks with entity detection | `docs/chatbot/` | `chatbot/` (Streamlit + CLI) |
| Paper trading | 20/100 moving-average strategy on META, NVDA, DBO, SPY, QQQ, CEG, SCHD with 100,000 USD of simulated capital | `docs/paper-trading/` | `quant-lab/paper_trading/` |
| Quantitative finance lab | Executed notebook: VaR/CVaR, Kupiec, Markowitz, ARIMA/GARCH, Monte Carlo, Black-Scholes, backtesting, pairs trading, HMM, SGD | `docs/quant-lab/` | `quant-lab/finanzas_cuantitativas.ipynb` |

## Deploy

1. Push to GitHub and set **Settings → Pages → Source: GitHub Actions**.
2. `.github/workflows/pages.yml` publishes `docs/` on every push that touches it.
3. Pages URL: `https://<user>.github.io/<repo>/`.

Preview locally: `python -m http.server --directory docs 8000`.

## Paper trading

```bash
cd quant-lab
pip install -r requirements.txt
python paper_trading/engine.py --backfill 120   # first run: rebuild 120 days (tagged "backfill"), then "live"
python paper_trading/engine.py                  # every later day: process new closes only
```

Rules: signal at the close of day *t*, fill at the close of *t+1* with 5 bp slippage, equal-weight slots, long only, no leverage. State lives in `quant-lab/paper_trading/data/` (`state.json`, `trades.csv`, `equity.csv`) and the page reads `docs/paper-trading/data.json`.
`.github/workflows/paper-trading.yml` runs the update every weekday after the US close and commits the ledger.
Only the `live` rows are an out-of-sample record; `backfill` rows are a replay with the same rules.

No broker keys are needed. To route orders to Alpaca paper trading, keep keys in a local `.env` (git-ignored) and never in the notebook.

## Dashboard and chatbot

```bash
cd dashboard && pip install -r requirements.txt && python seed_demo_data.py && streamlit run app.py
cd chatbot   && pip install -r requirements.txt && streamlit run app.py
```

Refresh the static exports with `python docs/_export_demo_data.py`.

## Notes

- All dashboard and chatbot data is synthetic. No credentials, corporate data or customer data are included.
- Educational material, not financial advice. Paper results do not predict real performance.
