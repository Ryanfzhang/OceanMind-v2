/** Keep each Electron E2E process out of the developer's real profile and single-instance lock. */
export function enableIsolatedElectronTestProfile(): void {
  process.env.OCEAN_DESKTOP_TEST_ISOLATED_PROFILE = '1';
}
