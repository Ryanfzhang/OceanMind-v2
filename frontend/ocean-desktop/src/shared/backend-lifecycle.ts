/** Only the currently active backend can produce a user-visible unexpected exit. */
export function shouldPublishBackendExit(
  stopping: boolean,
  childIsCurrent: boolean,
): boolean {
  return !stopping && childIsCurrent;
}
