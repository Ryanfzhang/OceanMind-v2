import assert from 'node:assert/strict';

import {TransportSequenceTracker} from '../../packages/ocean-client/src/transport.js';
import type {OceanEvent} from '../../packages/ocean-client/src/generated/protocol-v2.js';

const event = (sequence: number): OceanEvent => ({
	protocol_version: 2,
	event_id: `evt_transport_${sequence}`,
	session_id: 'ses_transport',
	workspace_id: 'ws_transport',
	request_id: 'req_transport',
	sequence,
	timestamp: '2026-07-11T12:00:00Z',
	type: 'request.completed',
	payload: {result: {}},
});

const tracker = new TransportSequenceTracker();
assert.equal(tracker.observe(event(1)), false);
assert.equal(tracker.observe(event(2)), false);
assert.equal(tracker.observe(event(4)), true);
assert.equal(tracker.observe(event(5)), false);
tracker.reset();
assert.equal(tracker.observe(event(1)), false);
tracker.reset();
assert.equal(tracker.observe(event(2)), true);
console.log('transport-sequence-gap=verified');
