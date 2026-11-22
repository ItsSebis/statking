"""The Search: test every Hypothesis (Series pair) and judge it on Adjusted Series.

Single entry point: run_search(conn, params) -> Snapshot. Every pair is tested at each Lag in
params['lags'] (or -max_lag..max_lag); each (pair, Lag) is one Hypothesis. Series are adjusted
for trend and seasonality (detrend/deseason where history allows, first differencing otherwise),
Spearman with a block-bootstrap p-value (Pearson stored as a secondary number),
Benjamini-Hochberg FDR correction over the whole Search.
A Real candidate must also be Stable (same sign and significance on both halves and rolling windows).
Pairs inside one Family (derived from the Series name prefix) are Derived Pairs: flagged in the
results and left out of the Hypothesis count and the FDR correction."""
import json
import math
import random
from itertools import combinations
from operator import itemgetter

from . import adjust, correlate, db, review

DEFAULT_PARAMS = {
    "alpha": 0.01,         # false-discovery rate the Search controls (Benjamini-Hochberg)
    "min_buckets": 30,     # fewer aligned buckets than this -> Too Little Data
    "stability_alpha": 0.05,  # p a half / rolling window must reach to count as "still significant"
    "lags": [0],           # Lags (in Resolution buckets, b read `lag` after a) tested for every pair
    "seed": 0,             # fixes the bootstrap resamples: same data + seed -> identical p-values
    "bootstrap_samples": 300,
    "strong_raw": 0.5,     # |Raw Correlation| from which a non-Real pair is blamed on trend/seasonality
}


# Series-name prefix -> number of leading dotted segments that identify the Family.
# Only prefixes whose Series are mechanically related are listed (weather.* / wiki.* / chess.* /
# steam.* Series under one prefix are independent measures, not derivations of each other). Slice 2 makes Families explicit per Source.
FAMILY_SEGMENTS = {"rent": 3}


def family(name):
    """The Family a Series belongs to (e.g. rent.kiel.wg), or None if it is not in one."""
    parts = name.split(".")
    n = FAMILY_SEGMENTS.get(parts[0])
    return ".".join(parts[:n]) if n and len(parts) > n else None


def _is_derived(a, b):
    fa = family(a)
    return fa is not None and fa == family(b)


def _derived(cache, a, b, lag):
    res = max(cache["res"][a], cache["res"][b], key=correlate.ORDER.index)
    return {"series_a": a, "series_b": b, "lag": lag, "res": res, "n": 0, "pearson_raw": None,
            "pearson": None, "adjustment": None, "spearman": None, "p_value": None,
            "q_value": None, "verdict": "derived", "reason": "same_family"}


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


def _resamplers(n, params):
    """Paired circular block-bootstrap index getters for series of length n, fixed by (seed, n).
    The block length ~sqrt(n) keeps short-range autocorrelation inside each block."""
    rng = random.Random(f"{params['seed']}:{n}")
    block = max(1, math.ceil(math.sqrt(n)))

    def one():
        idx = []
        while len(idx) < n:
            start = rng.randrange(n)
            idx.extend((start + k) % n for k in range(block))
        return itemgetter(*idx[:n])

    return [(one(), one()) for _ in range(params["bootstrap_samples"])]


def _p_value(xs, ys, rho, resamplers):
    """Two-sided p for rho against the null "the Series are independent but each keeps its own
    autocorrelation". Resampling x and y *independently* in blocks destroys any cross-relationship
    and preserves autocorrelation; the spread of rho over those resamples is the null standard error.
    A normal tail on that standard error (rather than a resample count) lets p go below 1/B, which
    the FDR correction over many Hypotheses needs."""
    rx, ry = _ranks(xs), _ranks(ys)
    null = [r for gx, gy in resamplers if (r := correlate.pearson(gx(rx), gy(ry))) is not None]
    if not null:
        return None
    se = math.sqrt(sum(r * r for r in null) / len(null))  # null is centred on 0
    if se == 0:
        return None
    return math.erfc(abs(rho) / (se * math.sqrt(2)))


def _coincidence_reason(raw, ta, tb, res, lag, params):
    """Why a non-Real pair looked related in Raw form: seasonality (still strongly correlated once the
    trend is removed), else trend; "not_significant" when the Raw Correlation was never strong."""
    if raw is None or abs(raw) < params["strong_raw"]:
        return "not_significant"
    if ta is not None:
        xs, ys, _ = correlate.align_buckets(ta, tb, res, lag)
        r = correlate.pearson(xs, ys)
        if r is not None and abs(r) >= params["strong_raw"]:
            return "seasonality_driven"
    return "trend_driven"


def _judge(conn_cache, a, b, lag, params):
    res = max(conn_cache["res"][a], conn_cache["res"][b], key=correlate.ORDER.index)
    da, db_ = conn_cache["buckets"](a, res), conn_cache["buckets"](b, res)
    xs, ys, _ = correlate.align_buckets(da, db_, res, lag)
    raw = correlate.pearson(xs, ys)
    out = {"series_a": a, "series_b": b, "lag": lag, "res": res, "n": len(xs),
           "pearson_raw": raw, "pearson": None, "adjustment": None, "spearman": None, "p_value": None}
    dx, dy, method, ta, tb = adjust.adjust_pair(da, db_, res)
    out["adjustment"] = method
    xs, ys, _ = correlate.align_buckets(dx, dy, res, lag)
    out["n"] = len(xs)
    rho = spearman(xs, ys) if len(xs) >= 3 else None
    if len(xs) < params["min_buckets"] or rho is None:
        return {**out, "verdict": "too_little_data", "reason": "too_few_buckets"}
    p = _p_value(xs, ys, rho, conn_cache["resamplers"](len(xs)))
    out = {**out, "spearman": rho, "pearson": correlate.pearson(xs, ys), "p_value": p}
    if p is None:  # degenerate null (constant resamples): untestable, not "not significant"
        return {**out, "verdict": "too_little_data", "reason": "untestable"}
    out["_xy"] = (xs, ys)
    return {**out, "verdict": "coincidence", "reason": _coincidence_reason(raw, ta, tb, res, lag, params)}


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


def _windows(n):
    """The two halves plus rolling windows of half the history, stepped by a quarter of it."""
    w = n // 2
    spans = {(0, w), (n - w, n)}
    spans.update((s, s + w) for s in range(0, n - w + 1, max(1, n // 4)))
    return sorted(spans)


def _is_stable(xs, ys, rho, params, resamplers):
    """True when every half and rolling window has the full-history sign and p <= stability_alpha."""
    for lo, hi in _windows(len(xs)):
        wx, wy = xs[lo:hi], ys[lo:hi]
        r = spearman(wx, wy) if len(wx) >= 3 else None
        if r is None or r * rho <= 0:
            return False
        p = _p_value(wx, wy, r, resamplers(len(wx)))
        if p is None or p > params["stability_alpha"]:
            return False
    return True


def _apply_stability(results, params, resamplers):
    """Demote FDR-Real candidates whose relationship is not present throughout the history."""
    for r in results:
        if r["verdict"] == "real" and not _is_stable(*r["_xy"], r["spearman"], params, resamplers):
            r["verdict"], r["reason"] = "coincidence", "unstable"


def run_search(conn, params=None):
    """Test all Series pairs and store the result as a Snapshot (returned)."""
    given = params or {}
    params = {**DEFAULT_PARAMS, **given}
    if "max_lag" in given:  # shorthand for the symmetric range -max_lag..max_lag
        if "lags" in given:
            raise ValueError("pass either 'lags' or 'max_lag', not both")
        if not isinstance(given["max_lag"], int) or given["max_lag"] < 0:
            raise ValueError("max_lag must be a non-negative integer")
        params["lags"] = list(range(-given["max_lag"], given["max_lag"] + 1))
    params.pop("max_lag", None)
    if not all(isinstance(g, int) for g in params["lags"]):
        raise ValueError("lags must be integers")
    params["lags"] = sorted(set(params["lags"]))  # a repeated Lag would double-count a Hypothesis
    names = [r[0] for r in correlate.list_series(conn)]
    cache = {"res": {s: correlate.granularity(conn, s) for s in names}, "b": {}}

    def buckets(s, res):
        if (s, res) not in cache["b"]:
            cache["b"][(s, res)] = correlate.bucketed(conn, s, res)
        return cache["b"][(s, res)]

    cache["buckets"] = buckets
    resampler_cache = {}

    def resamplers(n):
        if n not in resampler_cache:
            resampler_cache[n] = _resamplers(n, params)
        return resampler_cache[n]

    cache["resamplers"] = resamplers
    results = [_derived(cache, a, b, lag) if _is_derived(a, b) else _judge(cache, a, b, lag, params)
               for a, b in combinations(names, 2) for lag in params["lags"]]
    hypotheses = [r for r in results if r["verdict"] != "derived"]
    _apply_fdr(hypotheses, params["alpha"])
    _apply_stability(hypotheses, params, resamplers)
    ts = db.now()
    cur = conn.execute("INSERT INTO snapshots (ts, params, hypotheses, real_count) VALUES (?,?,?,?)",
                       (ts, json.dumps(params), len(hypotheses), sum(r["verdict"] == "real" for r in hypotheses)))
    sid = cur.lastrowid
    conn.executemany(
        """INSERT INTO snapshot_results (snapshot_id, series_a, series_b, lag, res, n, spearman,
           pearson_raw, pearson, p_value, q_value, verdict, reason, adjustment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(sid, r["series_a"], r["series_b"], r["lag"], r["res"], r["n"], r["spearman"],
          r["pearson_raw"], r["pearson"], r["p_value"], r["q_value"], r["verdict"], r["reason"], r["adjustment"]) for r in results])
    conn.commit()
    return latest_snapshot(conn, sid)


def latest_snapshot(conn, snapshot_id=None, review_state=None, source=None, res=None):
    """The given (or most recent) Snapshot with its per-Hypothesis results, or None.
    Each result carries the pair's current Review State (joined live, not stored in the Snapshot).
    Results can be narrowed by Review State, by Source (the first dotted segment of either Series
    name) and by Resolution."""
    if review_state is not None and review_state not in review.STATES:
        raise ValueError(f"review_state must be one of {', '.join(review.STATES)}")
    if res is not None and res not in correlate.ORDER:
        raise ValueError(f"res must be one of {', '.join(correlate.ORDER)}")
    row = (conn.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone() if snapshot_id
           else conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone())
    if row is None:
        return None
    rows = conn.execute(
        """SELECT s.series_a, s.series_b, s.lag, s.res, s.n, s.spearman, s.pearson_raw, s.pearson, s.p_value,
                  s.q_value, s.verdict, s.reason, s.adjustment,
                  COALESCE(v.state, 'unreviewed') AS review_state, v.note AS review_note
           FROM snapshot_results s
           LEFT JOIN pair_reviews v ON v.series_a=s.series_a AND v.series_b=s.series_b
           WHERE s.snapshot_id=?
           ORDER BY (verdict='real') DESC, ABS(spearman) DESC""", (row["id"],)).fetchall()
    results = [dict(r) for r in rows
               if review_state in (None, r["review_state"]) and res in (None, r["res"])
               and (source is None or source in (r["series_a"].split(".")[0], r["series_b"].split(".")[0]))]
    return {"id": row["id"], "ts": row["ts"], "params": json.loads(row["params"]),
            "hypotheses": row["hypotheses"], "real_count": row["real_count"], "results": results}
