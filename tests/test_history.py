from fastapi.testclient import TestClient

from statking import jobs, search
from statking.web import app as webapp

from conftest import related_walks


def _snap(conn, ts, a, b, verdict, lag=0):
    cur = conn.execute("INSERT INTO snapshots (ts, params, hypotheses, real_count) VALUES (?,?,?,?)", (ts, "{}", 1, int(verdict == "real")))
    conn.execute("""INSERT INTO snapshot_results (snapshot_id, series_a, series_b, lag, res, n, verdict)
                    VALUES (?,?,?,?,?,?,?)""", (cur.lastrowid, a, b, lag, "day", 100, verdict))
    conn.commit()


def test_history_reports_when_a_pair_became_and_stopped_being_real(conn):
    for ts, v in [("2026-01-01", "coincidence"), ("2026-01-02", "real"), ("2026-01-03", "real"),
                  ("2026-01-04", "coincidence"), ("2026-01-05", "real")]:
        _snap(conn, ts, "x.a", "y.b", v)
    h = search.pair_history(conn, "y.b", "x.a")  # pair is unordered
    assert [s["real"] for s in h["snapshots"]] == [False, True, True, False, True]
    assert [(e["ts"], e["event"]) for e in h["events"]] == [
        ("2026-01-02", "became_real"), ("2026-01-04", "stopped_real"), ("2026-01-05", "became_real")]


def test_history_is_real_if_any_lag_is_real_and_ignores_other_pairs(conn):
    _snap(conn, "2026-01-01", "x.a", "y.b", "coincidence", lag=0)
    conn.execute("INSERT INTO snapshot_results (snapshot_id, series_a, series_b, lag, res, n, verdict) VALUES (1,'x.a','y.b',1,'day',90,'real')")
    _snap(conn, "2026-01-02", "x.a", "z.c", "real")
    h = search.pair_history(conn, "x.a", "y.b")
    assert [s["real"] for s in h["snapshots"]] == [True, None]  # second Snapshot did not test the pair
    assert [e["event"] for e in h["events"]] == ["became_real"]  # ...which is not evidence it stopped being Real


def test_history_over_http(tmp_path, monkeypatch, conn):
    monkeypatch.setattr(webapp, "DB_PATH", str(tmp_path / "test.db"))
    _snap(conn, "2026-01-01", "x.a", "y.b", "real")
    r = TestClient(webapp.app).get("/api/search/history", params={"series_a": "x.a", "series_b": "y.b"})
    assert r.json()["events"][0]["event"] == "became_real"


def test_search_job_is_scheduled_nightly_and_stores_a_snapshot(conn, monkeypatch, seed_series):
    assert jobs.JOBS["search"][1] == 24
    a, b = related_walks(seed=1)
    seed_series(test__a=a, test__b=b)
    jobs.run_due(conn, ["search"])
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert conn.execute("SELECT ok FROM job_runs WHERE job='search'").fetchone()[0] == 1
    jobs.run_due(conn, ["search"])  # not due again within the interval
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
