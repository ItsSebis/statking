"""SQLite storage. One row per listing; first_seen/last_seen give listing lifetime."""
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    source TEXT NOT NULL,
    id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT,
    district TEXT,
    street TEXT,
    flat_desc TEXT,
    price_eur INTEGER,
    size_m2 REAL,
    available_from TEXT,
    url TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    gone_at TEXT,
    PRIMARY KEY (source, id)
);
CREATE TABLE IF NOT EXISTS runs (
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    listings_found INTEGER NOT NULL,
    complete INTEGER NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


HISTORY_SCHEMA = """
-- every price a listing has ever shown (one row on first sight + one per change)
CREATE TABLE IF NOT EXISTS price_history (
    source TEXT NOT NULL, id TEXT NOT NULL, ts TEXT NOT NULL, price_eur INTEGER,
    PRIMARY KEY (source, id, ts)
);
-- generic time series every source writes to; ts is an ISO UTC string
-- (daily series use YYYY-MM-DD) so series can be aligned and correlated
CREATE TABLE IF NOT EXISTS metrics (
    series TEXT NOT NULL, ts TEXT NOT NULL, value REAL,
    PRIMARY KEY (series, ts)
);
CREATE TABLE IF NOT EXISTS job_runs (job TEXT PRIMARY KEY, ts TEXT NOT NULL, ok INTEGER NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS district_metrics (
    dataset TEXT NOT NULL, merkmal TEXT NOT NULL, district TEXT NOT NULL, ts TEXT NOT NULL, value REAL,
    PRIMARY KEY (dataset, merkmal, district, ts)
);
CREATE TABLE IF NOT EXISTS chess_games (
    url TEXT PRIMARY KEY, end_time TEXT NOT NULL, time_class TEXT, color TEXT,
    result TEXT, my_rating INTEGER, opp_rating INTEGER
);
"""

GEO_SCHEMA = """
CREATE TABLE IF NOT EXISTS geocache (query TEXT PRIMARY KEY, lat REAL, lon REAL, ts TEXT NOT NULL);
"""


def connect(path: str) -> sqlite3.Connection:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA + GEO_SCHEMA + HISTORY_SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    for c in ("lat", "lon"):  # lightweight migration
        if c not in cols:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {c} REAL")
    conn.execute(
        """INSERT OR IGNORE INTO price_history (source,id,ts,price_eur)
           SELECT source,id,first_seen,price_eur FROM listings WHERE price_eur IS NOT NULL""")
    conn.commit()
    return conn


def save_run(conn, source: str, category: str, items: list, complete: bool) -> tuple:
    """Upsert listings. If the crawl was complete, mark unseen listings as gone.
    Returns (new, gone)."""
    ts = now()
    new = 0
    for it in items:
        cur = conn.execute(
            "SELECT price_eur FROM listings WHERE source=? AND id=?", (source, it["id"])
        ).fetchone()
        if cur is None:
            new += 1
            conn.execute("INSERT OR IGNORE INTO price_history VALUES (?,?,?,?)", (source, it["id"], ts, it["price_eur"]))
            conn.execute(
                """INSERT INTO listings (source,id,category,title,district,street,flat_desc,
                   price_eur,size_m2,available_from,url,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source, it["id"], category, it["title"], it["district"], it["street"],
                 it["flat_desc"], it["price_eur"], it["size_m2"], it["available_from"],
                 it["url"], ts, ts),
            )
        else:
            if cur["price_eur"] != it["price_eur"]:
                conn.execute("INSERT OR IGNORE INTO price_history VALUES (?,?,?,?)", (source, it["id"], ts, it["price_eur"]))
            conn.execute(
                """UPDATE listings SET last_seen=?, gone_at=NULL, price_eur=?, size_m2=?,
                   title=?, district=? WHERE source=? AND id=?""",
                (ts, it["price_eur"], it["size_m2"], it["title"], it["district"], source, it["id"]),
            )
    gone = 0
    if complete and items:
        gone = conn.execute(
            """UPDATE listings SET gone_at=? WHERE source=? AND category=?
               AND gone_at IS NULL AND last_seen<?""",
            (ts, source, category, ts),
        ).rowcount
    conn.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (ts, source, category, len(items), int(complete)))
    if complete:
        record_rent_metrics(conn, source, category, ts, new, gone)
    conn.commit()
    return new, gone


def put_metrics(conn, rows):
    """rows: iterable of (series, ts, value). Idempotent."""
    conn.executemany("INSERT OR REPLACE INTO metrics VALUES (?,?,?)", list(rows))


def record_rent_metrics(conn, source, category, ts, new, gone):
    """Snapshot of the market at this scrape: city + per-district median EUR/m2."""
    from . import geo  # local import: geo imports db
    import json, statistics
    polys = [f["properties"]["name"] for f in json.load(open(os.path.join(
        os.path.dirname(__file__), "web", "static", "kiel_districts.geojson")))["features"]]
    rows = conn.execute(
        """SELECT district, price_eur, size_m2 FROM listings WHERE source=? AND category=?
           AND gone_at IS NULL AND price_eur>0 AND size_m2>0""", (source, category)).fetchall()
    pre = f"rent.{category}"
    out = [(f"{pre}.active", ts, len(rows)), (f"{pre}.new", ts, new), (f"{pre}.gone", ts, gone)]
    if rows:
        out.append((f"{pre}.median_eur_m2", ts, statistics.median(r["price_eur"] / r["size_m2"] for r in rows)))
        out.append((f"{pre}.median_price", ts, statistics.median(r["price_eur"] for r in rows)))
    by = {}
    for r in rows:
        d = geo.polygon_name(r["district"], polys)
        if d:
            by.setdefault(d, []).append(r["price_eur"] / r["size_m2"])
    for d, v in by.items():
        if len(v) >= 3:  # don't track districts from 1-2 listings
            out.append((f"{pre}.eur_m2.{d}", ts, statistics.median(v)))
    put_metrics(conn, out)
