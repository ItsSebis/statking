"""City registry. A city needs: WG-Gesucht id (verified!), centre (for geocoding bounds + weather station
lookup) and an OSM area name for district polygons.
Add a city by appending an entry; verify the WG-Gesucht id by opening
https://www.wg-gesucht.de/wg-zimmer-in-<Name>.<id>.0.1.0.html and checking the page title."""
import json
import os

CITIES = {
    "kiel":      {"name": "Kiel",              "wg_id": 71, "lat": 54.3233, "lon": 10.1394, "osm": "Kiel",              "categories": ("wg", "1zi", "wohnung")},
    "hamburg":   {"name": "Hamburg",           "wg_id": 55, "lat": 53.5511, "lon": 9.9937,  "osm": "Hamburg",           "categories": ("wg",)},
    "berlin":    {"name": "Berlin",            "wg_id": 8,  "lat": 52.5200, "lon": 13.4050, "osm": "Berlin",            "categories": ("wg",)},
    "muenchen":  {"name": "München",           "wg_id": 90, "lat": 48.1374, "lon": 11.5755, "osm": "München", "admin_level": 9,          "categories": ("wg",)},
    "koeln":     {"name": "Köln",              "wg_id": 73, "lat": 50.9375, "lon": 6.9603,  "osm": "Köln",              "categories": ("wg",)},
    "frankfurt": {"name": "Frankfurt am Main", "wg_id": 41, "lat": 50.1109, "lon": 8.6821,  "osm": "Frankfurt am Main", "categories": ("wg",)},
    "leipzig":   {"name": "Leipzig",           "wg_id": 77, "lat": 51.3397, "lon": 12.3731, "osm": "Leipzig",           "categories": ("wg",)},
    "hannover":  {"name": "Hannover",          "wg_id": 57, "lat": 52.3759, "lon": 9.7320,  "osm": "Hannover", "area_level": 8,          "categories": ("wg",)},
}
# stations used before auto-selection existed: keep series continuity
WEATHER_STATION_OVERRIDE = {"hamburg": "01975", "kiel": "02564"}

DISTRICT_DIR = os.path.join(os.path.dirname(__file__), "web", "static", "districts")


def enabled():
    """RENT_CITIES=kiel,hamburg,... (default: all in the registry)."""
    raw = os.environ.get("RENT_CITIES", "")
    names = [c.strip() for c in raw.split(",") if c.strip()] or list(CITIES)
    return [c for c in names if c in CITIES]


def district_names(city):
    path = os.path.join(DISTRICT_DIR, f"{city}.geojson")
    if not os.path.exists(path):
        return []
    return [f["properties"]["name"] for f in json.load(open(path))["features"]]


def bounds(city, pad=0.3):
    c = CITIES[city]
    return (c["lat"] - pad, c["lat"] + pad, c["lon"] - pad * 1.6, c["lon"] + pad * 1.6)
