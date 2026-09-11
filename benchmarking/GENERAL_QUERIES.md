# General research queries and skill coverage

Q13–Q30 use `query_version: 2026-09-11-general-v1`. Q01–Q06 retain their precise calculation contracts and standard-answer design; Q07–Q12 are unchanged.

The agent-facing question specifies data, region, time and the scientific objective. It does not prescribe thresholds, diagnostics, baselines, robustness procedures, figure counts, or a notebook. Q25–Q30 retain the requirement to develop an idea informed by the supplied related work. Methods and scientific quality remain the agent's responsibility.

`catalog_version` and download manifests retain their existing values because the input archive has not changed. The new `query_version` identifies the prompt revision separately. Query hashes and draft evaluator references have been updated. Q13–Q24 criteria now accept defensible method choices and treat every original-paper panel as context only. Q25–Q30 retain their idea-quality anchors as evaluator-only expectations. The rubrics remain drafts pending calibration; this edit does not create validated reference answers.

## Current questions in Chinese

| ID | General query |
|---|---|
| Q13 | 使用 2011 年 7 月至 2015 年 8 月墨西哥湾（98–82°W，18–30°N）的 GLORYS12 数据，研究上层海洋盐度结构及其区域差异。 |
| Q14 | 使用同一时空范围的 GLORYS12 数据，研究混合层的季节演变及其与层化的关系。 |
| Q15 | 使用同一时空范围的 GLORYS12 数据，研究海面高度能反映哪些深层温跃层信息。 |
| Q16 | 使用 2016 年 8 月 5 日至 11 月 15 日墨西哥湾（98–82°W，18–30°N）的 GLORYS12 数据，研究套流区域反气旋涡的温盐垂向结构。 |
| Q17 | 使用 2016 年 8 月 6 日至 2017 年 7 月 25 日墨西哥湾中部（92–88°W，23–27°N）的 GLORYS12 数据，研究次表层盐度结构的季节演变及其与上层混合的关系。 |
| Q18 | 使用 2015 年 7 月至 2016 年 11 月墨西哥湾（98–82°W，18–30°N）的 GLORYS12 数据，研究套流区域反气旋涡的分离及后续演变。 |
| Q19 | 使用 2003–2012 年 MODIS-Aqua 数据，研究南海沿岸（112–115°E，21–23°N）与外海（115–118°E，17–20°N）叶绿素季节性的差异。 |
| Q20 | 使用 2003–2012 年南海（104–122°E，1–25°N）的 MODIS-Aqua 和 NOAA 融合风场数据，研究区域叶绿素变化与风速的关系。 |
| Q21 | 使用 2003–2012 年 MODIS-Aqua 数据，研究 SEATS 区域（115.5–116.5°E，17.5–18.5°N）与珠江沿岸（112–115°E，21–23°N）叶绿素变化的差异。 |
| Q22 | 使用 2004–2012 年 5–9 月越南近海（109–112°E，10–16°N）的 MODIS-Aqua 和 NOAA 融合风场数据，研究夏季叶绿素变化与季风的关系。 |
| Q23 | 使用 1982–2023 年东海（25–34°N，120–128°E）的逐日海温数据，研究 2023 年海洋热浪的特征及其在历史记录中的异常程度。 |
| Q24 | 使用提供的 OISST、GLORYS12 和 ERA5 数据，研究 2023 年东海（25–34°N，120–128°E）海洋热浪的形成和维持机制。 |
| Q25 | 结合所提供的文献及 2018–2022 年南海北部陆架—陆坡 CMOMS 数据，提出并研究一个关于叶绿素垂向结构及其物理控制因素的想法。 |
| Q26 | 结合相同区域、年份的 CMOMS 数据和所提供的文献，提出并研究一个关于环流与生态系统耦合的想法。 |
| Q27 | 结合所提供的文献及 2018–2022 年南海北部给定沿岸区域的 CMOMS 数据，提出并研究一个关于氧变化或低氧的想法。 |
| Q28 | 结合所提供的文献及 2013–2017 年南海（104–122°E，1–25°N）MODIS-Aqua 数据，提出并研究一个关于不完整海色记录能够揭示哪些区域叶绿素变化信息的想法。 |
| Q29 | 结合所提供的文献及 2016 年 8 月 6 日至 2017 年 7 月 25 日墨西哥湾（98–82°W，18–30°N）GLORYS12 数据，提出并研究一个关于套流区域反气旋涡垂向结构演变的想法。 |
| Q30 | 结合所提供的文献及 2011 年 7 月至 2015 年 8 月墨西哥湾（98–82°W，18–30°N）GLORYS12 数据，提出并研究一个关于海面高度所携带次表层水文信息的想法。 |

The authoritative English strings are in `tasks/Qxx/task_info.json`; the table abbreviates repeated input descriptions for readability.

## Packaged skill audit

This is an audit of repository-packaged skills, not a claim that a particular run loaded them or that a workspace's evolved skills are identical.

| Scientific concern | Existing guidance | Roles allowed to load it |
|---|---|---|
| Units, calendars, coordinates, grid layout, missingness, product identity | `ocean-dataset-diagnosis` | Data & Reproducibility |
| Area/volume weighting, valid denominators, ocean vs box coverage, baselines, seasonality, autocorrelation, uncertainty | `ocean-analysis-design`, with linked anomaly/trend/transport references | Data & Reproducibility; Ocean Process; Statistical Inference |
| Falsifiable claims, competing explanations, controls, sampling dependence, exploratory vs confirmatory analysis | `hypothesis-experiment-design` | Statistical Inference; Scientific Discussion |
| Physical consistency, flux/tendency conversion, budget residuals, association vs mechanism | `ocean-physical-consistency-review` | Ocean Process; Scientific Discussion |
| Reproducibility, provenance, executed evidence and exploratory changes | `reproducibility-audit` | Data & Reproducibility; Statistical Inference; Literature & Reproduction |
| Literature-grounded gaps, feasible ideas and novelty limits | `paper-grounded-idea-framing` | Coordinator; Scientific Discussion |

Skill selection is optional and role-filtered. `ocean_load_skill` checks the role/capabilities; for packaged skills it also returns the reference documents explicitly linked by that skill. A file existing under `resources/references` does not make it automatically available in every Expert's prompt. In particular, `methods/mixed-layer-depth.md` and `methods/water-mass.md` exist but no current packaged SKILL.md links them.

Most general scientific safeguards are covered, but coverage is not comprehensive: there is no dedicated packaged eddy-tracking or marine-heatwave-detection skill; the incomplete-ocean-colour reconstruction safeguards (held-out leakage, artificial vs real cloud gaps, temporal resolution limits) are not fully specified as a specialist workflow. Existing broad guidance is not a complete algorithm implementation or a guarantee of correct application. This query revision does not alter skills, their routing or automatic loading.

## Running and comparison

Generate a **new JSONL input file** from the updated catalogue with `prepare_queries.py`; existing batch1/batch2 JSONL files contain the old query strings and are not rewritten by a source update. Use new result directories. Existing downloads and bindings can be reused; data and reading-pack readiness requirements still apply.

Do not relabel previous attempts as general-query runs. The evaluator's query-hash check intentionally distinguishes them. Preserve the previous catalogue/rubric revision for evaluating historical runs.

For the pending Q21 **expert-policy-only** comparison, use the original batch1 JSONL unchanged for both sides. A run using the new general Q21 changes both query and Expert policy and cannot isolate the policy effect. Evaluate it as a separate experiment.

The shared benchmark runners still specify artifact locations, tools, data access and literature policy. Those operational instructions are separate from the scientific query. Scientific recipe hints removed here must not be appended by the runner or supplied to only one system as hidden task guidance.
