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


GEO_SCHEMA = """
CREATE TABLE IF NOT EXISTS geocache (query TEXT PRIMARY KEY, lat REAL, lon REAL, ts TEXT NOT NULL);
"""


def connect(path: str) -> sqlite3.Connection:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA + GEO_SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    for c in ("lat", "lon"):  # lightweight migration
        if c not in cols:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {c} REAL")
    return conn


def save_run(conn, source: str, category: str, items: list, complete: bool) -> tuple:
    """Upsert listings. If the crawl was complete, mark unseen listings as gone.
    Returns (new, gone)."""
    ts = now()
    new = 0
    for it in items:
        cur = conn.execute(
            "SELECT 1 FROM listings WHERE source=? AND id=?", (source, it["id"])
        ).fetchone()
        if cur is None:
            new += 1
            conn.execute(
                """INSERT INTO listings (source,id,category,title,district,street,flat_desc,
                   price_eur,size_m2,available_from,url,first_seen,last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source, it["id"], category, it["title"], it["district"], it["street"],
                 it["flat_desc"], it["price_eur"], it["size_m2"], it["available_from"],
                 it["url"], ts, ts),
            )
        else:
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
    conn.commit()
    return new, gone
