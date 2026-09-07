---
name: modis-ocean-download
description: Acquire MODIS ocean SST or ocean-colour data through documented ERDDAP subsets or NASA Ocean Color files, checking processing level, time aggregation and coverage.
metadata:
  origin: oceanmind
  roles:
    - data_reproducibility_expert
    - ocean_process_expert
    - visualization_communication_expert
---

# MODIS ocean data

Write a Python download script and execute it with `ocean_expert_run_code`;
there is no MODIS-specific API. Consult `ocean-data-acquisition` for transfer
and storage guidance when needed.

## ERDDAP subset route

The earlier OceanMaster download script used NOAA CoastWatch ERDDAP, dataset
`erdMH1sstdmdayR20190SQ`, variable `sstMasked`, for Aqua MODIS monthly SST.
This is a historical example, not a promise that the ID is still available or that
it supplies chlorophyll. Verify the current catalog and processing version first.

1. Inspect the server's `/erddap/search/index.json?page=1&itemsPerPage=10&searchFor=MODIS`
   and then `/erddap/info/{dataset_id}/index.json` (or `.dds` for dimension order).
2. Read required coordinate arrays through `/erddap/griddap/{dataset_id}.json?latitude`
   and analogous longitude/time requests. Keep each response bounded.
3. Select real indices from the metadata; do not assume ascending latitude,
   Unix-second time units, a particular dimension order, or a monthly timestamp
   on the first day. Empty time selections are missing data, not zero fields.
4. A griddap NetCDF request has the form
   `.../{dataset_id}.nc?{variable}[t0:1:t1][y0:1:y1][x0:1:x1]` for a verified
   three-dimensional time/latitude/longitude variable. Extra axes require explicit
   selections. Derive indices, variable names and axis order; do not copy placeholders.
5. Explain the region, period and aggregation in `purpose`, then stream the verified
   URL with a Python HTTP client into `OCEAN_WORK_DIR/downloads`. Use timeouts and
   bounded retries. Inspect the resulting NetCDF before calculating anything.

Official syntax: https://coastwatch.noaa.gov/erddap/griddap/documentation.html

## NASA Ocean Color route

Start at https://oceandata.sci.gsfc.nasa.gov/l3/ or Earthdata's CMR catalog.
Choose Aqua/Terra, SST versus ocean-colour, day/night, L2/L3, resolution,
aggregation and reprocessing version from current metadata. Use CMR's downloadable
data links, not browse imagery or landing-page HTML. Granule search by region does
not crop a granule; disclose full-file volume when no server-side subset exists.

An installed `earthaccess` Python client can search and download granules into the
declared directory; documented HTTP access is another option. For authenticated
products, check that an authorized login is available to the execution environment;
if not, report missing setup. A login-page redirect is not data.
Do not put a token, password or signed URL into tools, code, experience or Skills.

SST quality masks and cloud gaps affect coverage; ocean-colour retrievals also
need coastal/turbidity and valid-pixel checks. Monthly, 8-day and daily products
are not interchangeable. Save a reusable, verified acquisition lesson optionally
with `ocean_save_experience`, not a record of every downloaded file.
