import {createHash, generateKeyPairSync, sign} from 'node:crypto';
import {mkdtemp, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

import {describe, expect, it} from 'vitest';

import {
  UPDATE_MANIFEST_SCHEMA,
  canonicalUpdatePayloadForSigning,
  type UpdatePackage,
} from '../shared/update-manifest.js';
import {DesktopUpdateService} from '../shared/update-service.js';
import type {PlatformUpdater, UpdaterCheckResult} from '../shared/update-transport.js';

const keys = generateKeyPairSync('ed25519');
const feedBaseUrl = 'https://updates.oceanpartner.example/releases/';
const manifestUrl = 'https://updates.oceanpartner.example/manifests/stable.json';
const packageBytes = Buffer.from('prepared by the verified updater');
const updatePackage: UpdatePackage = {
  platform: 'darwin',
  architecture: 'arm64',
  url: `${feedBaseUrl}0.1.1/Ocean-Partner-arm64.zip`,
  sha256: createHash('sha256').update(packageBytes).digest('hex'),
  sizeBytes: packageBytes.byteLength,
};

function manifest() {
  const payload = {
    keyId: 'release-2026-a',
    version: '0.1.1',
    protocolVersion: 2,
    backendSchema: 'ocean-desktop-backend/v1',
    packages: [updatePackage],
  };
  return {
    schema_version: UPDATE_MANIFEST_SCHEMA,
    key_id: payload.keyId,
    release: {
      version: payload.version,
      protocol_version: payload.protocolVersion,
      backend_schema: payload.backendSchema,
      packages: payload.packages.map((item) => ({
        platform: item.platform,
        architecture: item.architecture,
        url: item.url,
        sha256: item.sha256,
        size_bytes: item.sizeBytes,
      })),
    },
    signature: sign(null, canonicalUpdatePayloadForSigning(payload), keys.privateKey).toString('base64'),
  };
}

class FakeUpdater implements PlatformUpdater {
  autoDownload = true;
  autoInstallOnAppQuit = true;
  allowDowngrade = true;
  allowPrerelease = true;
  disableWebInstaller = false;
  disableDifferentialDownload = false;
  installs = 0;

  constructor(private readonly downloadedPath: string) {}

  setFeedURL(): void {}

  async checkForUpdates(): Promise<UpdaterCheckResult> {
    return {
      isUpdateAvailable: true,
      updateInfo: {
        version: '0.1.1',
        files: [{url: '0.1.1/Ocean-Partner-arm64.zip', size: packageBytes.byteLength}],
      },
    };
  }

  async downloadUpdate(): Promise<string[]> {
    return [this.downloadedPath];
  }

  quitAndInstall(): void {
    this.installs += 1;
  }
}

describe('desktop update service', () => {
  it('does not contact a platform updater until the signed manifest admits an exact update', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'ocean-update-service-'));
    const packagePath = join(directory, 'Ocean-Partner-arm64.zip');
    await writeFile(packagePath, packageBytes);
    const updater = new FakeUpdater(packagePath);
    const fetched: string[] = [];
    const service = new DesktopUpdateService({
      manifestUrl,
      feedBaseUrl,
      runtime: {
        version: '0.1.0',
        protocolVersion: 2,
        backendSchema: 'ocean-desktop-backend/v1',
        platform: 'darwin',
        architecture: 'arm64',
      },
      trustedKeys: {'release-2026-a': keys.publicKey},
      updater,
      fetchManifest: async (url) => {
        fetched.push(url);
        return manifest();
      },
    });

    expect(service.preparedUpdate()).toBeNull();
    await service.checkAndPrepare();
    expect(fetched).toEqual([manifestUrl]);
    expect(service.preparedUpdate()).toMatchObject({version: '0.1.1', package: updatePackage});
    await service.installPrepared();
    expect(updater.installs).toBe(1);
  });

  it('fails before contacting the updater when manifest admission rejects the release', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'ocean-update-service-'));
    const packagePath = join(directory, 'Ocean-Partner-arm64.zip');
    await writeFile(packagePath, packageBytes);
    const updater = new FakeUpdater(packagePath);
    const rejectedManifest = manifest();
    rejectedManifest.release.backend_schema = 'ocean-desktop-backend/v2';
    await expect(new DesktopUpdateService({
      manifestUrl,
      feedBaseUrl,
      runtime: {
        version: '0.1.0',
        protocolVersion: 2,
        backendSchema: 'ocean-desktop-backend/v1',
        platform: 'darwin',
        architecture: 'arm64',
      },
      trustedKeys: {'release-2026-a': keys.publicKey},
      updater,
      fetchManifest: async () => rejectedManifest,
    }).checkAndPrepare()).rejects.toMatchObject({code: 'invalid_signature'});
    expect(updater.installs).toBe(0);
  });

  it('rejects an invalid signed-manifest endpoint before it can fetch', () => {
    expect(() => new DesktopUpdateService({
      manifestUrl: 'http://updates.oceanpartner.example/manifest.json',
      feedBaseUrl,
      runtime: {
        version: '0.1.0',
        protocolVersion: 2,
        backendSchema: 'ocean-desktop-backend/v1',
        platform: 'darwin',
        architecture: 'arm64',
      },
      trustedKeys: {'release-2026-a': keys.publicKey},
      updater: new FakeUpdater('/not-used'),
      fetchManifest: async () => manifest(),
    })).toThrow('HTTPS');
  });
});
