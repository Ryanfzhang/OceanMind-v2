# Anomaly Methods

An anomaly is a departure from an explicitly defined reference state, not a synonym for an unusually colored map. Record the baseline period, climatology construction, temporal resolution, leap-day treatment, smoothing, and missing-data policy. For gridded fields, distinguish anomalies calculated before versus after spatial aggregation. Check whether seasonality, trends, and changing sampling make the baseline inappropriate.

When a task specifies an `equal_month_mean`, each selected monthly field has the
same coefficient: use the ordinary arithmetic mean across the selected monthly
samples. Do not silently replace it with a calendar-day, inverse-day, or other
time-length weighting. Record the baseline and target windows independently so
the direction of the anomaly remains reviewable.
