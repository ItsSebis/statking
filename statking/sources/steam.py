"""Steam player stats from keyless public endpoints.
- global logged-in users: store.steampowered.com/stats/userdata.json (~5 min resolution, ~2 days back)
- top-100 games by concurrent players: ISteamChartsService
- per-app concurrent players: ISteamUserStats (for tracked apps)
Steam exposes no keyless total for 'in game'; we sum the top-100 as a lower bound."""
import time
from datetime import datetime, timezone

import requests

from ..db import now

NAME = "steam"  # Source interface: see statking/sources/__init__.py
SCHEMA = """
CREATE TABLE IF NOT EXISTS steam_global (ts INTEGER PRIMARY KEY, online INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS steam_top (ts TEXT NOT NULL, rank INTEGER, appid INTEGER, concurrent INTEGER, peak INTEGER);
CREATE TABLE IF NOT EXISTS steam_apps (appid INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS steam_tracked (ts TEXT NOT NULL, appid INTEGER NOT NULL, players INTEGER);
CREATE INDEX IF NOT EXISTS i_top ON steam_top(ts, appid);
"""
DEFAULT_TRACKED = [730, 570, 440]

API = "https://api.steampowered.com"
UA = "statking/0.1 (personal research)"


def _get(url, **params):
    r = requests.get(url, params=params, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.json()


def global_online():
    """List of (unix_ts, users_logged_in)."""
    data = _get("https://store.steampowered.com/stats/userdata.json")
    return [(int(ts / 1000), int(n)) for ts, n in data[0]["data"]]


def top_games():
    """List of (rank, appid, concurrent, peak_today)."""
    d = _get(f"{API}/ISteamChartsService/GetGamesByConcurrentPlayers/v1/")["response"]
    return [(r["rank"], r["appid"], r["concurrent_in_game"], r["peak_in_game"]) for r in d["ranks"]]


def current_players(appid: int):
    return _get(f"{API}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/", appid=appid)["response"].get("player_count")


def app_name(appid: int):
    try:
        d = _get("https://store.steampowered.com/api/appdetails", appids=appid, filters="basic")
        return d[str(appid)]["data"]["name"]
    except Exception:
        return None


def collect(conn, params):
    """Side tables for the report plus steam.* Series. params['tracked']: extra appids besides the top 100."""
    tracked_ids = [int(a) for a in params.get("tracked", DEFAULT_TRACKED)]
    conn.executescript(SCHEMA)
    ts = now()
    pts = global_online()
    conn.executemany("INSERT OR IGNORE INTO steam_global VALUES (?,?)", pts)
    top = top_games()
    conn.executemany("INSERT INTO steam_top VALUES (?,?,?,?,?)", [(ts, *t) for t in top])
    known = {r[0] for r in conn.execute("SELECT appid FROM steam_apps")}
    for appid in {t[1] for t in top[:25]} | set(tracked_ids):  # name lookups only for top 25 + tracked
        if appid not in known:
            conn.execute("INSERT OR REPLACE INTO steam_apps VALUES (?,?)", (appid, app_name(appid)))
            time.sleep(1.5)  # store API is rate limited
    tracked = {}
    for appid in tracked_ids:
        tracked[appid] = current_players(appid)
        conn.execute("INSERT INTO steam_tracked VALUES (?,?,?)", (ts, appid, tracked[appid]))
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
    print(f"steam: {len(pts)} global points, {len(top)} top games, {len(tracked_ids)} tracked")
    return ([("steam.online", iso(t), n) for t, n in pts]
            + [("steam.in_game_top100", ts, sum(t[2] for t in top))]
            + [("steam.players.%d" % a, ts, n) for a, n in tracked.items() if n is not None])
