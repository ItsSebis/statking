# statking

A personal, self-hosted tool that continuously collects data from many sources and searches all of it for relationships that are unlikely to be coincidence.

## Language

**Source**:
An origin of data (e.g. weather, WG-Gesucht, Statistikamt Nord) that is collected on a schedule and contributes Series.
_Avoid_: Scraper, feed, collector

**Series**:
A named sequence of values over time, identified by a dotted name (e.g. `rent.kiel.wg.median_eur_m2`).
_Avoid_: Metric, time series, signal

**Resolution**:
The time bucket (day, month, year) at which a Series is compared; two Series are compared at the coarser of their natural Resolutions.
_Avoid_: Granularity, frequency

**Family**:
A group of Series from one Source that are mechanically related (e.g. everything under `rent.kiel.wg`); pairs inside a Family may be Derived Pairs.
_Avoid_: Category, namespace, group

**Place**:
A city or a district within a city; the join key for Across-places Correlations.
_Avoid_: Location, area, region

**Source Health**:
Per-Source record of last success and consecutive failures; observations missing because of an outage are excluded from comparison, not filled in.
_Avoid_: Job status, uptime

**Place Metric**:
A value measured per district or city at a point in time, as opposed to a Series, which has no spatial dimension.
_Avoid_: District metric, geo metric

## Correlations

**Correlation**:
A measured statistical relationship between two Series (over time) or two Place Metrics (across places).
_Avoid_: Link, relationship, connection

**Over-time Correlation**:
A Correlation between two Series aligned by time bucket, optionally with a Lag.
_Avoid_: Time correlation

**Across-places Correlation**:
A Correlation between two Place Metrics aligned by place.
_Avoid_: Spatial correlation, cross-sectional correlation

**Lag**:
The number of Resolution buckets by which the second Series is shifted relative to the first.
_Avoid_: Offset, delay

**Adjusted Series**:
A Series after removing shared trend and seasonality (differencing, or detrend/deseason where history allows); the basis for judging whether a Correlation is real.
_Avoid_: Detrended series, stationary series

**Raw Correlation**:
A Correlation computed on unadjusted Series, shown for contrast but never counted as real.
_Avoid_: Naive correlation

## Verdict

**Hypothesis**:
One tested pair at one Lag and Resolution (or one pair of Place Metrics); the unit that the multiple-testing correction counts.
_Avoid_: Candidate, test, comparison

**Search**:
An exhaustive run that tests every Hypothesis across all available Series or Place Metrics and corrects for the total number tested.
_Avoid_: Scan, sweep, run

**Real Correlation**:
A Correlation that survives the Search's false-discovery-rate correction on Adjusted Series and is stable over time; it asserts "not a coincidence statistically", not causation.
_Avoid_: Significant correlation, true correlation, causal link

**Coincidence**:
A Correlation that is strong in Raw form or passes a naive test but fails the Real Correlation criteria.
_Avoid_: Spurious correlation, false positive

**Stability**:
Whether a Correlation keeps its sign and significance when computed on each half of the history and in rolling windows; a Series needs a minimum number of buckets before Stability can be judged.
_Avoid_: Robustness, consistency

**Too Little Data**:
The verdict for a pair whose Series are too short to judge Stability; never counted as real.
_Avoid_: Insufficient data, inconclusive

**Derived Pair**:
Two Series that are mechanically related by construction (e.g. a district's median and its city's median); excluded from the Hypothesis count.
_Avoid_: Redundant pair, trivial correlation

**Snapshot**:
The stored result of one Search, including its Hypothesis count and parameters, so changes in what is real can be traced over time.
_Avoid_: Report, run result

**Review State**:
The human judgement on a pair: unreviewed, Confirmed Link, or dismissed; stored independently of Snapshots so it survives re-runs.
_Avoid_: Label, status, tag

**Confirmed Link**:
A human-set label on a pair meaning "a plausible mechanism exists"; never computed.
_Avoid_: Causal link, verified correlation
