"""Source Health: last success + consecutive failures per job, shown over HTTP; outage gaps are never filled."""
import pytest
from fastapi.testclient import TestClient

from statking import jobs
from statking.search import run_search
from statking.web import app as webapp

from conftest import related_walks


@pytest.fixture
def flaky(monkeypatch):
    state = {"fail": False}

    def job(conn):
        if state["fail"]:
            raise RuntimeError("boom")

    monkeypatch.setitem(jobs.JOBS, "flaky", (job, 1, None))
    monkeypatch.setattr(jobs, "SOURCE_JOBS", {*jobs.SOURCE_JOBS, "flaky"})
    return state


@pytest.fixture
def client(tmp_path, monkeypatch, conn):
    monkeypatch.setattr(webapp, "DB_PATH", str(tmp_path / "test.db"))
    return TestClient(webapp.app)


def health(client, name="flaky"):
    return {h["source"]: h for h in client.get("/api/health").json()}[name]


def test_failures_count_up_and_a_success_resets_them(conn, client, flaky):
    jobs.run_job(conn, "flaky")
    ok = health(client)
    assert ok["consecutive_failures"] == 0 and ok["last_success"] is not None

    flaky["fail"] = True
    jobs.run_job(conn, "flaky")
    jobs.run_job(conn, "flaky")
    bad = health(client)
    assert bad["consecutive_failures"] == 2
    assert bad["last_success"] == ok["last_success"]  # failures leave the last success alone
    assert "boom" in bad["last_error"]

    flaky["fail"] = False
    jobs.run_job(conn, "flaky")
    assert health(client)["consecutive_failures"] == 0


def test_never_succeeded_source_has_no_last_success(conn, client, flaky):
    flaky["fail"] = True
    jobs.run_job(conn, "flaky")
    h = health(client)
    assert h["last_success"] is None and h["consecutive_failures"] == 1


def test_search_compares_only_buckets_with_real_observations(conn, seed_series):
    a, b = related_walks(seed=1, n=200)
    seed_series(test__a=a, test__b=b)
    full = run_search(conn)["results"][0]["n"]

    # outage: 20 days of b are missing (never stored); the Search must not fill them in
    conn.execute("DELETE FROM metrics WHERE series='test.b' AND ts BETWEEN '2024-03-01' AND '2024-03-20'")
    conn.commit()
    gap = run_search(conn)["results"][0]

    assert 0 < gap["n"] < full - 19  # the 20 missing buckets plus the diffs they break


def test_search_job_has_no_source_health(conn, client):
    jobs.run_job(conn, "search")
    assert "search" not in {h["source"] for h in client.get("/api/health").json()}
