import json
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import cities, correlate, db, geo, review, search, steam_job

DB_PATH = os.environ.get("STATKING_DB", "data/statking.db")
STATIC = os.path.join(os.path.dirname(__file__), "static")

app = FastAPI(title="statking")


def _days(a, b):
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/rent")
def rent(category: str = "wg", include_gone: bool = False, city: str = "kiel"):
    conn = db.connect(DB_PATH)
    polygons = cities.district_names(city)
    q = "SELECT * FROM listings WHERE category=? AND city=? AND price_eur>0 AND size_m2>0"
    if not include_gone:
        q += " AND gone_at IS NULL"
    listings, by = [], {}
    for r in conn.execute(q, (category, city)):
        poly = geo.polygon_name(r["district"], polygons, city)
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
        "SELECT first_seen, gone_at FROM listings WHERE category=? AND city=? AND gone_at IS NOT NULL", (category, city))]
    last_run = conn.execute("SELECT MAX(ts) FROM runs WHERE category=? AND city=?", (category, city)).fetchone()[0]
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


GROUPS = {"rent": "Mieten", "weather": "Wetter (DWD)", "wiki": "Wikipedia", "steam": "Steam",
          "chess": "Chess.com", "stat": "Statistik Nord/SH"}


@app.get("/api/cities")
def cities_list():
    conn = db.connect(DB_PATH)
    counts = {r[0]: r[1] for r in conn.execute("SELECT city, COUNT(*) FROM listings WHERE gone_at IS NULL GROUP BY city")}
    return [{"id": k, "name": c["name"], "lat": c["lat"], "lon": c["lon"], "categories": list(c["categories"]),
             "active": counts.get(k, 0), "has_districts": bool(cities.district_names(k)), "has_layers": k == "kiel"}
            for k, c in cities.CITIES.items()]


@app.get("/api/series")
def series_list():
    conn = db.connect(DB_PATH)
    out = []
    for name, n, lo, hi in correlate.list_series(conn):
        out.append({"name": name, "group": GROUPS.get(name.split(".")[0], "Sonstige"), "n": n, "from": lo, "to": hi,
                    "res": correlate.granularity(conn, name) if n >= 3 else "day"})
    return out


@app.get("/api/series/data")
def series_data(name: List[str] = Query(...), start: Optional[str] = None, end: Optional[str] = None):
    """Daily means for each requested series."""
    conn = db.connect(DB_PATH)
    res = {}
    for n in name[:4]:
        res[n] = [{"d": d, "v": round(v, 4)} for d, v in sorted(correlate.daily(conn, n).items())
                  if (not start or d >= start) and (not end or d <= end)]
    return res


@app.get("/api/corr")
def corr(a: str, b: str, start: Optional[str] = None, end: Optional[str] = None, max_lag: Optional[int] = None, diff: bool = False):
    """Pearson r at lag 0 plus a lag scan (positive lag = b follows a) and aligned pairs for a scatter plot.
    Resolution (day/month/year) is the coarser of the two series; lag is in that unit."""
    conn = db.connect(DB_PATH)
    res = correlate.common_resolution(conn, a, b)
    if max_lag is None:
        max_lag = {"day": 14, "month": 12, "year": 5}[res]
    scan = []
    for lag in range(-max_lag, max_lag + 1):
        xs, ys, _, _ = correlate.aligned(conn, a, b, lag, start, end, res, diff)
        r = correlate.pearson(xs, ys)
        scan.append({"lag": lag, "r": None if r is None else round(r, 4), "n": len(xs)})
    xs, ys, ks, _ = correlate.aligned(conn, a, b, 0, start, end, res, diff)
    r0 = correlate.pearson(xs, ys)
    step = max(1, len(xs) // 600)  # cap scatter points
    return {"r": None if r0 is None else round(r0, 4), "n": len(xs), "unit": res, "scan": scan,
            "points": [{"x": round(x, 3), "y": round(y, 3), "d": k} for x, y, k in list(zip(xs, ys, ks))[::step]]}


@app.get("/api/search/latest")
def search_latest(review_state: Optional[str] = None):
    """Most recent Snapshot (parameters + every tested Hypothesis), or null before the first Search.
    review_state narrows the results to unreviewed / confirmed / dismissed pairs."""
    try:
        return search.latest_snapshot(db.connect(DB_PATH), review_state=review_state)
    except ValueError as e:
        raise HTTPException(400, str(e))


class ReviewIn(BaseModel):
    series_a: str
    series_b: str
    state: str
    note: Optional[str] = None


@app.put("/api/review")
def review_set(body: ReviewIn):
    """Set a pair's Review State (confirmed needs a note; unreviewed clears it)."""
    try:
        review.set_review(db.connect(DB_PATH), body.series_a, body.series_b, body.state, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/search/run")
def search_run():
    """'Run now': a full Search over all Series, stored as a new Snapshot."""
    return search.run_search(db.connect(DB_PATH))


@app.get("/api/layers")
def layers():
    """Per-Stadtteil statistics usable as map layers."""
    from ..sources import opendata_sh
    conn = db.connect(DB_PATH)
    out = []
    for key, (_cid, label, unit) in opendata_sh.DISTRICT_DATASETS.items():
        row = conn.execute("SELECT MAX(substr(ts,1,4)) FROM district_metrics WHERE dataset=?", (key,)).fetchone()
        if row and row[0]:
            out.append({"id": key, "label": label, "unit": unit, "year": row[0]})
    return out


@app.get("/api/layer")
def layer(id: str, year: Optional[str] = None):
    """{polygon name: value} for the latest (or given) year. Only the first merkmal of a dataset is used."""
    conn = db.connect(DB_PATH)
    if not year:
        year = conn.execute("SELECT MAX(substr(ts,1,4)) FROM district_metrics WHERE dataset=?", (id,)).fetchone()[0]
    first = conn.execute("SELECT merkmal FROM district_metrics WHERE dataset=? ORDER BY merkmal LIMIT 1", (id,)).fetchone()
    values = {}
    if first:
        for r in conn.execute(
                "SELECT district, value FROM district_metrics WHERE dataset=? AND merkmal=? AND substr(ts,1,4)=?",
                (id, first[0], year)):
            poly = geo.polygon_name(r["district"], cities.district_names("kiel"), "kiel")
            if poly:
                values[poly] = r["value"]
    return {"id": id, "year": year, "values": values}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
