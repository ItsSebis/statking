"""DWD weather via Bright Sky (a free JSON API over DWD open data, no key).
Hourly values are aggregated to daily series."""
from datetime import date, timedelta

import time

import requests

from .. import cities

NAME = "weather"  # Source interface: see statking/sources/__init__.py
BACKFILL_FROM = "2010-01-01"  # Bright Sky / DWD station history
API = "https://api.brightsky.dev/weather"
UA = "statking/0.1 (personal research)"


def nearest_station(lat, lon, max_dist=60000):
    """Nearest DWD station (with history) to a point -> (dwd_station_id, name, distance_m)."""
    r = requests.get("https://api.brightsky.dev/sources", params={"lat": lat, "lon": lon, "max_dist": max_dist},
                     headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    cands = [x for x in r.json()["sources"] if x.get("dwd_station_id") and x["observation_type"] == "historical"
             and x.get("first_record") and x["first_record"] < "2024-01-01"
             and x.get("last_record") and x["last_record"][:10] >= (date.today() - timedelta(days=60)).isoformat()]
    # prefer full synoptic stations (WMO id): many small stations only measure precipitation and have no weather rows
    full = [x for x in cands if x.get("wmo_station_id")]
    best = min(full or cands, key=lambda x: x["distance"])
    return best["dwd_station_id"], best["station_name"], best["distance"]


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


def _today():
    return date.today()


def station_for(conn, city):
    """DWD station for a city: fixed override, cached lookup, or nearest station."""
    if city in cities.WEATHER_STATION_OVERRIDE:
        return cities.WEATHER_STATION_OVERRIDE[city]
    row = conn.execute("SELECT value FROM kv WHERE key=?", (f"weather_station.{city}",)).fetchone()
    if row:
        return row[0]
    c = cities.CITIES[city]
    sid, name, dist = nearest_station(c["lat"], c["lon"])
    print(f"weather[{city}]: using {name} ({sid}), {dist / 1000:.0f} km away")
    conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (f"weather_station.{city}", sid))
    conn.commit()
    return sid


def collect(conn, params):
    """Daily weather Series per city. First run backfills from params['backfill_from']; later runs resume
    one day before the last stored day."""
    backfill = params.get("backfill_from", "2024-01-01")
    pause = params.get("pause_seconds", 1)
    today = _today()
    rows = []
    for name in cities.CITIES:
        station = station_for(conn, name)
        last = conn.execute("SELECT MAX(ts) FROM metrics WHERE series=?", (f"weather.{name}.temp_mean",)).fetchone()[0]
        start = date.fromisoformat(last) - timedelta(days=1) if last else date.fromisoformat(backfill)
        n = 0
        while start <= today:
            end = min(start + timedelta(days=89), today)
            hours = fetch_hourly(station, start.isoformat(), (end + timedelta(days=1)).isoformat())
            for day, v in daily(hours, today.isoformat()).items():
                for k, val in v.items():
                    if val is not None:
                        rows.append((f"weather.{name}.{k}", day, val))
                        n += 1
            start = end + timedelta(days=1)
            time.sleep(pause)
        print(f"weather[{name}]: {n} points")
    return rows
