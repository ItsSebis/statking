import random

from statking.search import latest_snapshot, run_search

from conftest import random_walk, related_walks


def verdicts(snapshot):
    return {(r["series_a"], r["series_b"]): r["verdict"] for r in snapshot["results"]}


def test_planted_relationship_is_marked_real(conn, seed_series):
    a, b = related_walks(seed=1)
    noise = random_walk(random.Random(2), 200)
    seed_series(test__a=a, test__b=b, test__noise=noise)

    snap = run_search(conn)

    v = verdicts(snap)
    assert v[("test.a", "test.b")] == "real"
    assert v[("test.a", "test.noise")] != "real"
    assert v[("test.b", "test.noise")] != "real"


def test_independent_noise_series_are_not_marked_real(conn, seed_series):
    # Uncorrected at alpha=0.01, 15 pairs give ~14% chance of a fluke; the seed is fixed so this is
    # deterministic. FDR correction (later slice) removes the dependence on the seed.
    rng = random.Random(7)
    seed_series(**{f"noise__{i}": random_walk(rng, 200) for i in range(6)})

    snap = run_search(conn)

    assert snap["hypotheses"] == 15
    assert snap["real_count"] == 0
    assert all(r["verdict"] != "real" for r in snap["results"])


def test_snapshot_is_stored_with_parameters_and_per_pair_results(conn, seed_series):
    a, b = related_walks(seed=1)
    seed_series(test__a=a, test__b=b)

    run_search(conn, {"alpha": 0.05})
    stored = latest_snapshot(conn)

    assert stored["params"]["alpha"] == 0.05
    assert stored["hypotheses"] == 1
    (r,) = stored["results"]
    assert (r["series_a"], r["series_b"], r["lag"], r["verdict"]) == ("test.a", "test.b", 0, "real")
    assert r["spearman"] > 0.5 and r["n"] == 199


def test_short_series_are_too_little_data(conn, seed_series):
    a, b = related_walks(seed=1, n=10)
    seed_series(test__a=a, test__b=b)

    (r,) = run_search(conn)["results"]

    assert r["verdict"] == "too_little_data"


def test_large_noise_dataset_yields_no_real_correlations(conn, seed_series):
    # 190 Hypotheses: seed 1 gives 3 uncorrected flukes at alpha=0.01, so only the FDR
    # correction across the whole Search keeps this at zero.
    rng = random.Random(1)
    seed_series(**{f"noise__{i}": random_walk(rng, 200) for i in range(20)})

    snap = run_search(conn)

    assert snap["hypotheses"] == 190
    assert snap["real_count"] == 0


def test_planted_relationship_survives_correction_among_many_hypotheses(conn, seed_series):
    a, b = related_walks(seed=1)
    rng = random.Random(3)  # not 1: related_walks(seed=1) would be reproduced as "noise"
    seed_series(test__a=a, test__b=b, **{f"noise__{i}": random_walk(rng, 200) for i in range(18)})

    snap = run_search(conn)

    assert snap["hypotheses"] == 190
    assert verdicts(snap)[("test.a", "test.b")] == "real"
    assert snap["real_count"] == 1
