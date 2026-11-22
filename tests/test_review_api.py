import pytest
from fastapi.testclient import TestClient

from statking.search import run_search
from statking.web import app as webapp

from conftest import related_walks


@pytest.fixture
def client(tmp_path, monkeypatch, seed_series, conn):
    monkeypatch.setattr(webapp, "DB_PATH", str(tmp_path / "test.db"))  # same file as the conn fixture
    a, b = related_walks(seed=1)
    seed_series(test__a=a, test__b=b)
    run_search(conn)
    return TestClient(webapp.app)


def test_review_can_be_set_and_filtered_over_http(client):
    r = client.put("/api/review", json={"series_a": "test.a", "series_b": "test.b", "state": "confirmed", "note": "same driver"})
    assert r.status_code == 200

    (hit,) = client.get("/api/search/latest", params={"review_state": "confirmed"}).json()["results"]
    assert (hit["review_state"], hit["review_note"]) == ("confirmed", "same driver")
    assert client.get("/api/search/latest", params={"review_state": "dismissed"}).json()["results"] == []


def test_invalid_review_is_a_400(client):
    r = client.put("/api/review", json={"series_a": "test.a", "series_b": "test.b", "state": "confirmed"})
    assert r.status_code == 400
    assert client.get("/api/search/latest", params={"review_state": "maybe"}).status_code == 400


def test_source_and_resolution_filters_over_http(client):
    assert len(client.get("/api/search/latest", params={"source": "test", "res": "day"}).json()["results"]) == 1
    assert client.get("/api/search/latest", params={"source": "wiki"}).json()["results"] == []
    assert client.get("/api/search/latest", params={"res": "decade"}).status_code == 400
