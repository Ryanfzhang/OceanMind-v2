import {describe, expect, it} from 'vitest';

import {
  assertReleaseSigningEnvironment,
  releaseSigningRequirements,
  signedElectronBuilderArguments,
} from '../../scripts/release-signing.mjs';
import {hasDeveloperIdAuthority, signedPackageLayout} from '../../scripts/verify-signed-package.mjs';

describe('desktop release signing gates', () => {
  it('requires all macOS signing and notarization credentials without exposing values', () => {
    expect(releaseSigningRequirements('darwin')).toEqual([
      'CSC_LINK',
      'CSC_KEY_PASSWORD',
      'APPLE_ID',
      'APPLE_APP_SPECIFIC_PASSWORD',
      'APPLE_TEAM_ID',
    ]);
    expect(() => assertReleaseSigningEnvironment('darwin', {CSC_LINK: 'certificate'})).toThrow(
      'CSC_KEY_PASSWORD, APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID',
    );
    expect(signedElectronBuilderArguments('darwin', {
      CSC_LINK: 'certificate',
      CSC_KEY_PASSWORD: 'password',
      APPLE_ID: 'release@example.org',
      APPLE_APP_SPECIFIC_PASSWORD: 'app-password',
      APPLE_TEAM_ID: 'TEAM123456',
    })).toEqual(['-c.forceCodeSigning=true']);
  });

  it('requires a dedicated Windows Authenticode certificate', () => {
    expect(releaseSigningRequirements('win32')).toEqual(['WIN_CSC_LINK', 'WIN_CSC_KEY_PASSWORD']);
    expect(() => assertReleaseSigningEnvironment('win32', {WIN_CSC_LINK: 'certificate'})).toThrow('WIN_CSC_KEY_PASSWORD');
    expect(signedElectronBuilderArguments('win32', {
      WIN_CSC_LINK: 'certificate',
      WIN_CSC_KEY_PASSWORD: 'password',
    })).toEqual(['-c.forceCodeSigning=true']);
  });

  it('keeps signed-package layout target-specific and requires Developer ID evidence on macOS', () => {
    const root = '/release-root';
    const files = (directory: string, matcher: (file: string) => boolean) => [
      '/release-root/dist/Ocean Research Partner-0.1.0.dmg',
      '/release-root/dist/Ocean Research Partner-0.1.0-mac.zip',
    ].filter((path) => matcher(path.slice(directory.length + 1)));
    expect(signedPackageLayout({platform: 'darwin', root, name: 'Ocean Research Partner', files})).toMatchObject({
      app: '/release-root/dist/mac/Ocean Research Partner.app',
      executables: [
        '/release-root/dist/mac/Ocean Research Partner.app/Contents/MacOS/Ocean Research Partner',
        '/release-root/dist/mac/Ocean Research Partner.app/Contents/Resources/sidecar/ocean-backend/ocean-backend',
      ],
    });
    expect(hasDeveloperIdAuthority('Authority=Developer ID Application: Ocean Partner (TEAM123456)')).toBe(true);
    expect(hasDeveloperIdAuthority('Authority=Apple Development: Ocean Partner')).toBe(false);
  });
});
