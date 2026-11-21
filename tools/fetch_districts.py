"""One-off: download Kiel Stadtteil boundaries from OSM (Overpass) -> GeoJSON.
Usage: python tools/fetch_districts.py statking/web/static/kiel_districts.geojson"""
import json
import subprocess
import sys
import time

Q = ('[out:json][timeout:90];area["name"="Kiel"]["boundary"="administrative"]["admin_level"="6"]->.k;'
     'rel(area.k)["boundary"="administrative"]["admin_level"="10"];out geom;')


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


def main(path):
    # curl instead of requests: macOS system Python's LibreSSL fails the TLS handshake with overpass-api.de
    for attempt in range(6):  # the public Overpass server is often overloaded
        res = subprocess.run(
            ["curl", "-s", "-m", "120", "-A", "statking/0.1 (personal research)",
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
        feats.append({"type": "Feature", "properties": {"name": el["tags"]["name"]},
                      "geometry": {"type": "MultiPolygon", "coordinates": polys}})
        print(el["tags"]["name"], len(polys))
    json.dump({"type": "FeatureCollection", "features": feats}, open(path, "w"), ensure_ascii=False)


main(sys.argv[1])
