import assert from 'node:assert/strict';

import {expectedWorkspaceOpenRevision} from '../../packages/ocean-client/src/workspace-revision.js';

assert.equal(expectedWorkspaceOpenRevision(null, 'ws_default'), undefined);
assert.equal(
	expectedWorkspaceOpenRevision({workspace_id: 'ws_other', revision: 9}, 'ws_default'),
	undefined,
);
assert.equal(
	expectedWorkspaceOpenRevision({workspace_id: 'ws_default', revision: 9}, 'ws_default'), 9);

console.log('workspace-open-revision=verified');
