# Adjusted Series: differencing plus per-phase seasonal means, not a moving-average STL

Issue #7 asked for STL-style detrend/deseason. A centred moving-average detrend smears each Series over
neighbouring buckets, so a Lag-scan finds Real Correlations at Lags next to the true one (lag 0 became
Real for a planted lag-3 relationship). Instead the Adjusted Series is first differences (trend) minus the
mean change at each position in the seasonal cycle (seasonality; day: 7 or 365, month: 12), used when both
Series have a gap-free history of at least 3 cycles (`adjustment = detrend_deseason`). Otherwise plain
differences (`adjustment = differenced`).

Known limits: one missing bucket disables deseasoning for the pair; with period 365 and ~3 years each
phase mean rests on 3 points, which the bootstrap p-value does not account for; on the `differenced`
path a strong-Raw Coincidence can only be labelled `trend_driven`.
