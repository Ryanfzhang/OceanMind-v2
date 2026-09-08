# Idea tasks and related work — Q25–Q30

Draft selection, checked 7 September 2026. The selection prioritizes regional/data relevance and stimulating methods, mechanisms or contrasting findings; publication recency alone is insufficient. Shared datasets are preferred, and exceptions are explicit. Each task has 2–3 papers (14 unique papers overall), not a fixed three-paper quota. Primary publisher records/methods were screened; complete frozen full-text packs and input files have not yet been staged or validated.

## Data alignment comes first

| Task | Focus | Related work | Alignment and remaining limitation |
|---|---|---|---|
| [Q25](#q25) | vertical chlorophyll structure | [Lu et al. (2023)](https://doi.org/10.1029/2022JC019225); [Xu et al. (2023)](https://doi.org/10.1029/2023JG007648) | Same South China Sea physical-biogeochemical setting; Lu uses CMOMS, Xu uses MITgcm-Darwin, not the same model/run or years. I1 remains the proposed 2018–2022 CMOMS archive. |
| [Q26](#q26) | shelf–slope physical–ecosystem coupling | [Lu et al. (2020)](https://doi.org/10.1016/j.pocean.2020.102308); [Lu et al. (2023)](https://doi.org/10.1029/2022JC019225); [Fang et al. (2026; online 2025)](https://doi.org/10.1002/lol2.70075) | Two readings share the South China Sea CMOMS framework; the newer northern-shelf study uses different observations and idealized experiments. All relate to the region, but neither files nor years are identical to I1. |
| [Q27](#q27) | coastal oxygen variability | [Li et al. (2021)](https://doi.org/10.1029/2020JC016700); [Chen et al. (2026)](https://doi.org/10.1029/2025GL118058) | Same estuary and explicitly linked model lineage, but different process experiments; neither is proven identical to the historical CMOMS archive. This is the least exact data match and remains provisional. |
| [Q28](#q28) | inference from incomplete ocean-colour records | [Ye et al. (2024)](https://doi.org/10.5194/essd-16-3125-2024); [Fang et al. (2025)](https://doi.org/10.3389/fmars.2025.1528921) | Explicit same chlorophyll dataset and years; Fang also uses additional HYCOM/ERA5 inputs not included in this bounded task. |
| [Q29](#q29) | the evolving structure of a Loop Current eddy | [Meunier et al. (2018)](https://doi.org/10.1029/2018JC013801); [Sosa-Gutierrez et al. (2020)](https://doi.org/10.1029/2019JC015397); [Marquez-Artavia et al. (2024)](https://doi.org/10.1029/2023JC020837) | Same Poseidon2016–2017 observation program; not every paper uses identical missions, cutoffs or sensors. |
| [Q30](#q30) | surface information and subsurface hydrography | [Miranda et al. (2025)](https://doi.org/10.1016/j.ocemod.2025.102550); [Thirion et al. (2024)](https://doi.org/10.1029/2023JC019764); [Hamilton et al. (2018)](https://doi.org/10.1175/JPO-D-17-0205.1) | Same Gulf region and profile-plus-altimetry data family, not identical releases, years or float/cycle selections. |

Q25 and Q26 share I1 and a CMOMS anchor but now have distinct reading combinations. Q25 adds MITgcm-Darwin subsurface analysis; Q26 adds a newer northern-shelf response study with different observations/experiments. These are explicit same-region context papers, not claims of identical input data. Q27's process-model/historical-archive mismatch and Q30's differing sample years remain provisional. Report shared data and references when interpreting aggregate results.

## Common evaluation

See the [English 14-item rubric](../evaluation/IDEA_RUBRIC.md). Each item has concrete 0–4 anchors, totaling 100 weighted points. A negative or inconclusive result can earn full credit with adequate testing. There is no unique target hypothesis or prescribed result. Assess scientific value and the explained extension, comparison, synthesis or boundary test, not worldwide first discovery. A related idea already studied elsewhere is not automatically penalized. Mere restatement of a supplied conclusion is insufficient; changing years alone is not a scientific rationale. No predetermined example hypothesis is a target answer. Independent evidence checking is still required; these anchors do not make an LLM an automatic scientific oracle.

## Input packages

- **I1 (Q25–Q27):** proposed 2018–2022 daily CMOMS, all supplied physical depths, with temperature, salinity, u, v, oxygen and chlorophyll plus geometry/masks. This historical window is not claimed to reproduce the papers' experiments. Authorization and file validation remain pending. Q25/Q26 need only the five non-oxygen fields.
- **I2 (Q28):** South China Sea 2013–2017 [Ye author reconstruction](https://doi.org/10.5281/zenodo.10478524), plus its underlying [MODIS observations](https://oceancolor.gsfc.nasa.gov/) and validity metadata. Freeze the common footprint, native sampling, releases and remapping. This replaces the earlier monthly 2003–2012 proposal; the complete matched acquisition is not implemented in the current downloader. HYCOM/ERA5 predictors used by Fang are not required inputs. Reconstruction is not independent observational truth.
- **G2/G3 (Q29):** authorized Poseidon temperature/salinity profiles and matched altimetry, 6 August 2016–25 July 2017. The papers overlap in campaign but differ in mission cutoffs and sensors. The newer paper's bio-optical data are not silently added: ChlaPoseidon requires provider approval and restricts third-party redistribution.
- **G1/G2 (Q30):** Gulf float profiles and matched altimetry, July 2011–August 2015. Later papers provide related work, not an assertion of identical years/products. No pretrained estimator or extra SST data are assumed.

“Non-CMOMS” does not imply unrestricted redistribution. Stage only authorized materials, and check permissions before sending data or derived evidence to external evaluators. Both agents must receive identical frozen source texts and data. Do not expose evaluator checklists or selection notes in agent workspaces.

<a id="q25"></a>

## Q25 — An idea about vertical chlorophyll structure

**English query** ([canonical task file](../tasks/Q25/task_info.json)):

Read the supplied related work on vertical chlorophyll structure and physical-biogeochemical coupling in the South China Sea. Using the supplied 2018-2022 CMOMS archive, identify one research opportunity, propose a specific falsifiable hypothesis, and execute a test. Choose the hypothesis yourself; it may concern spatial or temporal structure, a boundary condition, or the information carried by different observations. Explain what your idea adds to the supplied studies and report whether the data support, contradict or cannot resolve it. Before the main test, record the primary hypothesis, operational variables, comparison and what would count against it; label subsequent revisions or exploratory analyses. Use an appropriate baseline/control, address a plausible competing explanation, quantify uncertainty with the sampling dependence considered, and perform a relevant robustness or independent-validation check. Provide reproducible analysis code, supporting numerical/visual evidence and a conclusion whose scope matches the evidence. Explain the scientific value of your question and its connection to the readings; worldwide novelty is not required, and a negative or inconclusive result is not a failure. Use only the authorized input package for the primary test; log any additional literature retrieval.

**Inputs:** I1; 2018–2022; chlorophyll, temp, salt, u, v.

**Selected related work**

- **Lu et al. (2023).** [A Modeling Study of Nutrient Transport and Dynamics Over the Northern Slope of the South China Sea](https://doi.org/10.1029/2022JC019225). Journal of Geophysical Research: Oceans. DOI: 10.1029/2022JC019225.
  - Source data: CMOMS/ROMS nitrogen-phosphorus biological module; northern South China Sea slope; Methods identifies the model framework and links the Lu2020 validation.
  - Role: Shares the CMOMS South China Sea framework and focuses on northern-slope transport and ecosystem structure.
  - Limitation: Same model system does not establish byte-identical output or forcing; the historical-window test is an explicit transfer. No unprovided nutrient flux is compulsory.
  - Verification: [primary record](https://agupubs.onlinelibrary.wiley.com/doi/abs/10.1029/2022JC019225); primary methods or existing verified benchmark record. Full-text pack still pending.
- **Xu et al. (2023).** [Mesoscale Eddy Modulation of Subsurface Chlorophyll Maximum Layers in the South China Sea](https://doi.org/10.1029/2023JG007648). Journal of Geophysical Research: Biogeosciences. DOI: 10.1029/2023JG007648.
  - Source data: South China Sea MITgcm-Darwin output, December 1993–December 2015, 3-day means; northern SCS BGC-Argo validation in 2014–2015. This is not CMOMS.
  - Role: Adds a structure-resolving perspective on subsurface chlorophyll rather than a surface-only ecosystem measure.
  - Limitation: Different model and years. Plankton types, SSH, PAR and nutrients in this paper are not added to I1; no eddy-composite reproduction is required.
  - Verification: [primary record](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2023JG007648); publisher methods sections 2 1 to 2 5. Full-text pack still pending.

**Selection rationale:** Combine a CMOMS regional-process anchor with a subsurface-structure perspective. Prefer an informative regional contrast over a duplicate transport reading pair; do not prescribe a particular hypothesis or require the added paper's data.

**Why this can inspire research (organizer only):** Regional transport context paired with vertically resolved ecosystem analysis. This is not a target hypothesis or a required finding.

**Data alignment:** Same South China Sea physical-biogeochemical setting; Lu uses CMOMS, Xu uses MITgcm-Darwin, not the same model/run or years. I1 remains the proposed 2018–2022 CMOMS archive.

**Evaluator scope:** Differentiate chlorophyll concentration, depth-integrated chlorophyll, biomass and productivity. Respect vertical resolution, varying integration support and temporal/spatial dependence. Do not require nutrient/light/organic-matter flux variables absent from I1. The MITgcm-Darwin reading is conceptual/methodological context, not an additional input package; no mandatory SSH-based eddy tracking.

[Per-query checklist and full score anchors](../tasks/Q25/target_study/checklist.json).

<a id="q26"></a>

## Q26 — An idea about shelf–slope physical–ecosystem coupling

**English query** ([canonical task file](../tasks/Q26/task_info.json)):

Read the supplied related work using the CMOMS physical-biogeochemical framework in the South China Sea. Using the supplied 2018-2022 CMOMS fields over the northern shelf and slope, identify one unresolved question about the relation between circulation and ecosystem variability. Propose a specific falsifiable hypothesis and test it using the available variables. Choose the relationship and comparison yourself, explain what goes beyond repeating a supplied result, and distinguish the evidence from alternative interpretations. Before the main test, record the primary hypothesis, operational variables, comparison and what would count against it; label subsequent revisions or exploratory analyses. Use an appropriate baseline/control, address a plausible competing explanation, quantify uncertainty with the sampling dependence considered, and perform a relevant robustness or independent-validation check. Provide reproducible analysis code, supporting numerical/visual evidence and a conclusion whose scope matches the evidence. Explain the scientific value of your question and its connection to the readings; worldwide novelty is not required, and a negative or inconclusive result is not a failure. Use only the authorized input package for the primary test; log any additional literature retrieval.

**Inputs:** I1; 2018–2022; chlorophyll, temp, salt, u, v.

**Selected related work**

- **Lu et al. (2020).** [Nutrient transport and dynamics in the South China Sea: A modeling study](https://doi.org/10.1016/j.pocean.2020.102308). Progress in Oceanography. DOI: 10.1016/j.pocean.2020.102308.
  - Source data: CMOMS South China Sea coupled physical-biogeochemical experiment; climatological final model years, not dated historical files.
  - Role: Provides a CMOMS physical-biogeochemical context for South China Sea chlorophyll and transport.
  - Limitation: The climatological experiment is not identical to the proposed 2018-2022 historical archive. Nutrient budgets require extra fields not supplied here.
  - Verification: [primary record](https://doi.org/10.1016/j.pocean.2020.102308); primary methods or existing verified benchmark record. Full-text pack still pending.
- **Lu et al. (2023).** [A Modeling Study of Nutrient Transport and Dynamics Over the Northern Slope of the South China Sea](https://doi.org/10.1029/2022JC019225). Journal of Geophysical Research: Oceans. DOI: 10.1029/2022JC019225.
  - Source data: CMOMS/ROMS nitrogen-phosphorus biological module; northern South China Sea slope; Methods identifies the model framework and links the Lu2020 validation.
  - Role: Shares the CMOMS South China Sea framework and focuses on northern-slope transport and ecosystem structure.
  - Limitation: Same model system does not establish byte-identical output or forcing; the historical-window test is an explicit transfer. No unprovided nutrient flux is compulsory.
  - Verification: [primary record](https://agupubs.onlinelibrary.wiley.com/doi/abs/10.1029/2022JC019225); primary methods or existing verified benchmark record. Full-text pack still pending.
- **Fang et al. (2026; online 2025).** [Typhoon-induced surface chlorophyll a decline on the shelf of the South China Sea](https://doi.org/10.1002/lol2.70075). Limnology and Oceanography Letters. DOI: 10.1002/lol2.70075.
  - Source data: Northern SCS shelf; OC-CCI v6.0, 4 km/8-day chlorophyll and IBTrACS tracks for 2001–2023; 2016 cruise context and idealized Nida-based experiments. Same CMOMS model identity is not established.
  - Role: Provides a counterexample to uniform positive ecosystem responses, connecting shelf context, transport and vertical structure.
  - Limitation: These observations and idealized experiments are not I1. No typhoon tracks, winds, nutrient fields or budget terms are added; event attribution requires inputs absent from I1.
  - Verification: [primary record](https://aslopubs.onlinelibrary.wiley.com/doi/10.1002/lol2.70075); publisher abstract data and methods. Full-text pack still pending.

**Selection rationale:** Keep the same-model transport foundation and add a recent regional counterexample that can inspire questions about context-dependent responses. The agent chooses its own feasible question, not a required typhoon experiment.

**Why this can inspire research (organizer only):** Regional transport foundations paired with a context-dependent ecological response. This is not a target hypothesis or a required finding.

**Data alignment:** Two readings share the South China Sea CMOMS framework; the newer northern-shelf study uses different observations and idealized experiments. All relate to the region, but neither files nor years are identical to I1.

**Evaluator scope:** Restrict the primary test to supplied chlorophyll, temperature, salinity and velocity support. An association with transport does not measure nutrient supply or biological production directly. No nutrient budget or a particular mechanism/sign is prescribed. No typhoon attribution or full process budget is required; I1 does not include verified storm tracks, winds or budget diagnostics.

[Per-query checklist and full score anchors](../tasks/Q26/target_study/checklist.json).

<a id="q27"></a>

## Q27 — An idea about coastal oxygen variability

**English query** ([canonical task file](../tasks/Q27/task_info.json)):

Read the supplied related work on coastal oxygen dynamics and benthic-pelagic coupling. Using the supplied 2018-2022 CMOMS archive, propose and test one data-supported hypothesis about coastal oxygen variability or hypoxia. You may investigate an explanatory relationship, a boundary condition or predictive information, but the choice of idea is yours. State what the available variables can and cannot establish, compare against a relevant alternative, and report the outcome without assuming that the hypothesis must be confirmed. Before the main test, record the primary hypothesis, operational variables, comparison and what would count against it; label subsequent revisions or exploratory analyses. Use an appropriate baseline/control, address a plausible competing explanation, quantify uncertainty with the sampling dependence considered, and perform a relevant robustness or independent-validation check. Provide reproducible analysis code, supporting numerical/visual evidence and a conclusion whose scope matches the evidence. Explain the scientific value of your question and its connection to the readings; worldwide novelty is not required, and a negative or inconclusive result is not a failure. Use only the authorized input package for the primary test; log any additional literature retrieval.

**Inputs:** I1; 2018–2022; oxygen, chlorophyll, temp, salt, u, v.

**Selected related work**

- **Li et al. (2021).** [Spatiotemporal Development and Dissipation of Hypoxia Induced by Variable Wind-Driven Shelf Circulation off the Pearl River Estuary: Observational and Modeling Studies](https://doi.org/10.1029/2020JC016700). Journal of Geophysical Research: Oceans. DOI: 10.1029/2020JC016700.
  - Source data: Pearl River Estuary/coastal transition zone; field observations and ROMS physical-biological process experiments.
  - Role: Supplies the regional coupled-model lineage used by the newer sediment study.
  - Limitation: Process experiments and finer estuary geometry are not identical to a CMOMS basin hindcast; no claim of identical files.
  - Verification: [primary record](https://agupubs.onlinelibrary.wiley.com/doi/abs/10.1029/2020JC016700); primary methods or existing verified benchmark record. Full-text pack still pending.
- **Chen et al. (2026).** [Sediment Legacy Organic Matter Amplifies Nutrient-Driven Coastal Hypoxia: A Coupled Benthic-Pelagic Modeling Study](https://doi.org/10.1029/2025GL118058). Geophysical Research Letters. DOI: 10.1029/2025GL118058.
  - Source data: Pearl River Estuary ROMS physical-biogeochemical model building on Li2021; new sediment module;45-day idealized summer experiments and2017/2021 field constraints.
  - Role: Adds a recent benthic-pelagic perspective on persistent organic-matter effects in coastal hypoxia.
  - Limitation: Reading this work does not authorize claiming historical CMOMS output reproduces its sediment experiments.
  - Verification: [primary record](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2025GL118058); publisher abstract. Full-text pack still pending.

**Selection rationale:** Use two Pearl River Estuary studies with explicitly linked ROMS physical-biological model lineage. The newer paper identifies the older model as its foundation; do not pad the group with unrelated sediment observations.

**Why this can inspire research (organizer only):** Circulation-driven oxygen variability paired with a newer benthic-pelagic legacy mechanism; the mechanism is reading context, not a prescribed causal claim. This is not a target hypothesis or a required finding.

**Data alignment:** Same estuary and explicitly linked model lineage, but different process experiments; neither is proven identical to the historical CMOMS archive. This is the least exact data match and remains provisional.

**Evaluator scope:** Verify oxygen units and geometric bottom indices where used. Distinguish continuous oxygen variability from threshold-event claims. Predictor timing and a persistence baseline matter if forecasting is claimed; they are not mandatory for a non-predictive hypothesis. Sediment/respiration causal attribution requires evidence not present in the six-field archive.

[Per-query checklist and full score anchors](../tasks/Q27/target_study/checklist.json).

<a id="q28"></a>

## Q28 — An idea about inference from incomplete ocean-colour records

**English query** ([canonical task](../tasks/Q28/task_info.json)):

Read the supplied South China Sea ocean-colour related work. Using only the public monthly MODIS-Aqua chlorophyll fields and their native valid-pixel masks for 2013-2017 (104-122 E, 1-25 N), propose and test one falsifiable idea about regional seasonal variability or the sensitivity of inference to incomplete spatial coverage. No author reconstruction or daily predictor dataset is supplied or required. If testing reconstruction, mask only originally valid observations, keep held-out values out of fitting and state that artificial missingness is not independent validation of real cloud gaps. Do not infer submonthly events from monthly means. Before the main test, record the primary hypothesis, operational variables, comparison and what would count against it; label subsequent revisions or exploratory analyses. Use an appropriate baseline/control, address a plausible competing explanation, quantify uncertainty with the sampling dependence considered, and perform a relevant robustness or independent-validation check. Provide reproducible analysis code, supporting numerical/visual evidence and a conclusion whose scope matches the evidence. Explain the scientific value of your question and its connection to the readings; worldwide novelty is not required, and a negative or inconclusive result is not a failure. Use only the authorized input package for the primary test; log any additional literature retrieval.

**Inputs:** P_MODIS; 2013–2017; chlor_a, native valid-pixel masks, coordinates. Fully specified in the unified download manifest, not the original authors' observational/reconstruction archive.

**Selected related work**

- **Ye et al. (2024).** [A daily reconstructed chlorophyll-a dataset in the South China Sea from MODIS using OI-SwinUnet](https://doi.org/10.5194/essd-16-3125-2024). DOI: 10.5194/essd-16-3125-2024.
  - Paper's original data: Daily South China Sea MODIS-derived reconstructed chlorophyll2013–2017; author product DOI10.5281/zenodo.10478524.
  - Verification: [primary source](https://essd.copernicus.org/articles/16/3125/2024/). Authorized full text still needs staging; no extra paper dataset is required.
- **Fang et al. (2025).** [Leveraging ResUnet, oceanic and atmospheric data for accurate chlorophyll-a estimations in the South China Sea](https://doi.org/10.3389/fmars.2025.1528921). DOI: 10.3389/fmars.2025.1528921.
  - Paper's original data: Ye2024 daily reconstructed South China Sea chlorophyll, 1 January2013–31 December2017; the paper additionally uses HYCOM and ERA5.
  - Verification: [primary source](https://www.frontiersin.org/journals/marine-science/articles/10.3389/fmars.2025.1528921/full). Authorized full text still needs staging; no extra paper dataset is required.

**Current data alignment and limit:** The readings discuss daily reconstruction; this reduced task uses independent monthly MODIS inputs for a methodological/seasonal hypothesis, not the shared author target or an exact replication.

**Evaluator scope:** The readings discuss daily reconstruction; this reduced task uses independent monthly MODIS inputs for a methodological/seasonal hypothesis, not the shared author target or an exact replication. Use only the canonical public group; no private observational archive or author reconstruction is compulsory. Uncertainty/control and blocked held-out testing must match the chosen hypothesis and actual sampling.

[Per-query 14-item checklist](../tasks/Q28/target_study/checklist.json).

<a id="q29"></a>

## Q29 — An idea about the evolving structure of a Loop Current eddy

**English query** ([canonical task](../tasks/Q29/task_info.json)):

Read the supplied Loop Current eddy related work. Using only public GLORYS temperature, salinity and sea-surface height for 6 August 2016-25 July 2017 in the Gulf of Mexico (98-82 W, 18-30 N), propose and test one falsifiable idea about the vertical structure or evolution of a resolved anticyclonic feature. Choose a feasible feature/contrast from the fields instead of assuming GLORYS contains the observed Poseidon trajectory. Original glider missions and bio-optical data are not supplied or required. Separate model-internal evidence from independent observational validation. Before the main test, record the primary hypothesis, operational variables, comparison and what would count against it; label subsequent revisions or exploratory analyses. Use an appropriate baseline/control, address a plausible competing explanation, quantify uncertainty with the sampling dependence considered, and perform a relevant robustness or independent-validation check. Provide reproducible analysis code, supporting numerical/visual evidence and a conclusion whose scope matches the evidence. Explain the scientific value of your question and its connection to the readings; worldwide novelty is not required, and a negative or inconclusive result is not a failure. Use only the authorized input package for the primary test; log any additional literature retrieval.

**Inputs:** P_GULF; 6 August 2016–25 July 2017; thetao, so, zos, coordinates/depth. Fully specified in the unified download manifest, not the original authors' observational/reconstruction archive.

**Selected related work**

- **Meunier et al. (2018).** [The Vertical Structure of a Loop Current Eddy](https://doi.org/10.1029/2018JC013801). DOI: 10.1029/2018JC013801.
  - Paper's original data: GMOG Poseidon2016 glider mission0003, 5 August–15 November2016, plus contextual observations.
  - Verification: [primary source](https://doi.org/10.1029/2018JC013801). Authorized full text still needs staging; no extra paper dataset is required.
- **Sosa-Gutierrez et al. (2020).** [Erosion of the Subsurface Salinity Maximum of the Loop Current Eddies From Glider Observations and a Numerical Model](https://doi.org/10.1029/2019JC015397). DOI: 10.1029/2019JC015397.
  - Paper's original data: GMOG Poseidon2016–2017 glider observations (6 August2016–25 July2017 analysis) plus a separate numerical model.
  - Verification: [primary source](https://doi.org/10.1029/2019JC015397). Authorized full text still needs staging; no extra paper dataset is required.
- **Marquez-Artavia et al. (2024).** [On the Seasonal Cycle of Phytoplankton Bio-Optical Properties Inside a Warm Core Ring in the Gulf of Mexico](https://doi.org/10.1029/2023JC020837). DOI: 10.1029/2023JC020837.
  - Paper's original data: GMOG Poseidon missions0003–0006, August2016–August2017; physical and bio-optical sensors; study windows differ across papers.
  - Verification: [primary source](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2023JC020837). Authorized full text still needs staging; no extra paper dataset is required.

**Current data alignment and limit:** Related work concerns the same region but observed missions are not identical to this reanalysis. The experiment tests a model-resolved feature, not reproduction of the original Poseidon sample.

**Evaluator scope:** Related work concerns the same region but observed missions are not identical to this reanalysis. The experiment tests a model-resolved feature, not reproduction of the original Poseidon sample. Use only the canonical public group; no private observational archive or author reconstruction is compulsory. Uncertainty/control and blocked held-out testing must match the chosen hypothesis and actual sampling.

[Per-query 14-item checklist](../tasks/Q29/target_study/checklist.json).

<a id="q30"></a>

## Q30 — An idea about surface information and subsurface hydrography

**English query** ([canonical task](../tasks/Q30/task_info.json)):

Read the supplied related work on Gulf of Mexico hydrography and sea-level structure. Using only co-located public GLORYS temperature, salinity and sea-surface height for July 2011-August 2015 (98-82 W, 18-30 N), propose and test one falsifiable idea about information in sea level for upper-ocean or thermocline structure. Choose the response and comparator yourself. If predicting subsurface properties, use blocked time or space evaluation and a meaningful climatological baseline; model-internal held-out performance is not independent observational validation. No original float cycles or additional satellite archive is required. Before the main test, record the primary hypothesis, operational variables, comparison and what would count against it; label subsequent revisions or exploratory analyses. Use an appropriate baseline/control, address a plausible competing explanation, quantify uncertainty with the sampling dependence considered, and perform a relevant robustness or independent-validation check. Provide reproducible analysis code, supporting numerical/visual evidence and a conclusion whose scope matches the evidence. Explain the scientific value of your question and its connection to the readings; worldwide novelty is not required, and a negative or inconclusive result is not a failure. Use only the authorized input package for the primary test; log any additional literature retrieval.

**Inputs:** P_GULF; July 2011–August 2015; thetao, so, zos, coordinates/depth. Fully specified in the unified download manifest, not the original authors' observational/reconstruction archive.

**Selected related work**

- **Miranda et al. (2025).** [Neural Synthetic Profiles from Remote Sensing and Observations (NeSPReSO) — Reconstructing temperature and salinity fields in the Gulf of Mexico](https://doi.org/10.1016/j.ocemod.2025.102550). DOI: 10.1016/j.ocemod.2025.102550.
  - Paper's original data: Gulf of Mexico Argo profiles, satellite surface data and additional glider evaluation; not the same frozen float/cycle sample as Hamilton2018.
  - Verification: [primary source](https://www.sciencedirect.com/science/article/pii/S1463500325000538). Authorized full text still needs staging; no extra paper dataset is required.
- **Thirion et al. (2024).** [Loop Current Eddies as a Possible Cause of the Rapid Sea Level Rise in the Gulf of Mexico](https://doi.org/10.1029/2023JC019764). DOI: 10.1029/2023JC019764.
  - Paper's original data: Gulf of Mexico altimetry and Argo-derived subsurface products;1993–2020 basin analysis, not a matched short float campaign.
  - Verification: [primary source](https://agupubs.onlinelibrary.wiley.com/doi/full/10.1029/2023JC019764). Authorized full text still needs staging; no extra paper dataset is required.
- **Hamilton et al. (2018).** [Hydrography of the Gulf of Mexico Using Autonomous Floats](https://doi.org/10.1175/JPO-D-17-0205.1). DOI: 10.1175/JPO-D-17-0205.1.
  - Paper's original data: Gulf of Mexico autonomous/APEX and Argo profiles with matched altimetry, July2011–August2015.
  - Verification: [primary source](https://doi.org/10.1175/JPO-D-17-0205.1). Authorized full text still needs staging; no extra paper dataset is required.

**Current data alignment and limit:** The papers' observations are not identical to GLORYS inputs; same-region methodological inspiration only. Do not claim independent validation using variables from one assimilating model.

**Evaluator scope:** The papers' observations are not identical to GLORYS inputs; same-region methodological inspiration only. Do not claim independent validation using variables from one assimilating model. Use only the canonical public group; no private observational archive or author reconstruction is compulsory. Uncertainty/control and blocked held-out testing must match the chosen hypothesis and actual sampling.

[Per-query 14-item checklist](../tasks/Q30/target_study/checklist.json).
