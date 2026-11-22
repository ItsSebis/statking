"""SQLite storage. One row per listing; first_seen/last_seen give listing lifetime."""
import os
import re
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


def _migration_3_q_value(conn):
    """FDR-adjusted p-value (Benjamini-Hochberg q) per tested Hypothesis."""
    conn.execute("ALTER TABLE snapshot_results ADD COLUMN q_value REAL")


def _migration_4_pearson_adjusted(conn):
    """Pearson on the Adjusted Series, stored as a secondary number next to Spearman."""
    conn.execute("ALTER TABLE snapshot_results ADD COLUMN pearson REAL")


def _migration_5_adjustment(conn):
    """How the Series were adjusted for this Hypothesis: detrend_deseason or differenced."""
    conn.execute("ALTER TABLE snapshot_results ADD COLUMN adjustment TEXT")


def _migration_6_pair_reviews(conn):
    """Review State per pair, independent of Snapshots; no row means unreviewed."""
    conn.execute("""CREATE TABLE pair_reviews (
        series_a TEXT NOT NULL, series_b TEXT NOT NULL, state TEXT NOT NULL, note TEXT, ts TEXT NOT NULL,
        PRIMARY KEY (series_a, series_b))""")


def _migration_7_source_health(conn):
    """Per-Source last success and consecutive failures."""
    conn.execute("""CREATE TABLE source_health (
        source TEXT PRIMARY KEY, last_success TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0, last_error TEXT)""")


def _city_name(city):
    from . import cities  # local import: keeps db importable without the registry at module load
    return cities.CITIES.get(city, {}).get("name") or city.capitalize()


def _ensure_place(conn, city, district=None):
    """Place id: the city key ('kiel') or '<city>/<district>'. Creates the row (and the parent city) if missing."""
    conn.execute("INSERT OR IGNORE INTO places (id,name,kind,parent_id) VALUES (?,?,'city',NULL)", (city, _city_name(city)))
    if district is None:
        return city
    pid = f"{city}/{district}"
    conn.execute("INSERT OR IGNORE INTO places (id,name,kind,parent_id) VALUES (?,?,'district',?)", (pid, district, city))
    return pid


def _migration_8_places(conn):
    """Places (city/district hierarchy) + Place Metrics (metric, place, ts); replaces district_metrics and the
    per-district rent Series (rent.<city>.<cat>.eur_m2.<District> -> metric rent.<cat>.eur_m2 at place <city>/<District>)."""
    conn.executescript("""
        CREATE TABLE places (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('city','district')),
            parent_id TEXT REFERENCES places(id));
        CREATE TABLE place_metrics (
            metric TEXT NOT NULL, place TEXT NOT NULL REFERENCES places(id), ts TEXT NOT NULL, value REAL,
            PRIMARY KEY (metric, place, ts));""")
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='district_metrics'").fetchone():
        # Stadtteil datasets were Kiel-only
        for dataset, merkmal, district, ts, value in conn.execute(
                "SELECT dataset,merkmal,district,ts,value FROM district_metrics").fetchall():
            conn.execute("INSERT OR REPLACE INTO place_metrics VALUES (?,?,?,?)",
                         (f"{dataset}.{merkmal}", _ensure_place(conn, "kiel", district), ts, value))
        conn.execute("DROP TABLE district_metrics")
    pat = re.compile(r"^rent\.([^.]+)\.([^.]+)\.eur_m2\.(.+)$")
    for series, ts, value in conn.execute("SELECT series,ts,value FROM metrics WHERE series LIKE 'rent.%.eur_m2.%'").fetchall():
        m = pat.match(series)
        if m:
            city, cat, district = m.groups()
            conn.execute("INSERT OR REPLACE INTO place_metrics VALUES (?,?,?,?)",
                         (f"rent.{cat}.eur_m2", _ensure_place(conn, city, district), ts, value))
            conn.execute("DELETE FROM metrics WHERE series=? AND ts=?", (series, ts))


# (version, function) applied in order, once each. Append only; never edit a shipped migration.
MIGRATIONS = [
    (1, _migration_1_baseline),
    (2, _migration_2_snapshots),
    (3, _migration_3_q_value),
    (4, _migration_4_pearson_adjusted),
    (5, _migration_5_adjustment),
    (6, _migration_6_pair_reviews),
    (7, _migration_7_source_health),
    (8, _migration_8_places),
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


def put_place_metrics(conn, rows):
    """rows: iterable of (metric, city, district_or_None, ts, value); district None means the city itself.
    Creates the Places as needed. Idempotent."""
    conn.executemany("INSERT OR REPLACE INTO place_metrics VALUES (?,?,?,?)",
                     [(m, _ensure_place(conn, city, d), ts, v) for m, city, d, ts, v in rows])


def read_place_metric(conn, metric, ts=None) -> dict:
    """One Place Metric as {place_id: {ts: value}}; ts restricts to a single timestamp."""
    q, args = "SELECT place, ts, value FROM place_metrics WHERE metric=?", [metric]
    if ts is not None:
        q, args = q + " AND ts=?", args + [ts]
    out = {}
    for r in conn.execute(q + " ORDER BY place, ts", args):
        out.setdefault(r["place"], {})[r["ts"]] = r["value"]
    return out


def place_metric_names(conn, prefix="") -> list:
    """Distinct Place Metric names (sorted), optionally only those starting with prefix."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT metric FROM place_metrics WHERE substr(metric,1,?)=? ORDER BY metric", (len(prefix), prefix))]


def places(conn, kind=None, parent=None) -> list:
    """Places as dicts (id, name, kind, parent_id), optionally filtered by kind and/or parent city id."""
    q, args, where = "SELECT id,name,kind,parent_id FROM places", [], []
    if kind:
        where.append("kind=?"); args.append(kind)
    if parent:
        where.append("parent_id=?"); args.append(parent)
    if where:
        q += " WHERE " + " AND ".join(where)
    return [dict(r) for r in conn.execute(q + " ORDER BY id", args)]


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
    by, place_rows = {}, []
    for r in rows:
        d = geo.polygon_name(r["district"], polys, city)
        if d:
            by.setdefault(d, []).append(r["price_eur"] / r["size_m2"])
    for d, v in by.items():
        if len(v) >= 3:  # don't track districts from 1-2 listings
            place_rows.append((f"rent.{category}.eur_m2", city, d, ts, statistics.median(v)))
    put_metrics(conn, out)
    put_place_metrics(conn, place_rows)
