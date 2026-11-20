# Weekly Spend Control Dashboard (Dash + DuckDB)

Production-style weekly spend dashboard for a multi-division retail group, with a data pipeline, an analytics warehouse, a multi-tab Plotly Dash app, role-based users, Excel/PDF exports and a rule-based chat assistant (Cashi) that needs no LLM.

This folder is the source code only. Company identifiers, credentials, internal hostnames, people, catalogs, real figures and branded assets were removed or replaced with neutral placeholders (`Aurora`, `<SHARED_DRIVE>`, `sql-server.example.local`). There is **no data**, so the app cannot start until you provide the inputs described in [RECONSTRUIR.md](RECONSTRUIR.md). For a runnable, fully synthetic version of the same ideas see [`../opex-control`](../opex-control), which includes a recorded tour.

## What is inside

| Path | What it does |
|------|--------------|
| `codigo/build_data.py` | Builds the consolidated fact table, the aggregates and `dashboard.duckdb` from weekly extracts and catalogs |
| `codigo/extraer_sql.py` | Pulls weekly actual, plan and forecast tables from SQL Server (credentials only from `.env`) |
| `codigo/aggs_duckdb.py`, `build_series.py`, `build_digitales*.py` | Aggregates and the digital-channel database |
| `codigo/pruebas_cuadre.py` | Reconciliation checks against the raw extracts |
| `dash_app/` | The app: `pages/` (one per tab), `services/` (DuckDB access, auth, exports, caching), `components/`, `assets/` (CSS, JS, fonts) |
| `rag/` | Cashi assistant: regex intent router, SQL templates, local entity ML, BM25 over a knowledge file, variance and anomaly monitoring (`rag/monitoreo/`) |
| `INICIAR_DASH.bat`, `MENU_DASH.bat` | Windows launchers for the app and the update menu |

## Engineering notes

- The app serves only from DuckDB: one shared connection, a cursor and a lock per query, a bounded memory and thread budget.
- The cut-off week is data (`aggs/_meta.json`), never a constant in code.
- Dual amounts (constant and local currency) are carried side by side; reconciliation tests compare the dashboard to the raw extracts.
- Cashi has no language model: a router, SQL templates and a small local classifier for entities, with a hand-labelled evaluation set.
- Anomaly monitoring combines a robust z-score (median and MAD, IQR fallback, materiality floor) with an Isolation Forest, a champion and challenger registry and a drift check.

## What was left out

Real data and catalogs, the company logo and background image (replaced by generated placeholders), the PowerPoint and Excel templates, change logs and design notes that contain real figures and people, and the hand-labelled evaluation files built from real user questions. Some modules that depend on those templates (`codigo/ppt_semanal`) are included for reference but cannot run without them.

