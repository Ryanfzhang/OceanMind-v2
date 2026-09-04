/** Return an optimistic-concurrency revision only when the client actually knows it. */
export function expectedWorkspaceOpenRevision(
	workspace: {workspace_id: string; revision: number} | null,
	requestedWorkspaceId: string,
): number | undefined {
	return workspace?.workspace_id === requestedWorkspaceId ? workspace.revision : undefined;
}
