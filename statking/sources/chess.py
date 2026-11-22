"""Chess.com public API: game history for one user."""
from datetime import datetime, timezone

import time

import requests

NAME = "chess"  # Source interface: see statking/sources/__init__.py

UA = "statking/0.1 (personal research)"


def _get(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.json()


def archives(user):
    return _get(f"https://api.chess.com/pub/player/{user}/games/archives")["archives"]


def parse_games(user, month_json):
    out = []
    me = user.lower()
    for g in month_json.get("games", []):
        w, b = g["white"], g["black"]
        if w["username"].lower() == me:
            mine, opp, color = w, b, "white"
        elif b["username"].lower() == me:
            mine, opp, color = b, w, "black"
        else:
            continue
        out.append((g["url"], datetime.fromtimestamp(g["end_time"], timezone.utc).isoformat(timespec="seconds"),
                    g.get("time_class"), color, mine["result"], mine.get("rating"), opp.get("rating")))
    return out


def month(url):
    return _get(url)


def collect(conn, params):
    """Games into chess_games (side table) and chess.rating.<time_class> Series; re-reads months from the last stored game."""
    user = params.get("user")
    if not user:
        print("chess: no user in the sources config, skipping")
        return []
    last = conn.execute("SELECT MAX(end_time) FROM chess_games").fetchone()[0]
    since = last[:7].replace("-", "/") if last else "0000/00"
    out, n = [], 0
    for url in archives(user):
        if url[-7:] < since:  # url ends with YYYY/MM
            continue
        rows = parse_games(user, month(url))
        conn.executemany("INSERT OR IGNORE INTO chess_games VALUES (?,?,?,?,?,?,?)", rows)
        out += [(f"chess.rating.{r[2]}", r[1], r[5]) for r in rows if r[5]]
        n += len(rows)
        time.sleep(1)
    print(f"chess[{user}]: {n} games read")
    return out
