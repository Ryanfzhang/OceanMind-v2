---
name: cmems-data-acquisition
description: Retrieve Copernicus Marine products with LLM-written Python scripts using the official Toolbox or documented HTTP access, keeping model/observation provenance explicit.
metadata:
  origin: oceanmind
  roles:
    - data_reproducibility_expert
    - ocean_process_expert
---

# Copernicus Marine acquisition

Choose the dataset from the current Marine Data Store / official documentation,
not from a guessed product ID. Distinguish observations, reanalysis and forecasts;
confirm variable names, native grid, depth, time coverage, frequency and version.

Write Python and run it with `ocean_expert_run_code`. Prefer the installed official
`copernicusmarine` client for supported products. Documented HTTPS original-file or
subset URLs can also be downloaded with a Python HTTP client. Retain product
identity/version and citation in the research result.
Reading a STAC catalog or finding an ARCO Zarr URL is not the same as downloading
a local NetCDF dataset. Never treat a Zarr metadata file as the complete array.

The official Toolbox supports `describe`, `subset` and `get`. A subset can limit
variables, region, time and depth; dry-run can report proposed selection/volume.
Run these methods in-process using the same code tool as analysis. Save to
`OCEAN_WORK_DIR/downloads`, not the original source directory. If the client or
authentication is missing, report the needed setup. Do not invent a `subset`
REST endpoint or silently fetch a full global product instead of a subset.

Official references (check current interfaces before recommending commands):
- https://help.marine.copernicus.eu/en/articles/9235249-how-to-download-a-subset-of-data
- https://toolbox-docs.marine.copernicus.eu/en/stable/python-interface.html

A typical Python workflow inside `ocean_expert_run_code` is
`copernicusmarine.describe(contains=[...])` followed by
`copernicusmarine.subset(dataset_id=..., variables=[...], minimum_longitude=...,
maximum_longitude=..., minimum_latitude=..., maximum_latitude=...,
start_datetime=..., end_datetime=..., dry_run=True)`, then the same verified
selection with `dry_run=False` and explicit `output_directory` under
`OCEAN_WORK_DIR/downloads`. Verify current parameters for the installed version.
Do not embed credentials or assume backend environment/login files are inherited.

Validate the acquired file before use, especially staggered velocities, depth
selection and time averaging. For transports, multiplying daily mean velocity by
daily mean concentration does not recover unresolved subdaily covariance.

Use `ocean_save_experience` only for a reusable verified method or product-access
constraint. Provider requirements belong in Skills; infrastructure bugs should be
reported, not institutionalized as sandbox-bypass recipes.
