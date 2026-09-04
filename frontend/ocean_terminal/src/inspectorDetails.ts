import type {ArtifactRef} from '../../packages/ocean-client/src/generated/protocol-v2.js';

export type InspectorDetailTone = 'info' | 'muted' | 'warning';

export type InspectorDetail = {
	key: string;
	text: string;
	tone: InspectorDetailTone;
};

type ArtifactLinks = {
	incoming: Record<string, unknown>[];
	outgoing: Record<string, unknown>[];
};

export function formatArtifactRef(ref: ArtifactRef): string {
	return `${ref.artifact_id}@v${String(ref.version).padStart(4, '0')}`;
}

export function asArtifactRef(value: unknown): ArtifactRef | null {
	const candidate = record(value);
	return typeof candidate.artifact_id === 'string'
		&& typeof candidate.version === 'number'
		&& Number.isInteger(candidate.version)
		&& candidate.version > 0
		? {artifact_id: candidate.artifact_id, version: candidate.version}
		: null;
}

export function verificationColor(state: string): 'green' | 'yellow' | 'red' {
	return state === 'pass' ? 'green' : state === 'fail' ? 'red' : 'yellow';
}

export function artifactMetadataDetails(
	artifactType: string,
	content: Record<string, unknown>,
): InspectorDetail[] {
	const details: InspectorDetail[] = [];
	const add = (key: string, text: string | null, tone: InspectorDetailTone = 'muted'): void => {
		if (text) {
			details.push({key, text, tone});
		}
	};

	switch (artifactType) {
			case 'dataset': {
				const level = stringValue(content.materialization_level) ?? 'unspecified materialization';
				const format = stringValue(content.format);
				add('dataset-contract', `Dataset ${level}${format ? `  ${format}` : ''}`, 'info');
				if (stringValue(content.schema_version) === 'ocean-derived-dataset/v1') {
					add('dataset-derived-from', refsLine('Derived from', content.source_dataset_refs), 'info');
					const variables = Array.isArray(content.variables)
						? content.variables.map((value) => variableLabel(record(value))).filter(isString)
						: [];
					add('dataset-derived-variables', variables.length > 0 ? `Variables ${variables.join(', ')}` : null);
					const runId = stringValue(content.analysis_run_id);
					const attemptId = stringValue(content.attempt_id);
					add('dataset-derived-run', runId ? `Run ${runId}${attemptId ? `  ${attemptId}` : ''}` : null, 'info');
					add('dataset-quicklook', stringValue(content.quicklook_uri) ? 'Quicklook PNG pinned' : null);
				}
				return details;
			}
		case 'claim': {
			add('claim-statement', prefixed('Claim', stringValue(content.statement)), 'info');
			const evidence = Array.isArray(content.evidence) ? content.evidence : [];
			evidence.slice(0, 3).forEach((value, index) => {
				const item = record(value);
				const paper = asArtifactRef(item.paper_ref);
				const locatorKind = stringValue(item.locator_kind);
				const locator = stringValue(item.locator);
				const relationship = stringValue(item.relationship) ?? 'supports';
				add(
					`claim-evidence-${index}`,
					paper && locatorKind && locator
						? `Evidence ${relationship}: ${formatArtifactRef(paper)} ${locatorKind} ${locator}`
						: null,
					'info',
				);
			});
			addLimitations(details, content.limitations, 'claim-limitation');
			return details;
		}
		case 'observation': {
			add('observation-statement', prefixed('Observation', stringValue(content.statement)), 'info');
			add('observation-kind', prefixed('Kind', stringValue(content.observation_kind)));
			add('observation-sources', refsLine('Sources', content.source_refs), 'info');
			addLimitations(details, content.limitations, 'observation-limitation');
			return details;
		}
		case 'hypothesis': {
			add('hypothesis-mechanism', prefixed('Mechanism', stringValue(content.mechanism)));
			add('hypothesis-evidence', refsLine('Evidence', content.evidence_refs), 'info');
			return details;
		}
		case 'experiment': {
			const outcome = stringValue(content.outcome) ?? 'proposed';
			add('experiment-outcome', `Outcome ${outcome}`, outcome === 'inconclusive' || outcome === 'failed' ? 'warning' : 'info');
			add('experiment-question', prefixed('Question', stringValue(content.scientific_question)), 'info');
			add('experiment-hypothesis', refLine('Hypothesis', content.hypothesis_ref), 'info');
			add('experiment-inputs', refsLine('Inputs', content.input_refs), 'info');
			add('experiment-plans', refsLine('Plans', content.analysis_plan_refs), 'info');
			add('experiment-results', refsLine('Results', content.result_refs), 'info');
			add('experiment-outcome-summary', prefixed('Outcome detail', stringValue(content.outcome_summary)), outcome === 'inconclusive' || outcome === 'failed' ? 'warning' : 'muted');
			return details;
		}
		case 'analysis_plan': {
			add('plan-question', prefixed('Question', stringValue(content.scientific_question)), 'info');
			add('plan-method', prefixed('Method', stringValue(content.method_summary)));
			add('plan-spec', refLine('FigureSpec', content.figure_spec_ref), 'info');
			add('plan-inputs', refsLine('Inputs', content.input_refs), 'info');
			const outputs = Array.isArray(content.expected_outputs)
				? content.expected_outputs.map((value) => stringValue(record(value).name)).filter(isString)
				: [];
			add('plan-outputs', outputs.length > 0 ? `Outputs ${outputs.join(', ')}` : null);
			return details;
		}
		case 'spatial_layer': {
			const variable = stringValue(content.variable) ?? 'field';
			const units = stringValue(content.units);
			add('layer-source', `${variable}${units ? ` [${units}]` : ''} from ${refOrUnknown(content.data_ref)}`, 'info');
			const longitude = stringValue(content.longitude_coordinate);
			const latitude = stringValue(content.latitude_coordinate);
			const sourceRegistration = stringValue(content.source_coordinate_registration);
			const pixelRegistration = stringValue(content.pixel_registration);
			const rowOrder = stringValue(content.row_order);
			add(
				'layer-registration',
				longitude && latitude && sourceRegistration && pixelRegistration && rowOrder
					? `${longitude}/${latitude} ${sourceRegistration} -> ${pixelRegistration}; rows ${rowOrder}`
					: null,
				'info',
			);
			const width = numberValue(content.width);
			const height = numberValue(content.height);
			const parts = Array.isArray(content.parts) ? content.parts.length : 0;
			const resampling = stringValue(content.resampling);
			const nodata = numberValue(content.nodata_alpha);
			add(
				'layer-rendering',
				width !== null && height !== null
					? `${width}x${height} pixels  ${parts} parts  ${resampling ?? 'resampling unspecified'}  nodata alpha ${nodata ?? 'unspecified'}`
					: null,
			);
			return details;
		}
		case 'selection': {
			const geometryType = stringValue(content.geometry_type);
			const label = stringValue(content.label);
			add('selection', [geometryType, label].filter(isString).join('  ') || null, 'info');
			return details;
		}
		case 'linked_plot': {
			add('linked-plot-kind', prefixed('Plot', stringValue(content.plot_kind)), 'info');
			add('linked-plot-selection', refLine('Selection', content.selection_ref), 'info');
			const variables = Array.isArray(content.variables)
				? content.variables.map((value) => variableLabel(record(value))).filter(isString)
				: [];
			add('linked-plot-variables', variables.length > 0 ? `Variables ${variables.join(', ')}` : null);
			return details;
		}
		case 'figure': {
			add('figure-spec', refLine('FigureSpec', content.figure_spec_ref), 'info');
			add('figure-plan', refLine('AnalysisPlan', content.analysis_plan_ref), 'info');
			const runId = stringValue(content.analysis_run_id);
			const attemptId = stringValue(content.attempt_id);
			add('figure-run', runId ? `Run ${runId}${attemptId ? `  ${attemptId}` : ''}` : null, 'info');
			return details;
		}
		default:
			return details;
	}
}

export function artifactLinkDetails(links: ArtifactLinks): InspectorDetail[] {
	const details: InspectorDetail[] = [];
	for (const [index, value] of links.outgoing.slice(0, 5).entries()) {
		const link = record(value);
		const target = asArtifactRef(link.target);
		const relation = stringValue(link.relation) ?? 'related_to';
		if (target) {
			details.push({
				key: `outgoing-${index}`,
				text: `Out ${relation} -> ${formatArtifactRef(target)}`,
				tone: 'info',
			});
		}
	}
	for (const [index, value] of links.incoming.slice(0, 5).entries()) {
		const link = record(value);
		const source = asArtifactRef(link.source);
		const relation = stringValue(link.relation) ?? 'related_to';
		if (source) {
			details.push({
				key: `incoming-${index}`,
				text: `In ${formatArtifactRef(source)} -> ${relation}`,
				tone: 'muted',
			});
		}
	}
	return details;
}

function addLimitations(details: InspectorDetail[], value: unknown, keyPrefix: string): void {
	if (!Array.isArray(value)) {
		return;
	}
	value.slice(0, 2).forEach((item, index) => {
		const limitation = stringValue(item);
		if (limitation) {
			details.push({key: `${keyPrefix}-${index}`, text: `Limitation ${limitation}`, tone: 'warning'});
		}
	});
}

function refLine(label: string, value: unknown): string | null {
	const ref = asArtifactRef(value);
	return ref ? `${label} ${formatArtifactRef(ref)}` : null;
}

function refsLine(label: string, value: unknown): string | null {
	const refs = artifactRefs(value);
	return refs.length > 0 ? `${label} ${formatRefs(refs)}` : null;
}

function refOrUnknown(value: unknown): string {
	const ref = asArtifactRef(value);
	return ref ? formatArtifactRef(ref) : 'unavailable source';
}

function artifactRefs(value: unknown): ArtifactRef[] {
	return Array.isArray(value)
		? value.map(asArtifactRef).filter((ref): ref is ArtifactRef => ref !== null)
		: [];
}

function formatRefs(refs: ArtifactRef[]): string {
	const visible = refs.slice(0, 3).map(formatArtifactRef);
	return `${visible.join(', ')}${refs.length > visible.length ? ` +${refs.length - visible.length}` : ''}`;
}

function prefixed(prefix: string, value: string | null): string | null {
	return value ? `${prefix} ${value}` : null;
}

function variableLabel(value: Record<string, unknown>): string | null {
	const name = stringValue(value.name);
	const units = stringValue(value.units);
	return name ? `${name}${units ? ` [${units}]` : ''}` : null;
}

function stringValue(value: unknown): string | null {
	const normalized = typeof value === 'string' ? value.trim() : '';
	return normalized || null;
}

function numberValue(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function isString(value: string | null): value is string {
	return value !== null;
}

function record(value: unknown): Record<string, unknown> {
	return value !== null && typeof value === 'object' && !Array.isArray(value)
		? value as Record<string, unknown>
		: {};
}
