"""Chess.com public API: game history for one user."""
from datetime import datetime, timezone

import requests

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
