"""Example Source: copy to statking/sources/<name>.py, rename, and add an entry to sources.json.
Documents the interface (see statking/sources/__init__.py); validated by the tests."""
from datetime import date, timedelta

NAME = "example"   # required; first segment of every Series written
FAMILY = 2         # optional; example.<x>.* Series are mechanically related (Derived Pairs)


def collect(conn, params):
    """Return (series, ts, value) rows. ts is YYYY-MM-DD for daily data.
    Idempotent: re-returning stored rows is fine. params come from sources.json."""
    last = conn.execute("SELECT MAX(ts) FROM metrics WHERE series=?", ("example.a.count",)).fetchone()[0]
    start = date.fromisoformat(last) if last else date.fromisoformat(params.get("backfill_from", "2024-01-01"))
    return [("example.a.count", (start + timedelta(days=i)).isoformat(), float(i)) for i in range(3)]
