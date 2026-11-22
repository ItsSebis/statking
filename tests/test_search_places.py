import random

from statking import db
from statking.search import latest_snapshot, run_search

from conftest import put_series, related_walks

TS = "2024-06-01"


def put_places(conn, metric, values, ts=TS):
    db.put_place_metrics(conn, [(metric, "kiel", f"D{i:02d}", ts, v) for i, v in enumerate(values)])
    conn.commit()


def planted(seed=1, n=40):
    rng = random.Random(seed)
    a = [rng.gauss(0, 1) for _ in range(n)]
    b = [x * 2 + rng.gauss(0, 0.3) for x in a]
    c = [rng.gauss(0, 1) for _ in range(n)]
    return a, b, c


def test_place_metric_pairs_are_judged_and_counted_as_hypotheses(conn):
    a, b, c = planted()
    put_places(conn, "geo.a", a)
    put_places(conn, "geo.b", b)
    put_places(conn, "geo.c", c)

    snap = run_search(conn)

    by_pair = {(r["series_a"], r["series_b"]): r for r in snap["results"]}
    assert snap["hypotheses"] == 3
    assert by_pair[("geo.a", "geo.b")]["verdict"] == "real"
    assert by_pair[("geo.a", "geo.b")]["kind"] == "across_places"
    assert by_pair[("geo.a", "geo.b")]["n"] == 40
    assert by_pair[("geo.a", "geo.c")]["verdict"] == "coincidence"
    assert by_pair[("geo.a", "geo.c")]["reason"] == "not_significant"
    assert snap["real_count"] == 1


def test_places_share_one_list_and_one_fdr_correction_with_over_time_pairs(conn):
    a, b, c = planted()
    put_places(conn, "geo.a", a)
    put_places(conn, "geo.b", b)
    wa, wb = related_walks(seed=1)
    put_series(conn, "test.a", wa)
    put_series(conn, "test.b", wb)

    snap = run_search(conn)

    kinds = {(r["series_a"], r["series_b"]): r["kind"] for r in snap["results"]}
    assert kinds == {("geo.a", "geo.b"): "across_places", ("test.a", "test.b"): "over_time"}
    assert snap["hypotheses"] == 2 and snap["real_count"] == 2
    assert {r["kind"] for r in latest_snapshot(conn)["results"]} == {"across_places", "over_time"}


def test_too_few_places_is_too_little_data(conn):
    a, b, _ = planted(n=5)
    put_places(conn, "geo.a", a)
    put_places(conn, "geo.b", b)

    (r,) = run_search(conn)["results"]

    assert (r["verdict"], r["reason"], r["kind"]) == ("too_little_data", "too_few_buckets", "across_places")


def test_only_places_present_in_both_metrics_are_aligned(conn):
    a, b, _ = planted(n=40)
    put_places(conn, "geo.a", a)
    put_places(conn, "geo.b", b[:30])

    (r,) = run_search(conn)["results"]

    assert r["n"] == 30


def test_two_metrics_are_aligned_on_a_common_timestamp(conn):
    a, b, c = planted(n=40)
    put_places(conn, "geo.a", a, ts="2023-01-01")
    put_places(conn, "geo.b", b, ts="2023-01-01")
    put_places(conn, "geo.a", c, ts="2024-01-01")  # only a has a later snapshot

    (r,) = run_search(conn)["results"]

    assert r["n"] == 40 and r["verdict"] == "real"  # compared at 2023-01-01, not a@2024 vs b@2023


def test_resolution_filter_can_select_across_places_rows(conn):
    a, b, _ = planted()
    put_places(conn, "geo.a", a)
    put_places(conn, "geo.b", b)
    wa, wb = related_walks(seed=1)
    put_series(conn, "test.a", wa)
    put_series(conn, "test.b", wb)
    run_search(conn)

    assert {r["kind"] for r in latest_snapshot(conn, res="place")["results"]} == {"across_places"}
    assert {r["kind"] for r in latest_snapshot(conn, res="day")["results"]} == {"over_time"}
