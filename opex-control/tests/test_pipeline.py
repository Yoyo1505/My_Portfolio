import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core import queries as Q
from core.db import meta, query
from monitoring.anomalies import detect, robust_z

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"


@pytest.fixture(scope="module")
def raw():
    return pd.read_parquet(RAW / "fact_weekly.parquet")


def test_warehouse_reconciles_with_raw(raw):
    cutoff = meta()["cutoff_week"]
    ytd = raw[raw["week"] <= cutoff]
    k = Q.kpis(Q.Filters())
    assert k["actual"] == pytest.approx(ytd["actual"].sum(), rel=1e-9)
    assert k["plan"] == pytest.approx(ytd["plan"].sum(), rel=1e-9)
    assert k["prev"] == pytest.approx(ytd["actual_prev"].sum(), rel=1e-9)
    assert k["forecast"] == pytest.approx(raw["forecast"].sum(), rel=1e-9)


def test_division_totals_add_up_to_company_total():
    parts = Q.by_dim(Q.Filters(), "division")
    assert parts["actual"].sum() == pytest.approx(Q.kpis(Q.Filters())["actual"], rel=1e-9)


def test_no_actuals_after_cutoff(raw):
    cutoff = meta()["cutoff_week"]
    assert raw.loc[raw["week"] > cutoff, "actual"].isna().all()
    assert (raw.loc[raw["week"] > cutoff, "forecast"] > 0).all()


def test_forecast_equals_actual_up_to_cutoff(raw):
    d = raw[raw["is_actual"]]
    assert np.allclose(d["forecast"], d["actual"])


def test_filters_use_bound_parameters():
    f = Q.Filters(divisions=["North'; DROP TABLE fact; --"])
    assert Q.kpis(f)["actual"] == 0
    assert query("SELECT COUNT(*) n FROM fact").iloc[0, 0] > 0


def test_weekly_filter_range():
    k_all = Q.kpis(Q.Filters())["actual"]
    k_part = Q.kpis(Q.Filters(week_from=10, week_to=20))["actual"]
    assert 0 < k_part < k_all


def test_robust_z_is_scale_free_and_handles_zero_mad():
    hist = np.array([[10, 10, 10, 10, 10, 10, 10, 10.0], [100, 110, 90, 105, 95, 100, 102, 98.0]])
    z, med, method = robust_z(hist, np.array([20.0, 100.0]))
    assert method[0] in ("IQR", "RELATIVE_FLOOR")
    assert z[0] > 3.5 and abs(z[1]) < 1


def test_detector_has_no_lookahead(raw):
    cutoff = meta()["cutoff_week"]
    base = detect(raw, cutoff - 5)
    modified = raw.copy()
    late = modified["week"] > cutoff - 5
    modified.loc[late & modified["actual"].notna(), "actual"] *= 50
    again = detect(modified, cutoff - 5)
    pd.testing.assert_frame_equal(base.reset_index(drop=True), again.reset_index(drop=True))


def test_detector_finds_material_injected_anomalies(raw):
    injected = json.loads((RAW / "injected_anomalies.json").read_text())
    found = query("SELECT store_id, account_id, week FROM anomalies")
    found_set = set(map(tuple, found.to_numpy().tolist()))
    material = []
    for it in injected:
        r = raw[(raw["store_id"] == it["store_id"]) & (raw["account_id"] == it["account_id"]) & (raw["week"] == it["week"])].iloc[0]
        if r["actual"] - r["plan"] > 6000 and it["week"] > 9:
            material.append((it["store_id"], it["account_id"], it["week"]))
    recall = sum(m in found_set for m in material) / len(material)
    precision = sum(tuple(r) in {(i["store_id"], i["account_id"], i["week"]) for i in injected}
                    for r in found.to_numpy().tolist()) / len(found)
    assert len(material) >= 10
    assert recall >= 0.8, recall
    assert precision >= 0.8, precision
