"""Collect + report Steam stats."""
from .db import put_metrics
from .sources import steam
from .sources.steam import SCHEMA


def collect(conn):
    """CLI entry (statking steam-scrape): one run with default params; the scheduler uses the Source."""
    put_metrics(conn, steam.collect(conn, {}))
    conn.commit()


def report(conn):
    conn.executescript(SCHEMA)
    last = conn.execute("SELECT MAX(ts) FROM steam_top").fetchone()[0]
    if not last:
        print("no steam data yet - run: statking steam-scrape")
        return
    g = conn.execute("SELECT ts, online FROM steam_global ORDER BY ts").fetchall()
    peak = max(g, key=lambda r: r[1])
    cur = g[-1]
    top = conn.execute(
        """SELECT t.rank, COALESCE(a.name, t.appid), t.concurrent, t.peak FROM steam_top t
           LEFT JOIN steam_apps a ON a.appid=t.appid WHERE t.ts=? ORDER BY t.rank LIMIT 15""", (last,)).fetchall()
    in_game = conn.execute("SELECT SUM(concurrent) FROM steam_top WHERE ts=?", (last,)).fetchone()[0]
    print(f"\n=== Steam ({last}) ===")
    print(f"logged in now: {cur[1]:,}   peak in stored window: {peak[1]:,}")
    print(f"in game (top-100 sum, lower bound): {in_game:,}  ({in_game / cur[1]:.0%} of logged in)")
    print(f"\n{'#':>3} {'game':38} {'now':>10} {'peak today':>11}")
    for r in top:
        print(f"{r[0]:>3} {str(r[1])[:38]:38} {r[2]:>10,} {r[3]:>11,}")
