import pytest

from statking import review
from statking.search import latest_snapshot, run_search

from conftest import related_walks


@pytest.fixture
def snap(conn, seed_series):
    a, b = related_walks(seed=1)
    c, _ = related_walks(seed=5)
    seed_series(test__a=a, test__b=b, test__c=c)
    return run_search(conn)


def result(snapshot, a, b):
    return next(r for r in snapshot["results"] if (r["series_a"], r["series_b"]) == (a, b))


def test_pairs_start_unreviewed(snap):
    assert all(r["review_state"] == "unreviewed" and r["review_note"] is None for r in snap["results"])


def test_pair_can_be_confirmed_with_a_note(conn, snap):
    review.set_review(conn, "test.a", "test.b", "confirmed", "same driver")

    r = result(latest_snapshot(conn), "test.a", "test.b")
    assert (r["review_state"], r["review_note"]) == ("confirmed", "same driver")
    assert result(latest_snapshot(conn), "test.a", "test.c")["review_state"] == "unreviewed"


def test_pair_can_be_dismissed_changed_and_reset(conn, snap):
    review.set_review(conn, "test.a", "test.b", "dismissed")
    assert result(latest_snapshot(conn), "test.a", "test.b")["review_state"] == "dismissed"

    review.set_review(conn, "test.a", "test.b", "confirmed", "actually plausible")
    assert result(latest_snapshot(conn), "test.a", "test.b")["review_state"] == "confirmed"

    review.set_review(conn, "test.a", "test.b", "unreviewed")
    r = result(latest_snapshot(conn), "test.a", "test.b")
    assert (r["review_state"], r["review_note"]) == ("unreviewed", None)


def test_confirmed_link_needs_a_note(conn, snap):
    with pytest.raises(ValueError):
        review.set_review(conn, "test.a", "test.b", "confirmed")
    with pytest.raises(ValueError):
        review.set_review(conn, "test.a", "test.b", "confirmed", "   ")
    assert result(latest_snapshot(conn), "test.a", "test.b")["review_state"] == "unreviewed"


def test_unknown_state_is_rejected(conn, snap):
    with pytest.raises(ValueError):
        review.set_review(conn, "test.a", "test.b", "maybe")


def test_pair_order_does_not_matter(conn, snap):
    review.set_review(conn, "test.b", "test.a", "dismissed")
    assert result(latest_snapshot(conn), "test.a", "test.b")["review_state"] == "dismissed"


def test_review_survives_a_new_search_and_applies_to_old_snapshots(conn, snap):
    review.set_review(conn, "test.a", "test.b", "confirmed", "same driver")

    newer = run_search(conn)

    assert newer["id"] != snap["id"]
    assert result(newer, "test.a", "test.b")["review_state"] == "confirmed"
    assert result(latest_snapshot(conn, snap["id"]), "test.a", "test.b")["review_note"] == "same driver"


def pairs(snapshot):
    return {(r["series_a"], r["series_b"]) for r in snapshot["results"]}


def test_results_can_be_filtered_by_review_state(conn, snap):
    review.set_review(conn, "test.a", "test.b", "confirmed", "same driver")
    review.set_review(conn, "test.a", "test.c", "dismissed")

    assert pairs(latest_snapshot(conn, review_state="confirmed")) == {("test.a", "test.b")}
    assert pairs(latest_snapshot(conn, review_state="dismissed")) == {("test.a", "test.c")}
    assert pairs(latest_snapshot(conn, review_state="unreviewed")) == {("test.b", "test.c")}
    assert len(latest_snapshot(conn)["results"]) == 3


def test_unknown_review_state_filter_is_rejected(conn, snap):
    with pytest.raises(ValueError):
        latest_snapshot(conn, review_state="maybe")
