import random
from datetime import date, timedelta

import pytest

from statking.search import latest_snapshot, run_search

from conftest import ar1_walk, put_series, lagged_walks, random_walk, related_walks, trended


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


def test_autocorrelated_but_independent_series_are_not_marked_real(conn, seed_series):
    # AR(1) changes (phi=0.8) inflate a naive test's variance ~4x: the naive p-value marks 4 of
    # these 28 independent pairs Real. Block-bootstrap p-values must not.
    rng = random.Random(1)
    seed_series(**{f"ar__{i}": ar1_walk(rng, 200) for i in range(8)})

    snap = run_search(conn)

    assert snap["hypotheses"] == 28
    assert snap["real_count"] == 0


def test_p_values_are_deterministic_for_a_fixed_seed(conn, seed_series):
    a, b = related_walks(seed=1, strength=0.3)
    seed_series(test__a=a, test__b=b)

    first = run_search(conn, {"seed": 5})["results"][0]["p_value"]
    again = run_search(conn, {"seed": 5})["results"][0]["p_value"]
    other = run_search(conn, {"seed": 6})["results"][0]["p_value"]

    assert first == again
    assert first != other


def test_default_seed_is_fixed_so_unseeded_searches_repeat(conn, seed_series):
    a, b = related_walks(seed=1, strength=0.3)
    seed_series(test__a=a, test__b=b)

    assert run_search(conn)["results"][0]["p_value"] == run_search(conn)["results"][0]["p_value"]


def test_pearson_on_adjusted_series_is_stored_next_to_spearman(conn, seed_series):
    a = random_walk(random.Random(4), 200)
    seed_series(test__a=a, test__b=[3 * v + 1 for v in a])  # b's changes are exactly 3x a's

    stored = latest_snapshot(conn, run_search(conn)["id"])
    (r,) = stored["results"]

    assert r["pearson"] == pytest.approx(1.0)
    assert r["spearman"] == pytest.approx(1.0)
    assert r["pearson_raw"] == pytest.approx(1.0)


def test_planted_lagged_relationship_is_found_at_the_correct_lag(conn, seed_series):
    a, b = lagged_walks(seed=1, lag=3)
    seed_series(test__a=a, test__b=b)

    snap = run_search(conn, {"lags": list(range(-5, 6))})

    real = [r["lag"] for r in snap["results"] if r["verdict"] == "real"]
    assert real == [3]  # b follows a by 3 days; neighbouring lags are not Real
    assert snap["results"][0]["lag"] == 3  # and it ranks first


def test_every_lag_is_a_hypothesis_in_the_count(conn, seed_series):
    rng = random.Random(7)
    seed_series(**{f"noise__{i}": random_walk(rng, 200) for i in range(4)})

    snap = run_search(conn, {"lags": [-2, -1, 0, 1, 2]})

    assert snap["hypotheses"] == 6 * 5
    assert sorted({r["lag"] for r in snap["results"]}) == [-2, -1, 0, 1, 2]
    assert snap["params"]["lags"] == [-2, -1, 0, 1, 2]
    assert snap["real_count"] == 0


def test_lag_range_param_expands_to_lags(conn, seed_series):
    a, b = lagged_walks(seed=1, lag=3)
    seed_series(test__a=a, test__b=b)

    snap = run_search(conn, {"max_lag": 4})

    assert sorted(r["lag"] for r in snap["results"]) == list(range(-4, 5))


def test_invalid_or_duplicate_lag_params(conn, seed_series):
    a, b = lagged_walks(seed=1, lag=3)
    seed_series(test__a=a, test__b=b)

    for bad in ({"max_lag": -1}, {"max_lag": 1.5}, {"max_lag": 1, "lags": [0]}, {"lags": [0.5]}):
        with pytest.raises(ValueError):
            run_search(conn, bad)
    assert run_search(conn, {"lags": [1, 0, 1]})["hypotheses"] == 2


def only(snap):
    (r,) = snap["results"]
    return r


def test_series_sharing_only_a_trend_are_a_trend_driven_coincidence(conn, seed_series):
    seed_series(test__a=trended(1, slope=1.0), test__b=trended(2, slope=2.0))

    r = only(run_search(conn))

    assert r["pearson_raw"] > 0.95  # Raw Correlation is stored and looks like a real relationship
    assert r["verdict"] == "coincidence"
    assert r["reason"] == "trend_driven"
    assert r["adjustment"] == "detrend_deseason"


def test_series_sharing_only_seasonality_are_a_seasonality_driven_coincidence(conn, seed_series):
    seed_series(test__a=trended(1, slope=0, season=5), test__b=trended(2, slope=0, season=5))

    r = only(run_search(conn))

    assert r["pearson_raw"] > 0.8
    assert (r["verdict"], r["reason"]) == ("coincidence", "seasonality_driven")


def test_genuine_relationship_survives_trend_and_seasonality(conn, seed_series):
    a, b = related_walks(seed=1, strength=0.9)
    shared = trended(5, slope=0.5, season=4, noise=0)
    seed_series(test__a=[x + s for x, s in zip(a, shared)], test__b=[x + s for x, s in zip(b, shared)])

    r = only(run_search(conn))

    assert r["verdict"] == "real"
    assert r["adjustment"] == "detrend_deseason"


def test_gappy_history_falls_back_to_differencing(conn, seed_series):
    a, b = related_walks(seed=1, n=60)
    for name, vals in (("test.a", a), ("test.b", b)):
        put_series(conn, name, vals[:30])
        put_series(conn, name, vals[31:], start=date(2024, 1, 1) + timedelta(days=31))  # day 30 missing

    r = only(run_search(conn, {"min_buckets": 20}))

    assert r["adjustment"] == "differenced"
    assert r["verdict"] == "real"
