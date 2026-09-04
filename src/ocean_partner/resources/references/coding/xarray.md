# Xarray Practices

Inspect dimensions, coordinates, dtypes, chunks, attributes, and missing values before computation. Use named dimensions and coordinate-aware selection where possible. Be explicit about reductions, alignment, broadcasting, lazy evaluation, and when `.load()` or `.compute()` would materialize large arrays. Preserve metadata deliberately rather than assuming every operation retains it.
