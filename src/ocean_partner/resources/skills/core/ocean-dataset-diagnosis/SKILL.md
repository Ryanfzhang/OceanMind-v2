---
name: ocean-dataset-diagnosis
description: Diagnose coordinate conventions, units, masks, grids, and missingness before an Ocean analysis is designed or run.
metadata:
  origin: oceanmind
  roles:
    - data_reproducibility_expert
---

# Ocean Dataset Diagnosis

## when_to_use
Use before a computation depends on a new NetCDF, Zarr, gridded product, station collection, or model output.

## research_objective
Establish what the data can represent and which coordinate or metadata ambiguities could invalidate an analysis.

## questions_to_resolve
- Are longitude convention, latitude order, depth sign, calendar, frequency, units, fill values, and masks explicit?
- Is the grid rectilinear, curvilinear, staggered, or otherwise unsuitable for a direct map overlay?
- What are resolution, chunks, missing ratio, and plausible land or boundary contamination risks?

## evidence_requirements
Keep dataset identity, materialization level, coordinate summary, units, and a bounded diagnostic result as DatasetArtifact and DatasetDiagnosisArtifact evidence.

## process_checkpoints
Separate metadata inspection from numerical analysis. Flag uncertainty rather than guessing a CF convention.

## expected_artifacts
DatasetArtifact and DatasetDiagnosisArtifact.

## quality_gates
Never assume a positive-down depth, Gregorian calendar, regular grid, or Celsius/Kelvin conversion without evidence.

## stop_or_escalation_conditions
Escalate when coordinate semantics, units, grid metrics, or calendar cannot be established from the dataset and source documentation.

## relevant_references
`references/data/cf-conventions.md`, `references/data/calendars.md`, `references/data/grids-and-coordinates.md`, `references/data/common-variables-and-units.md`.
