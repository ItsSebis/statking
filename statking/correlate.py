"""Align two metric series per day and compute Pearson correlation (optionally lagged)."""
import math


def daily(conn, series):
    """{YYYY-MM-DD: mean value} for a series of any granularity."""
    return {r[0]: r[1] for r in conn.execute(
        "SELECT substr(ts,1,10) d, AVG(value) FROM metrics WHERE series=? AND value IS NOT NULL GROUP BY d", (series,))}


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def correlate(conn, a, b, lag=0):
    """r between a(day) and b(day + lag days). Returns (r, n_days)."""
    from datetime import date, timedelta
    da, db_ = daily(conn, a), daily(conn, b)
    xs, ys = [], []
    for d, v in da.items():
        d2 = (date.fromisoformat(d) + timedelta(days=lag)).isoformat()
        if d2 in db_:
            xs.append(v)
            ys.append(db_[d2])
    return pearson(xs, ys), len(xs)


def list_series(conn):
    return conn.execute(
        "SELECT series, COUNT(*), MIN(substr(ts,1,10)), MAX(substr(ts,1,10)) FROM metrics GROUP BY series ORDER BY series").fetchall()
