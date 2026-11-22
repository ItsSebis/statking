import glob
import sqlite3

from statking import db

LEGACY = """
CREATE TABLE listings (
    source TEXT NOT NULL, id TEXT NOT NULL, category TEXT NOT NULL, title TEXT, district TEXT,
    street TEXT, flat_desc TEXT, price_eur INTEGER, size_m2 REAL, available_from TEXT, url TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, gone_at TEXT, PRIMARY KEY (source, id));
CREATE TABLE runs (ts TEXT NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL,
    listings_found INTEGER NOT NULL, complete INTEGER NOT NULL);
CREATE TABLE metrics (series TEXT NOT NULL, ts TEXT NOT NULL, value REAL, PRIMARY KEY (series, ts));
INSERT INTO listings (source,id,category,title,price_eur,size_m2,first_seen,last_seen)
    VALUES ('wg','1','wg','Room',400,15,'2024-01-01','2024-01-02');
INSERT INTO runs VALUES ('2024-01-01','wg','wg',1,1);
INSERT INTO metrics VALUES ('rent.wg.median_eur_m2','2024-01-01',20.0), ('weather.kiel.temp','2024-01-01',5.0);
"""


def make_legacy(path):
    c = sqlite3.connect(path)
    c.executescript(LEGACY)
    c.commit()
    c.close()


def backups(path):
    return glob.glob(str(path) + ".bak-*")


def test_legacy_db_is_migrated_and_data_kept(tmp_path):
    p = tmp_path / "s.db"
    make_legacy(p)
    conn = db.connect(str(p))
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(listings)")}
    assert {"lat", "lon", "city"} <= cols
    assert conn.execute("SELECT city FROM listings").fetchone()["city"] == "kiel"
    assert conn.execute("SELECT city FROM runs").fetchone()["city"] == "kiel"
    names = {r["series"] for r in conn.execute("SELECT series FROM metrics")}
    assert names == {"rent.kiel.wg.median_eur_m2", "weather.kiel.temp"}
    assert conn.execute("SELECT price_eur FROM price_history").fetchone()["price_eur"] == 400


def test_migrations_recorded_and_applied_once(tmp_path):
    p = str(tmp_path / "s.db")
    make_legacy(p)
    db.connect(p).close()
    conn = db.connect(p)
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert [r["version"] for r in rows] == [m[0] for m in db.MIGRATIONS]
    assert len(backups(p)) == 1  # second connect had nothing pending


def test_backup_made_before_migration_with_original_content(tmp_path):
    p = str(tmp_path / "s.db")
    make_legacy(p)
    db.connect(p).close()
    (b,) = backups(p)
    old = sqlite3.connect(b)
    assert "city" not in {r[1] for r in old.execute("PRAGMA table_info(listings)")}
    assert old.execute("SELECT series FROM metrics WHERE series LIKE 'rent.wg%'").fetchone()
    assert not old.execute("SELECT name FROM sqlite_master WHERE name='schema_migrations'").fetchone()


def test_fresh_db_gets_no_backup(tmp_path):
    p = str(tmp_path / "s.db")
    conn = db.connect(p)
    assert conn.execute("SELECT count(*) c FROM schema_migrations").fetchone()["c"] == len(db.MIGRATIONS)
    assert backups(p) == []


def test_legacy_db_gets_pair_reviews_table(tmp_path):
    p = tmp_path / "s.db"
    make_legacy(p)
    conn = db.connect(str(p))
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pair_reviews)")}
    assert {"series_a", "series_b", "state", "note"} <= cols
