import pytest

from statking.search import latest_snapshot, run_search

from conftest import random_walk, related_walks
import random


@pytest.fixture
def snap(conn, seed_series):
    a, b = related_walks(seed=1)
    seed_series(weather__a=a, wiki__b=b, wiki__c=random_walk(random.Random(3), 200))
    return run_search(conn)


def pairs(snapshot):
    return {(r["series_a"], r["series_b"]) for r in snapshot["results"]}


def test_results_can_be_filtered_by_source(conn, snap):
    # a pair matches when either of its Series comes from the Source
    assert pairs(latest_snapshot(conn, source="weather")) == {("weather.a", "wiki.b"), ("weather.a", "wiki.c")}
    assert len(latest_snapshot(conn, source="wiki")["results"]) == 3
    assert latest_snapshot(conn, source="steam")["results"] == []


def test_results_can_be_filtered_by_resolution(conn, snap):
    assert len(latest_snapshot(conn, res="day")["results"]) == 3
    assert latest_snapshot(conn, res="month")["results"] == []


def test_unknown_resolution_filter_is_rejected(conn, snap):
    with pytest.raises(ValueError):
        latest_snapshot(conn, res="decade")
