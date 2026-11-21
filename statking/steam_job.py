"""Collect + report Steam stats."""
import os
import time

from datetime import datetime, timezone

from .db import now, put_metrics
from .sources import steam

SCHEMA = """
CREATE TABLE IF NOT EXISTS steam_global (ts INTEGER PRIMARY KEY, online INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS steam_top (ts TEXT NOT NULL, rank INTEGER, appid INTEGER, concurrent INTEGER, peak INTEGER);
CREATE TABLE IF NOT EXISTS steam_apps (appid INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS steam_tracked (ts TEXT NOT NULL, appid INTEGER NOT NULL, players INTEGER);
CREATE INDEX IF NOT EXISTS i_top ON steam_top(ts, appid);
"""
# extra apps to track on every run besides the top 100 (comma separated appids)
TRACKED = [int(x) for x in os.environ.get("STEAM_TRACK", "730,570,440").split(",") if x.strip()]


def collect(conn):
    conn.executescript(SCHEMA)
    ts = now()
    pts = steam.global_online()
    conn.executemany("INSERT OR IGNORE INTO steam_global VALUES (?,?)", pts)
    top = steam.top_games()
    conn.executemany("INSERT INTO steam_top VALUES (?,?,?,?,?)", [(ts, *t) for t in top])
    known = {r[0] for r in conn.execute("SELECT appid FROM steam_apps")}
    for appid in {t[1] for t in top[:25]} | set(TRACKED):  # name lookups only for top 25 + tracked
        if appid not in known:
            conn.execute("INSERT OR REPLACE INTO steam_apps VALUES (?,?)", (appid, steam.app_name(appid)))
            time.sleep(1.5)  # store API is rate limited
    tracked = {}
    for appid in TRACKED:
        tracked[appid] = steam.current_players(appid)
        conn.execute("INSERT INTO steam_tracked VALUES (?,?,?)", (ts, appid, tracked[appid]))
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
    put_metrics(conn, [("steam.online", iso(t), n) for t, n in pts])
    put_metrics(conn, [("steam.in_game_top100", ts, sum(t[2] for t in top))])
    put_metrics(conn, [("steam.players.%d" % a, ts, n) for a, n in tracked.items() if n is not None])
    conn.commit()
    print(f"steam: {len(pts)} global points, {len(top)} top games, {len(TRACKED)} tracked")


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
