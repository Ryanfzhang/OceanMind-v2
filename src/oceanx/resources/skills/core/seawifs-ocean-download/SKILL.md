---
name: seawifs-ocean-download
description: Locate and acquire historical SeaWiFS ocean-colour files while checking mission dates, product level, reprocessing version and temporal comparability.
metadata:
  origin: oceanmind
  roles:
    - data_reproducibility_expert
    - ocean_process_expert
---

# SeaWiFS ocean colour

SeaWiFS operated in 1997–2010. It cannot provide observations concurrent with
2011–2022 CMOMS output. Use another sensor for contemporaneous comparison, or
explicitly label a cross-period climatology comparison; let the research question
and evidence determine that choice.

Find the collection through NASA Ocean Color / Earthdata CMR, verify its dates,
L2/L3 level, variable, temporal aggregation and processing version, then inspect
the actual granule links with Python through `ocean_expert_run_code`.
For example, the public CMR collections JSON search accepts a keyword and bounded
page_size; use the returned collection concept ID to search granules with an
explicit temporal interval. Page through results deliberately rather than assuming
the first page is the entire time series.

Use a Python HTTP client or installed `earthaccess` to download verified data links.
Store reusable files in `OCEAN_WORK_DIR/downloads`, with timeouts, bounded retries
and checks for partial transfers. Report missing authentication setup; do not assume
a backend login is inherited. A spatial search does not imply cropped bytes. Never invent
granule filenames or replace an unavailable SeaWiFS period with another year.

After transfer, check file format and readable variables. Older HDF and newer
NetCDF products may require different local readers; report a missing dependency
instead of claiming support merely because the suffix is familiar.

Sources:
- https://oceandata.sci.gsfc.nasa.gov/l3/
- https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html
- https://earth.gsfc.nasa.gov/ocean/missions/seawifs-sea-viewing-wide-field-view-sensor

Save only validated reusable acquisition lessons through `ocean_save_experience`;
omit credentials, one-off URLs and private task information.
