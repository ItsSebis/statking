"""The Search: test every Hypothesis (Series pair) and judge it on Adjusted Series.

Single entry point: run_search(conn, params) -> Snapshot. Walking-skeleton scope: lag 0 only,
first differencing, Spearman, Benjamini-Hochberg FDR correction over the whole Search.
Later slices add Stability, Derived Pairs, more Lags."""
import json
import math
from itertools import combinations

from . import correlate, db

DEFAULT_PARAMS = {
    "alpha": 0.01,         # false-discovery rate the Search controls (Benjamini-Hochberg)
    "min_buckets": 30,     # fewer aligned buckets than this -> Too Little Data
    "lags": [0],
}


def _ranks(xs):
    order = sorted(range(len(xs)), key=xs.__getitem__)
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs, ys):
    return correlate.pearson(_ranks(xs), _ranks(ys))


def _p_value(rho, n):
    """Two-sided p from the Fisher z approximation (Spearman variance inflation 1.06)."""
    rho = max(-0.999999, min(0.999999, rho))
    z = math.atanh(rho) * math.sqrt((n - 3) / 1.06)
    return math.erfc(abs(z) / math.sqrt(2))


def _judge(conn_cache, a, b, lag, params):
    res = max(conn_cache["res"][a], conn_cache["res"][b], key=correlate.ORDER.index)
    da, db_ = conn_cache["buckets"](a, res), conn_cache["buckets"](b, res)
    xs, ys, _ = correlate.align_buckets(da, db_, res, lag)
    raw = correlate.pearson(xs, ys)
    out = {"series_a": a, "series_b": b, "lag": lag, "res": res, "n": len(xs),
           "pearson_raw": raw, "spearman": None, "p_value": None}
    dx, dy = correlate.differenced(da, res), correlate.differenced(db_, res)
    xs, ys, _ = correlate.align_buckets(dx, dy, res, lag)
    out["n"] = len(xs)
    rho = spearman(xs, ys) if len(xs) >= 3 else None
    if len(xs) < params["min_buckets"] or rho is None:
        return {**out, "verdict": "too_little_data", "reason": "too_few_buckets"}
    return {**out, "spearman": rho, "p_value": _p_value(rho, len(xs)), "verdict": "coincidence",
            "reason": "not_significant"}


def _apply_fdr(results, alpha):
    """Benjamini-Hochberg over every Hypothesis in the Search (m = all of them, including Too Little
    Data ones, which can never be Real: the conservative choice). Sets q_value on tested results and
    promotes those with q <= alpha to Real."""
    m = len(results)
    tested = sorted((r for r in results if r["p_value"] is not None), key=lambda r: r["p_value"])
    q = 1.0
    for rank in range(len(tested), 0, -1):  # running minimum from the largest p down keeps q monotone
        r = tested[rank - 1]
        q = min(q, r["p_value"] * m / rank)
        r["q_value"] = q
    for r in results:
        r.setdefault("q_value", None)
        if r["q_value"] is not None and r["q_value"] <= alpha:
            r["verdict"], r["reason"] = "real", None


def run_search(conn, params=None):
    """Test all Series pairs and store the result as a Snapshot (returned)."""
    params = {**DEFAULT_PARAMS, **(params or {})}
    names = [r[0] for r in correlate.list_series(conn)]
    cache = {"res": {s: correlate.granularity(conn, s) for s in names}, "b": {}}

    def buckets(s, res):
        if (s, res) not in cache["b"]:
            cache["b"][(s, res)] = correlate.bucketed(conn, s, res)
        return cache["b"][(s, res)]

    cache["buckets"] = buckets
    results = [_judge(cache, a, b, lag, params)
               for a, b in combinations(names, 2) for lag in params["lags"]]
    _apply_fdr(results, params["alpha"])
    ts = db.now()
    cur = conn.execute("INSERT INTO snapshots (ts, params, hypotheses, real_count) VALUES (?,?,?,?)",
                       (ts, json.dumps(params), len(results), sum(r["verdict"] == "real" for r in results)))
    sid = cur.lastrowid
    conn.executemany(
        """INSERT INTO snapshot_results (snapshot_id, series_a, series_b, lag, res, n, spearman,
           pearson_raw, p_value, q_value, verdict, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(sid, r["series_a"], r["series_b"], r["lag"], r["res"], r["n"], r["spearman"],
          r["pearson_raw"], r["p_value"], r["q_value"], r["verdict"], r["reason"]) for r in results])
    conn.commit()
    return latest_snapshot(conn, sid)


def latest_snapshot(conn, snapshot_id=None):
    """The given (or most recent) Snapshot with its per-Hypothesis results, or None."""
    row = (conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone() if snapshot_id
           else conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone())
    if row is None:
        return None
    rows = conn.execute(
        """SELECT series_a, series_b, lag, res, n, spearman, pearson_raw, p_value, q_value, verdict, reason
           FROM snapshot_results WHERE snapshot_id=?
           ORDER BY (verdict='real') DESC, ABS(spearman) DESC""", (row["id"],)).fetchall()
    return {"id": row["id"], "ts": row["ts"], "params": json.loads(row["params"]),
            "hypotheses": row["hypotheses"], "real_count": row["real_count"],
            "results": [dict(r) for r in rows]}
