import json
import os
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import correlate, db, geo, steam_job

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


GROUPS = {"rent": "Kiel Mieten", "weather": "Wetter (DWD)", "wiki": "Wikipedia", "steam": "Steam",
          "chess": "Chess.com", "stat": "Statistik Nord/SH"}


@app.get("/api/series")
def series_list():
    conn = db.connect(DB_PATH)
    out = []
    for name, n, lo, hi in correlate.list_series(conn):
        out.append({"name": name, "group": GROUPS.get(name.split(".")[0], "Sonstige"), "n": n, "from": lo, "to": hi})
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
def corr(a: str, b: str, start: Optional[str] = None, end: Optional[str] = None, max_lag: int = 14):
    """Pearson r at lag 0 plus a lag scan (positive lag = b follows a), plus aligned pairs for a scatter plot."""
    conn = db.connect(DB_PATH)
    da, dbb = correlate.daily(conn, a), correlate.daily(conn, b)
    if start:
        da = {d: v for d, v in da.items() if d >= start}
    if end:
        da = {d: v for d, v in da.items() if d <= end}
    def aligned(lag):
        xs, ys, ds = [], [], []
        for d, v in sorted(da.items()):
            d2 = (date.fromisoformat(d) + timedelta(days=lag)).isoformat()
            if d2 in dbb:
                xs.append(v); ys.append(dbb[d2]); ds.append(d)
        return xs, ys, ds
    scan = []
    for lag in range(-max_lag, max_lag + 1):
        xs, ys, _ = aligned(lag)
        r = correlate.pearson(xs, ys)
        scan.append({"lag": lag, "r": None if r is None else round(r, 4), "n": len(xs)})
    xs, ys, ds = aligned(0)
    r0 = correlate.pearson(xs, ys)
    step = max(1, len(xs) // 600)  # cap scatter points
    return {"r": None if r0 is None else round(r0, 4), "n": len(xs), "scan": scan,
            "points": [{"x": round(x, 3), "y": round(y, 3), "d": d} for x, y, d in list(zip(xs, ys, ds))[::step]]}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
