"""District-name normalisation + geocoding (Nominatim, cached, 1 req/s)."""
import re
import time

import requests

from .db import now

UA = "statking/0.1 (personal research)"
# WG-Gesucht uses finer/different names than the OSM Stadtteil polygons
ALIASES = {
    "gaarden": "Gaarden-Süd/Kronsburg",
    "gaarden-süd": "Gaarden-Süd/Kronsburg",
    "kronsburg": "Gaarden-Süd/Kronsburg",
    "dietrichsdorf": "Neumühlen-Dietrichsdorf",
    "neumühlen": "Neumühlen-Dietrichsdorf",
    "neumühlen/dietrichsdorf": "Neumühlen-Dietrichsdorf",
}


def polygon_name(district, known):
    """Map a WG-Gesucht district string to an OSM polygon name (or None)."""
    if not district:
        return None
    d = district.strip().lower()
    if d in ALIASES:
        return ALIASES[d]
    if d.startswith("elmschenhagen"):
        return "Elmschenhagen"
    for k in known:
        if k.lower() == d:
            return k
    return None


def _query_for(street):
    street = re.sub(r"\s+", " ", street.strip())
    return f"{street}, Kiel"


def geocode_missing(conn, limit=500):
    rows = conn.execute(
        "SELECT source, id, street FROM listings WHERE lat IS NULL AND street IS NOT NULL AND street!='' LIMIT ?",
        (limit,)).fetchall()
    done = 0
    for r in rows:
        q = _query_for(r["street"])
        hit = conn.execute("SELECT lat, lon FROM geocache WHERE query=?", (q,)).fetchone()
        if hit is None:
            try:
                resp = requests.get("https://nominatim.openstreetmap.org/search",
                                    params={"q": q, "format": "json", "limit": 1, "countrycodes": "de"},
                                    headers={"User-Agent": UA}, timeout=30)
                resp.raise_for_status()
                js = resp.json()
            except requests.RequestException as e:
                print(f"geocode failed ({e}); stopping")
                break
            lat, lon = (float(js[0]["lat"]), float(js[0]["lon"])) if js else (None, None)
            conn.execute("INSERT OR REPLACE INTO geocache VALUES (?,?,?,?)", (q, lat, lon, now()))
            conn.commit()
            time.sleep(1.1)  # Nominatim policy: max 1 request/second
        else:
            lat, lon = hit["lat"], hit["lon"]
        if lat is not None:
            # sanity: must lie roughly inside Kiel
            if 54.2 < lat < 54.45 and 10.0 < lon < 10.3:
                conn.execute("UPDATE listings SET lat=?, lon=? WHERE source=? AND id=?", (lat, lon, r["source"], r["id"]))
                done += 1
        conn.commit()
    print(f"geocoded {done}/{len(rows)}")
