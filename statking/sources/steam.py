"""Steam player stats from keyless public endpoints.
- global logged-in users: store.steampowered.com/stats/userdata.json (~5 min resolution, ~2 days back)
- top-100 games by concurrent players: ISteamChartsService
- per-app concurrent players: ISteamUserStats (for tracked apps)
Steam exposes no keyless total for 'in game'; we sum the top-100 as a lower bound."""
import requests

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
