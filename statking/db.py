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
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
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


def _migration_1_baseline(conn):
    """The ad hoc column/data migrations that used to run on every connect.
    Idempotent so the existing production DB (which already has most of this) is unaffected."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    for c in ("lat", "lon"):
        if c not in cols:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {c} REAL")
    if "city" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN city TEXT NOT NULL DEFAULT 'kiel'")  # everything scraped so far was Kiel
    if "city" not in {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}:
        conn.execute("ALTER TABLE runs ADD COLUMN city TEXT NOT NULL DEFAULT 'kiel'")
    # rent series gained a city segment: rent.<cat>.x -> rent.<city>.<cat>.x
    conn.execute("""UPDATE OR IGNORE metrics SET series='rent.kiel.'||substr(series,6)
                    WHERE (series LIKE 'rent.wg.%' OR series LIKE 'rent.1zi.%' OR series LIKE 'rent.wohnung.%')""")
    conn.execute(
        """INSERT OR IGNORE INTO price_history (source,id,ts,price_eur)
           SELECT source,id,first_seen,price_eur FROM listings WHERE price_eur IS NOT NULL""")


def _migration_2_snapshots(conn):
    """Search results: one row per Search (parameters, Hypothesis count) + one per tested pair."""
    conn.executescript("""
        CREATE TABLE snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, params TEXT NOT NULL,
            hypotheses INTEGER NOT NULL, real_count INTEGER NOT NULL);
        CREATE TABLE snapshot_results (
            snapshot_id INTEGER NOT NULL REFERENCES snapshots(id), series_a TEXT NOT NULL, series_b TEXT NOT NULL,
            lag INTEGER NOT NULL, res TEXT NOT NULL, n INTEGER NOT NULL, spearman REAL, pearson_raw REAL,
            p_value REAL, verdict TEXT NOT NULL, reason TEXT,
            PRIMARY KEY (snapshot_id, series_a, series_b, lag));""")


# (version, function) applied in order, once each. Append only; never edit a shipped migration.
MIGRATIONS = [
    (1, _migration_1_baseline),
    (2, _migration_2_snapshots),
]


def _applied_versions(conn) -> set:
    try:
        return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    except sqlite3.OperationalError:
        return set()


def _backup(path: str) -> str:
    dest = f"{path}.bak-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
    src = sqlite3.connect(path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def connect(path: str) -> sqlite3.Connection:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    existing = os.path.exists(path) and os.path.getsize(path) > 0
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    pending = [m for m in MIGRATIONS if m[0] not in _applied_versions(conn)]
    if pending and existing:
        _backup(path)  # before anything touches the schema
    conn.executescript(SCHEMA + GEO_SCHEMA + HISTORY_SCHEMA)
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    for version, fn in pending:
        fn(conn)
        conn.execute("INSERT INTO schema_migrations VALUES (?,?)", (version, now()))
        conn.commit()
    return conn


def save_run(conn, source: str, category: str, items: list, complete: bool, city: str = "kiel") -> tuple:
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
                   price_eur,size_m2,available_from,url,first_seen,last_seen,city)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (source, it["id"], category, it["title"], it["district"], it["street"],
                 it["flat_desc"], it["price_eur"], it["size_m2"], it["available_from"],
                 it["url"], ts, ts, city),
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
            """UPDATE listings SET gone_at=? WHERE source=? AND category=? AND city=?
               AND gone_at IS NULL AND last_seen<?""",
            (ts, source, category, city, ts),
        ).rowcount
    conn.execute("INSERT INTO runs (ts,source,category,listings_found,complete,city) VALUES (?,?,?,?,?,?)", (ts, source, category, len(items), int(complete), city))
    if complete:
        record_rent_metrics(conn, source, category, ts, new, gone, city)
    conn.commit()
    return new, gone


def put_metrics(conn, rows):
    """rows: iterable of (series, ts, value). Idempotent."""
    conn.executemany("INSERT OR REPLACE INTO metrics VALUES (?,?,?)", list(rows))


def record_rent_metrics(conn, source, category, ts, new, gone, city="kiel"):
    """Snapshot of the market at this scrape: city + per-district median EUR/m2."""
    from . import cities, geo  # local imports: geo imports db
    import statistics
    polys = cities.district_names(city)
    rows = conn.execute(
        """SELECT district, price_eur, size_m2 FROM listings WHERE source=? AND category=? AND city=?
           AND gone_at IS NULL AND price_eur>0 AND size_m2>0""", (source, category, city)).fetchall()
    pre = f"rent.{city}.{category}"
    out = [(f"{pre}.active", ts, len(rows)), (f"{pre}.new", ts, new), (f"{pre}.gone", ts, gone)]
    if rows:
        out.append((f"{pre}.median_eur_m2", ts, statistics.median(r["price_eur"] / r["size_m2"] for r in rows)))
        out.append((f"{pre}.median_price", ts, statistics.median(r["price_eur"] for r in rows)))
    by = {}
    for r in rows:
        d = geo.polygon_name(r["district"], polys, city)
        if d:
            by.setdefault(d, []).append(r["price_eur"] / r["size_m2"])
    for d, v in by.items():
        if len(v) >= 3:  # don't track districts from 1-2 listings
            out.append((f"{pre}.eur_m2.{d}", ts, statistics.median(v)))
    put_metrics(conn, out)
