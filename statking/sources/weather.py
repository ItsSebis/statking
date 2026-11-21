"""DWD weather via Bright Sky (a free JSON API over DWD open data, no key).
Hourly values are aggregated to daily series."""
import requests

API = "https://api.brightsky.dev/weather"
UA = "statking/0.1 (personal research)"
STATIONS = {"hamburg": "01975", "kiel": "02564"}  # Hamburg-Fuhlsbuettel, Kiel-Holtenau


def fetch_hourly(station: str, start: str, end: str):
    r = requests.get(API, params={"dwd_station_id": station, "date": start, "last_date": end},
                     headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    return r.json()["weather"]


def daily(hours, before):
    """before: ISO date; days on/after it are dropped (today is incomplete and the API appends forecast hours)."""
    """-> {date: {temp_mean, temp_min, temp_max, precip_mm, sunshine_min, wind_gust_max}}"""
    by = {}
    for h in hours:
        by.setdefault(h["timestamp"][:10], []).append(h)
    out = {}
    for day, hs in by.items():
        if day >= before or len(hs) < 20:
            continue
        t = [h["temperature"] for h in hs if h["temperature"] is not None]
        out[day] = {
            "temp_mean": sum(t) / len(t) if t else None,
            "temp_min": min(t) if t else None,
            "temp_max": max(t) if t else None,
            "precip_mm": sum(h["precipitation"] or 0 for h in hs),
            "sunshine_min": sum(h["sunshine"] or 0 for h in hs),
            "wind_gust_max": max((h["wind_gust_speed"] for h in hs if h["wind_gust_speed"] is not None), default=None),
        }
    return out
