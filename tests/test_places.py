"""Places (city/district hierarchy) and Place Metrics (metric, place, ts)."""
import sqlite3

from fastapi.testclient import TestClient

from statking import db
from statking.sources import opendata_sh
from statking.web import app as webapp

from test_migrations import make_legacy


def _seed_old(path):
    make_legacy(path)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE district_metrics (dataset TEXT NOT NULL, merkmal TEXT NOT NULL, district TEXT NOT NULL,
            ts TEXT NOT NULL, value REAL, PRIMARY KEY (dataset, merkmal, district, ts));
        INSERT INTO district_metrics VALUES ('einwohner','Einwohner gesamt','Gaarden','2022-12-31',100.0),
            ('einwohner','Einwohner gesamt','Gaarden','2023-12-31',110.0),
            ('einwohner','Einwohner gesamt','Schilksee','2023-12-31',50.0);
        INSERT INTO metrics VALUES ('rent.kiel.wg.eur_m2.Gaarden','2024-01-01',15.5),
            ('rent.hamburg.wg.eur_m2.St. Pauli','2024-01-01',22.0),
            ('rent.kiel.wg.median_eur_m2','2024-02-01',19.0);
    """)
    c.commit()
    c.close()


def test_migration_moves_district_metrics_and_per_district_rent_into_place_metrics(tmp_path):
    p = str(tmp_path / "s.db")
    _seed_old(p)
    conn = db.connect(p)

    assert db.read_place_metric(conn, "einwohner.Einwohner gesamt") == {
        "kiel/Gaarden": {"2022-12-31": 100.0, "2023-12-31": 110.0}, "kiel/Schilksee": {"2023-12-31": 50.0}}
    assert db.read_place_metric(conn, "rent.wg.eur_m2") == {
        "kiel/Gaarden": {"2024-01-01": 15.5}, "hamburg/St. Pauli": {"2024-01-01": 22.0}}
    # city-level rent stays a Series; per-district Series names and the old table are gone
    assert [r[0] for r in conn.execute("SELECT DISTINCT series FROM metrics WHERE series LIKE 'rent.%'")] == ["rent.kiel.wg.median_eur_m2"]
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='district_metrics'").fetchone()
    places = {x["id"]: (x["kind"], x["parent_id"], x["name"]) for x in db.places(conn)}
    assert places["kiel"] == ("city", None, "Kiel")
    assert places["kiel/Gaarden"] == ("district", "kiel", "Gaarden")
    assert places["hamburg/St. Pauli"] == ("district", "hamburg", "St. Pauli")


def test_record_rent_metrics_writes_place_metrics_not_series(conn):
    for i in range(3):
        conn.execute("""INSERT INTO listings (source,id,category,district,price_eur,size_m2,first_seen,last_seen,city)
                        VALUES ('wg',?,'wg','Schilksee',400,20,'2024-01-01','2024-01-01','kiel')""", (str(i),))
    db.record_rent_metrics(conn, "wg", "wg", "2024-01-01", 3, 0, "kiel")
    assert db.read_place_metric(conn, "rent.wg.eur_m2") == {"kiel/Schilksee": {"2024-01-01": 20.0}}
    assert not conn.execute("SELECT 1 FROM metrics WHERE series LIKE '%eur_m2.%'").fetchone()
    assert conn.execute("SELECT 1 FROM metrics WHERE series='rent.kiel.wg.median_eur_m2'").fetchone()
    assert {x["id"] for x in db.places(conn, parent="kiel")} == {"kiel/Schilksee"}


def test_stadtteil_datasets_are_written_as_place_metrics(conn, monkeypatch):
    csv_text = "Merkmal;Stadtteil;2023\nEinwohner;Gaarden;1.000\n"
    monkeypatch.setattr(opendata_sh, "DATASETS", {})
    monkeypatch.setattr(opendata_sh, "DISTRICT_DATASETS", {"einwohner": ("x", "Einwohner", "Personen")})
    monkeypatch.setattr(opendata_sh, "fetch_csv", lambda cid: csv_text)
    opendata_sh.collect(conn, {})
    assert db.read_place_metric(conn, "einwohner.Einwohner") == {"kiel/Gaarden": {"2023-12-31": 1000.0}}


def test_place_metric_names_and_places_filter(conn):
    db.put_place_metrics(conn, [("a.x", "kiel", "Gaarden", "2024-01-01", 1.0), ("b.y", "kiel", None, "2024-01-01", 2.0)])
    assert db.place_metric_names(conn) == ["a.x", "b.y"]
    assert db.place_metric_names(conn, prefix="a.") == ["a.x"]
    assert [x["id"] for x in db.places(conn, kind="city")] == ["kiel"]
    assert db.read_place_metric(conn, "b.y") == {"kiel": {"2024-01-01": 2.0}}


def test_layer_endpoint_reads_place_metrics(tmp_path, monkeypatch, conn):
    monkeypatch.setattr(webapp, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(webapp.cities, "district_names", lambda c: ["Schilksee"])
    db.put_place_metrics(conn, [("einwohner.Einwohner", "kiel", "Schilksee", "2022-12-31", 1.0),
                                ("einwohner.Einwohner", "kiel", "Schilksee", "2023-12-31", 2.0)])
    conn.commit()
    got = TestClient(webapp.app).get("/api/layer", params={"id": "einwohner"}).json()
    assert got["year"] == "2023" and got["values"] == {"Schilksee": 2.0}
