"""Align two metric series and compute Pearson correlation (optionally lagged).
Series of different granularity (daily weather vs yearly Kiel statistics) are
compared at the coarser resolution: both are averaged per day / month / year."""
import math
from datetime import date, datetime

LEN = {"day": 10, "month": 7, "year": 4}
ORDER = ["day", "month", "year"]


def _points(conn, series):
    return conn.execute("SELECT substr(ts,1,10), value FROM metrics WHERE series=? AND value IS NOT NULL", (series,)).fetchall()


def granularity(conn, series):
    """Natural resolution from the median gap between distinct dates."""
    days = sorted({date.fromisoformat(d) for d, _ in _points(conn, series)})
    if len(days) < 2:
        return "day"
    gaps = sorted((b - a).days for a, b in zip(days, days[1:]))
    med = gaps[len(gaps) // 2]
    return "year" if med >= 300 else "month" if med >= 25 else "day"


def common_resolution(conn, a, b):
    return max(granularity(conn, a), granularity(conn, b), key=ORDER.index)


def bucketed(conn, series, res="day"):
    """{bucket: mean value}; bucket is YYYY-MM-DD / YYYY-MM / YYYY."""
    acc = {}
    for d, v in _points(conn, series):
        acc.setdefault(d[:LEN[res]], []).append(v)
    return {k: sum(v) / len(v) for k, v in acc.items()}


def daily(conn, series):
    return bucketed(conn, series, "day")


def shift(bucket, res, n):
    if res == "year":
        return str(int(bucket) + n)
    if res == "month":
        y, m = int(bucket[:4]), int(bucket[5:7])
        t = y * 12 + (m - 1) + n
        return f"{t // 12:04d}-{t % 12 + 1:02d}"
    return (date.fromisoformat(bucket) + __import__("datetime").timedelta(days=n)).isoformat()


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


def differenced(d, res):
    """Change vs. the previous bucket (only for consecutive buckets) - removes shared trends."""
    return {k: v - d[shift(k, res, -1)] for k, v in d.items() if shift(k, res, -1) in d}


def aligned(conn, a, b, lag=0, start=None, end=None, res=None, diff=False):
    """-> (xs, ys, buckets, res). b is read `lag` buckets after a."""
    res = res or common_resolution(conn, a, b)
    da, db_ = bucketed(conn, a, res), bucketed(conn, b, res)
    if diff:
        da, db_ = differenced(da, res), differenced(db_, res)
    xs, ys, ks = [], [], []
    for k, v in sorted(da.items()):
        if (start and k < start[:LEN[res]]) or (end and k > end[:LEN[res]]):
            continue
        k2 = shift(k, res, lag)
        if k2 in db_:
            xs.append(v); ys.append(db_[k2]); ks.append(k)
    return xs, ys, ks, res


def correlate(conn, a, b, lag=0):
    xs, ys, _, res = aligned(conn, a, b, lag)
    return pearson(xs, ys), len(xs), res


def list_series(conn):
    return conn.execute(
        "SELECT series, COUNT(*), MIN(substr(ts,1,10)), MAX(substr(ts,1,10)) FROM metrics GROUP BY series ORDER BY series").fetchall()
