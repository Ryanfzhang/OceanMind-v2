# OceanMind benchmarking

Initial English task catalogue: 30 tasks across three evaluation tracks.

## Status

This is a query draft, not a runnable or validated benchmark release. Data subsets, exact spatial masks, event availability, dataset versions, background papers, reference results, reference figures, and scoring rubrics have not yet been finalized. Dates and regions in the queries preserve the proposed design and must be checked against the selected data before release.

No datasets are downloaded or copied here. No application, execution, or multi-agent behavior is changed by this catalogue.

All preparation now lives in one [product summary and data/query audit](preparation/DATA_PREPARATION.md): the opening Table 0 groups acquisition/export needs by product, time, horizontal/depth range, variables and task, marking unresolved or conditional inputs; Table 1 inventories original-paper inputs and selected benchmark adaptations (B01–B04), including sources, variables, periods and acquisition routes; Table 2 separates each current query's dependencies from the paper's actual analysis. A paper group is not a mandatory whole-package download for every related question. Method matches, adaptations, references needing replacement and open-ended ideas are distinguished. Storage limits do not drive this plan; conflicting dates, sources, methods and sampling remain explicit. The 2026-09-06 revision replaces Q05 with a paper-anchored anomaly calculation, extends Q14/Q24 to 2011–2022, and changes Q23 to historical SeaWiFS/blended-wind EEMD analysis (1997-09–2010-04). Original-paper processing versions are not assumed available. Q14's earlier negative reference audit was corrected after checking Section 4.5.1 / Figure 13.

## Layout

```text
benchmarking/
├── README.md
├── download/                    # Standalone Linux downloader; no runtime changes
│   ├── download_data.py         # Preview, subset transfer, validation and resume
│   ├── download_config.json     # Three public products; explicit pending sources
│   ├── README.md                # Server commands and coverage limitations
│   ├── requirements.txt
│   └── test_download_data.py     # Offline regression tests
├── preparation/                 # Maintainer-only
│   └── DATA_PREPARATION.md       # Product summary, two evidence tables
└── tasks/
    ├── Q01/task_info.json        # Canonical English query
    ├── Q02/task_info.json
    └── ... Q30/task_info.json
```

Each task file is the canonical source for its English query:

- `id`: stable task identifier, Q01–Q30.
- `track`: evaluation category.
- `title`: English task title.
- `query`: complete English prompt, including the agreed track-wide instructions where applicable.
- `data_status`: `pending_subset_selection` until the data subset is agreed.

These files are a task catalogue, not a batch-runner input contract. A runnable manifest can be prepared after dataset paths and execution settings are finalized.

The [server download guide](download/README.md) provides a separate metadata-preview and download workflow for monthly MODIS, SeaWiFS and blended winds. It requires an explicit MODIS bounding box, inventories actual missing months and retains provenance/checksums. Other non-CMOMS products remain explicitly pending where product IDs, sample selections or author access are unresolved. Downloading a rectangle does not freeze the scientific mask or make the entire benchmark ready.

## Task index

| Track | IDs | Count |
| --- | --- | ---: |
| Data analysis correctness | Q01–Q06 | 6 |
| Autoresearch | Q07–Q24 | 18 |
| Idea generation and hypothesis testing | Q25–Q30 | 6 |
| Total | Q01–Q30 | 30 |

### Data analysis correctness

| ID | Task |
| --- | --- |
| Q01 | [Area-weighted seasonal chlorophyll](tasks/Q01/task_info.json) |
| Q02 | [Subsurface chlorophyll maximum](tasks/Q02/task_info.json) |
| Q03 | [Mixed-layer depth from temperature and salinity](tasks/Q03/task_info.json) |
| Q04 | [Subsurface salinity maximum](tasks/Q04/task_info.json) |
| Q05 | [Interannual chlorophyll anomalies near the Pearl River](tasks/Q05/task_info.json) |
| Q06 | [Bottom hypoxia exposure](tasks/Q06/task_info.json) |

### Autoresearch

| ID | Task |
| --- | --- |
| Q07 | [Spatial organization of subsurface chlorophyll](tasks/Q07/task_info.json) |
| Q08 | [Distinct chlorophyll hotspots](tasks/Q08/task_info.json) |
| Q09 | [Pearl River plume variability](tasks/Q09/task_info.json) |
| Q10 | [Spatially intensified coastal upwelling](tasks/Q10/task_info.json) |
| Q11 | [Hypoxia geography and persistence](tasks/Q11/task_info.json) |
| Q12 | [Wind transitions and oxygen recovery](tasks/Q12/task_info.json) |
| Q13 | [Oxygen-budget diagnosis](tasks/Q13/task_info.json) |
| Q14 | [Climatological nitrate dynamics over the shelf](tasks/Q14/task_info.json) |
| Q15 | [Gulf of Mexico water-mass structure](tasks/Q15/task_info.json) |
| Q16 | [Vertical structure of a warm-core eddy](tasks/Q16/task_info.json) |
| Q17 | [Evolution of the subsurface salinity core](tasks/Q17/task_info.json) |
| Q18 | [Seasonal upper-ocean stratification](tasks/Q18/task_info.json) |
| Q19 | [Eddy detachment and westward propagation](tasks/Q19/task_info.json) |
| Q20 | [Sea level as a subsurface indicator](tasks/Q20/task_info.json) |
| Q21 | [Regional chlorophyll seasonality](tasks/Q21/task_info.json) |
| Q22 | [Coastal and offshore bloom timing](tasks/Q22/task_info.json) |
| Q23 | [Wind–chlorophyll variability across time scales](tasks/Q23/task_info.json) |
| Q24 | [Climatological model–satellite chlorophyll consistency](tasks/Q24/task_info.json) |

### Idea generation and hypothesis testing

| ID | Task |
| --- | --- |
| Q25 | [Can surface chlorophyll represent the water column?](tasks/Q25/task_info.json) |
| Q26 | [A testable explanation for plume chlorophyll variability](tasks/Q26/task_info.json) |
| Q27 | [A precursor of bottom-water hypoxia](tasks/Q27/task_info.json) |
| Q28 | [Robustness of a satellite-derived ecological conclusion](tasks/Q28/task_info.json) |
| Q29 | [What controls changes in a salinity core?](tasks/Q29/task_info.json) |
| Q30 | [Improving a surface-based subsurface estimate](tasks/Q30/task_info.json) |

## Next preparation step

Resolve the strict query-to-paper audit before downloading: either reproduce an identified paper analysis using its actual data/case or explicitly retain an adaptation with newly computed reference results. Record the selected inputs' provider, product/version, variables, units, time range, spatial bounds, vertical coverage, resolution, masks, grid metadata and local path. Check event coverage and diagnostic definitions before freezing the affected tasks. Do not add unrelated whole-paper datasets merely because a task cites that paper.

Keep permitted background papers separate from evaluator-only target papers, reference outputs, and scoring rubrics. Derive numerical reference results from the actual supplied subset rather than treating published values from a different period or dataset as ground truth. A rejected hypothesis can still represent a successful scientific test.

The preparation directory is not agent input. Future runners must expose only the selected query, approved data/metadata and permitted background papers, not this entire directory tree. No runtime isolation or automatic download integration is implemented here.

Each evaluated task should start independently; shared data must not imply shared answers or cross-task experience updates. Self-evolving evaluation is outside the scope of this catalogue.
