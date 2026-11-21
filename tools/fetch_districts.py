"""One-off: download district boundaries of a registry city from OSM (Overpass) -> GeoJSON.
Usage: python tools/fetch_districts.py <city> [admin_level]   (writes statking/web/static/districts/<city>.geojson)"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from statking import cities  # noqa: E402


def query(osm_name, level, area_level=None):
    al = f'"admin_level"="{area_level}"' if area_level else '"admin_level"~"^(4|6)$"'
    return (f'[out:json][timeout:180];area["name"="{osm_name}"]["boundary"="administrative"][{al}]->.k;'
            f'rel(area.k)["boundary"="administrative"]["admin_level"="{level}"];out geom;')


def rings(ways):
    """Join way segments (lists of (lon,lat)) into closed rings."""
    segs = [list(w) for w in ways if len(w) > 1]
    out = []
    while segs:
        ring = segs.pop(0)
        grew = True
        while grew and ring[0] != ring[-1]:
            grew = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[::-1][1:]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[::-1][:-1] + ring
                else:
                    continue
                segs.pop(i)
                grew = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            out.append(ring)
    return out


def main(city, level):
    Q = query(cities.CITIES[city]["osm"], level, cities.CITIES[city].get("area_level"))
    path = os.path.join(cities.DISTRICT_DIR, f"{city}.geojson")
    # curl instead of requests: macOS system Python's LibreSSL fails the TLS handshake with overpass-api.de
    for attempt in range(6):  # the public Overpass server is often overloaded
        res = subprocess.run(
            ["curl", "-s", "-m", "200", "-A", "statking/0.1 (personal research)",
             "--data-urlencode", "data=" + Q, "https://overpass-api.de/api/interpreter"],
            capture_output=True)
        if res.stdout[:1] == b"{":
            raw = res.stdout
            break
        print(f"attempt {attempt + 1} failed, retrying", file=sys.stderr)
        time.sleep(10)
    else:
        sys.exit("overpass unavailable")
    feats = []
    for el in json.loads(raw)["elements"]:
        outer = [[(p["lon"], p["lat"]) for p in m["geometry"]]
                 for m in el["members"] if m["type"] == "way" and m["role"] == "outer"]
        polys = [[list(map(list, ring))] for ring in rings(outer)]
        if not polys:
            continue
        feats.append({"type": "Feature", "properties": {"name": el["tags"]["name"]},
                      "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    json.dump({"type": "FeatureCollection", "features": feats}, open(path, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"\n{city}: {len(feats)} districts -> {path}")


main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else str(cities.CITIES[sys.argv[1]].get("admin_level", 10)))
