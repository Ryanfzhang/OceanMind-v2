import assert from 'node:assert/strict';

import {artifactLinkDetails, artifactMetadataDetails, verificationColor} from '../src/inspectorDetails.js';

const claim = artifactMetadataDetails('claim', {
	statement: 'A bounded literature claim.',
	evidence: [{
		paper_ref: {artifact_id: 'paper_fixture', version: 1},
		locator_kind: 'figure',
		locator: 'Figure 2',
		relationship: 'supports',
	}],
	limitations: ['Metadata only; paper text remains local.'],
});
assert.ok(claim.some((detail) => detail.text.includes('paper_fixture@v0001 figure Figure 2')));
assert.ok(claim.some((detail) => detail.text.startsWith('Limitation ')));

const experiment = artifactMetadataDetails('experiment', {
	outcome: 'inconclusive',
	scientific_question: 'Does the series separate two explanations?',
	hypothesis_ref: {artifact_id: 'hypothesis_fixture', version: 1},
	input_refs: [{artifact_id: 'dataset_fixture', version: 1}],
	analysis_plan_refs: [{artifact_id: 'analysis_plan_fixture', version: 1}],
	result_refs: [{artifact_id: 'linked_plot_fixture', version: 2}],
	outcome_summary: 'The checked result does not discriminate the mechanism.',
});
assert.ok(experiment.some((detail) => detail.text === 'Outcome inconclusive' && detail.tone === 'warning'));
assert.ok(experiment.some((detail) => detail.text.includes('linked_plot_fixture@v0002')));

const spatial = artifactMetadataDetails('spatial_layer', {
	data_ref: {artifact_id: 'dataset_fixture', version: 1},
	variable: 'sst',
	units: 'degC',
	longitude_coordinate: 'lon',
	latitude_coordinate: 'lat',
	source_coordinate_registration: 'center',
	pixel_registration: 'outer_edges',
	row_order: 'north_to_south',
	width: 8,
	height: 4,
	parts: [{}, {}],
	resampling: 'nearest',
	nodata_alpha: 0,
});
assert.ok(spatial.some((detail) => detail.text.includes('sst [degC] from dataset_fixture@v0001')));
assert.ok(spatial.some((detail) => detail.text.includes('rows north_to_south')));

const derivedDataset = artifactMetadataDetails('dataset', {
	schema_version: 'ocean-derived-dataset/v1',
	materialization_level: 'materialized_snapshot',
	format: 'netcdf',
	source_dataset_refs: [{artifact_id: 'dataset_source', version: 1}],
	analysis_run_id: 'run_derived_fixture',
	attempt_id: 'attempt_derived_fixture',
	quicklook_uri: 'ocean://artifacts/dataset/dataset_derived/v0001/quicklook.png',
	variables: [{name: 'sst_anomaly', units: 'degC'}],
});
assert.ok(derivedDataset.some((detail) => detail.text.includes('Derived from dataset_source@v0001')));
assert.ok(derivedDataset.some((detail) => detail.text.includes('Variables sst_anomaly [degC]')));
assert.ok(derivedDataset.some((detail) => detail.text === 'Quicklook PNG pinned'));

const links = artifactLinkDetails({
	outgoing: [{
		target: {artifact_id: 'dataset_fixture', version: 1},
		relation: 'uses_dataset',
	}],
	incoming: [{
		source: {artifact_id: 'report_fixture', version: 1},
		relation: 'uses_evidence',
	}],
});
assert.ok(links.some((detail) => detail.text === 'Out uses_dataset -> dataset_fixture@v0001'));
assert.ok(links.some((detail) => detail.text === 'In report_fixture@v0001 -> uses_evidence'));

assert.equal(verificationColor('pass'), 'green');
assert.equal(verificationColor('warning'), 'yellow');
assert.equal(verificationColor('fail'), 'red');

console.log('artifact-inspector-evidence=verified');
