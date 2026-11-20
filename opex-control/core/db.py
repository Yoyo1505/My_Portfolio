import json
import os
import threading
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("OPEX_DB", ROOT / "data" / "warehouse" / "opex.duckdb"))
_lock = threading.RLock()
_con = None
_cache = {}


def connection():
    global _con
    with _lock:
        if _con is None:
            _con = duckdb.connect(str(DB_PATH), read_only=True)
            _con.execute(f"SET threads={int(os.environ.get('DUCKDB_THREADS', 4))}")
        return _con


def query(sql, params=None):
    with _lock:
        cur = connection().cursor()
        try:
            return cur.execute(sql, params or []).df()
        finally:
            cur.close()


def meta():
    return json.loads((DB_PATH.parent / "_meta.json").read_text(encoding="utf-8"))


def cached(key, fn):
    if key not in _cache:
        _cache[key] = fn()
    return _cache[key]


def catalogs():
    def load():
        return {
            "divisions": query("SELECT DISTINCT division FROM dim_store ORDER BY 1")["division"].tolist(),
            "regions": query("SELECT DISTINCT division, region FROM dim_store ORDER BY 1, 2"),
            "zones": query("SELECT DISTINCT zone FROM dim_store ORDER BY 1")["zone"].tolist(),
            "groups": query("SELECT DISTINCT account_group FROM dim_account ORDER BY 1")["account_group"].tolist(),
            "accounts": query("SELECT account, account_group, owner FROM dim_account ORDER BY 2, 1"),
            "stores": query("SELECT store_id, store_name, division, region, zone FROM dim_store ORDER BY 1"),
        }
    return cached("catalogs", load)
