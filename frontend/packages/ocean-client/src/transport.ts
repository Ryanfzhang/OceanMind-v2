import type {OceanEvent} from './generated/protocol-v2.js';

export class TransportSequenceTracker {
	private lastSequence = 0;

	observe(event: OceanEvent): boolean {
		const gap = this.lastSequence === 0
			? event.sequence !== 1
			: event.sequence !== this.lastSequence + 1;
		this.lastSequence = event.sequence;
		return gap;
	}

	reset(): void {
		this.lastSequence = 0;
	}
}
