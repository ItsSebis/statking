"""Adjusted Series: remove trend and seasonality from a bucketed Series.

Trend: first differencing (handles deterministic and stochastic trends alike). Seasonality: where there is
a gap-free history of at least MIN_CYCLES seasonal periods, the mean change at each position in the cycle
is subtracted. Unlike a moving-average detrend this does no smoothing, so it does not smear a Series over
neighbouring Lags. Without that history the Adjusted Series is plain first differences (the fallback)."""
from . import correlate

PERIODS = {"day": [365, 7], "month": [12], "year": []}  # candidate seasonal periods, longest first
MIN_CYCLES = 3


def _contiguous(keys, res):
    return all(correlate.shift(a, res, 1) == b for a, b in zip(keys, keys[1:]))


def _period(keys, res):
    """Longest seasonal period with enough gap-free history, else None."""
    if not _contiguous(keys, res):
        return None
    return next((p for p in PERIODS[res] if len(keys) >= MIN_CYCLES * p), None)


def deseasoned(diffs, res):
    """Subtract the per-phase mean from a differenced Series -> {bucket: value}, or None when the
    history does not allow estimating seasonality."""
    keys = sorted(diffs)
    period = _period(keys, res)
    if period is None:
        return None
    by_phase = {}
    for i, k in enumerate(keys):
        by_phase.setdefault(i % period, []).append(diffs[k])
    means = {p: sum(v) / len(v) for p, v in by_phase.items()}
    return {k: diffs[k] - means[i % period] for i, k in enumerate(keys)}


def adjust_pair(da, db_, res):
    """Adjust two {bucket: value} dicts the same way. -> (adjusted_a, adjusted_b, method, differenced_a,
    differenced_b). method is "detrend_deseason" when both Series have the history for deseasoning, else
    "differenced" (and the differenced pair, used to tell trend from seasonality, is None)."""
    ta, tb = correlate.differenced(da, res), correlate.differenced(db_, res)
    sa, sb = deseasoned(ta, res), deseasoned(tb, res)
    if sa is not None and sb is not None:
        return sa, sb, "detrend_deseason", ta, tb
    return ta, tb, "differenced", None, None
