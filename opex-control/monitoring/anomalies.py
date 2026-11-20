import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

HISTORY_MIN = 8
Z_THRESHOLD = 3.5
MATERIALITY = 4000.0


def robust_z(history, value):
    med = np.nanmedian(history, axis=1)
    mad = np.nanmedian(np.abs(history - med[:, None]), axis=1)
    q1, q3 = np.nanpercentile(history, [25, 75], axis=1)
    iqr_scale = (q3 - q1) / 2.0
    floor = np.maximum(0.05 * np.abs(med), 1.0)
    scale = np.where(mad > 0, mad, np.where(iqr_scale > 0, iqr_scale, floor))
    method = np.where(mad > 0, "MAD", np.where(iqr_scale > 0, "IQR", "RELATIVE_FLOOR"))
    return 0.6745 * (value - med) / scale, med, method


def detect(fact, cutoff_week, z_threshold=Z_THRESHOLD, materiality=MATERIALITY):
    d = fact[fact["week"] <= cutoff_week]
    piv = d.pivot_table(index=["store_id", "account_id"], columns="week", values="actual", aggfunc="sum")
    plan = d.pivot_table(index=["store_id", "account_id"], columns="week", values="plan", aggfunc="sum").reindex(piv.index)
    prev = d.pivot_table(index=["store_id", "account_id"], columns="week", values="actual_prev", aggfunc="sum").reindex(piv.index)
    x = piv.to_numpy(float)
    out = []
    for j in range(HISTORY_MIN, x.shape[1]):
        z, med, method = robust_z(x[:, :j], x[:, j])
        dev = x[:, j] - med
        flag = (np.abs(z) >= z_threshold) & (np.abs(dev) >= materiality)
        idx = np.where(flag)[0]
        for i in idx:
            out.append((piv.index[i][0], piv.index[i][1], int(piv.columns[j]), float(x[i, j]), float(med[i]),
                        float(dev[i]), float(z[i]), method[i], float(plan.iat[i, j]), float(prev.iat[i, j])))
    cols = ["store_id", "account_id", "week", "actual", "median_prior", "deviation", "robust_z", "method", "plan", "actual_prev"]
    res = pd.DataFrame(out, columns=cols)
    res["direction"] = np.where(res["deviation"] > 0, "spike", "drop")
    return res


def features(fact, cutoff_week):
    d = fact[fact["week"] <= cutoff_week].copy()
    d["ratio_plan"] = d["actual"] / d["plan"].replace(0, np.nan)
    d["ratio_prev"] = d["actual"] / d["actual_prev"].replace(0, np.nan)
    d["log_amount"] = np.log1p(d["actual"].clip(lower=0))
    g = d.groupby(["store_id", "account_id"])["actual"]
    d["roll_med"] = g.transform(lambda s: s.shift(1).rolling(8, min_periods=4).median())
    d["ratio_roll"] = d["actual"] / d["roll_med"].replace(0, np.nan)
    cols = ["ratio_plan", "ratio_prev", "log_amount", "ratio_roll"]
    return d.dropna(subset=cols), cols


def isolation_scores(fact, cutoff_week, train_until=None, seed=7):
    d, cols = features(fact, cutoff_week)
    train_until = train_until or max(cutoff_week - 12, 20)
    model = IsolationForest(n_estimators=200, contamination="auto", random_state=seed, n_jobs=-1)
    model.fit(d.loc[d["week"] <= train_until, cols].clip(-10, 10))
    d["iforest_score"] = -model.score_samples(d[cols].clip(-10, 10))
    return d[["store_id", "account_id", "week", "iforest_score"]], model


def psi(expected, actual, bins=10):
    edges = np.unique(np.nanpercentile(expected, np.linspace(0, 100, bins + 1)))
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(np.clip(actual, edges[0], edges[-1]), edges)[0] / len(actual)
    e, a = np.clip(e, 1e-6, None), np.clip(a, 1e-6, None)
    return float(np.sum((a - e) * np.log(a / e)))


def combine(fact, cutoff_week):
    stat = detect(fact, cutoff_week)
    scores, _ = isolation_scores(fact, cutoff_week)
    out = stat.merge(scores, on=["store_id", "account_id", "week"], how="left")
    thr = scores["iforest_score"].quantile(0.97)
    out["model_agrees"] = out["iforest_score"] >= thr
    return out
