import json
import os
import statistics
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import db, geo, steam_job

DB_PATH = os.environ.get("STATKING_DB", "data/statking.db")
STATIC = os.path.join(os.path.dirname(__file__), "static")
POLYGONS = [f["properties"]["name"] for f in json.load(open(os.path.join(STATIC, "kiel_districts.geojson")))["features"]]

app = FastAPI(title="statking")


def _days(a, b):
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/rent")
def rent(category: str = "wg", include_gone: bool = False):
    conn = db.connect(DB_PATH)
    q = "SELECT * FROM listings WHERE category=? AND price_eur>0 AND size_m2>0"
    if not include_gone:
        q += " AND gone_at IS NULL"
    listings, by = [], {}
    for r in conn.execute(q, (category,)):
        poly = geo.polygon_name(r["district"], POLYGONS)
        eur_m2 = r["price_eur"] / r["size_m2"]
        life = _days(r["first_seen"], r["gone_at"] or db.now())
        listings.append({
            "id": r["id"], "title": r["title"], "district": poly or r["district"], "polygon": poly,
            "street": r["street"], "price": r["price_eur"], "size": r["size_m2"], "eur_m2": round(eur_m2, 2),
            "flat_desc": r["flat_desc"], "available_from": r["available_from"], "url": r["url"],
            "lat": r["lat"], "lon": r["lon"], "gone": r["gone_at"] is not None, "days_listed": round(life, 1),
        })
        if poly:
            by.setdefault(poly, []).append(listings[-1])
    districts = {
        name: {"n": len(ls), "eur_m2": round(statistics.median(l["eur_m2"] for l in ls), 2),
               "price": round(statistics.median(l["price"] for l in ls))}
        for name, ls in by.items()
    }
    gone = [_days(r["first_seen"], r["gone_at"]) for r in conn.execute(
        "SELECT first_seen, gone_at FROM listings WHERE category=? AND gone_at IS NOT NULL", (category,))]
    last_run = conn.execute("SELECT MAX(ts) FROM runs WHERE category=?", (category,)).fetchone()[0]
    return {"listings": listings, "districts": districts, "last_run": last_run,
            "median_lifetime_days": round(statistics.median(gone), 1) if gone else None, "vanished": len(gone)}


@app.get("/api/steam")
def steam():
    conn = db.connect(DB_PATH)
    conn.executescript(steam_job.SCHEMA)
    online = [{"t": r[0], "v": r[1]} for r in conn.execute("SELECT ts, online FROM steam_global ORDER BY ts")]
    last = conn.execute("SELECT MAX(ts) FROM steam_top").fetchone()[0]
    top = []
    if last:
        top = [dict(r) for r in conn.execute(
            """SELECT t.rank, COALESCE(a.name, CAST(t.appid AS TEXT)) AS name, t.appid, t.concurrent, t.peak
               FROM steam_top t LEFT JOIN steam_apps a ON a.appid=t.appid WHERE t.ts=? ORDER BY t.rank LIMIT 20""", (last,))]
        in_game = conn.execute("SELECT SUM(concurrent) FROM steam_top WHERE ts=?", (last,)).fetchone()[0]
    else:
        in_game = None
    return {"online": online, "top": top, "in_game_lower_bound": in_game, "snapshot": last}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
