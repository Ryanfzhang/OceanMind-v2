import {createHash} from 'node:crypto';
import {mkdtemp, symlink, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

import {describe, expect, it} from 'vitest';

import {
  DesktopUpdateCoordinator,
  UpdateTransportError,
  assertUpdaterDownloadedFileMatchesSignedUpdate,
  assertUpdaterFeedMatchesSignedUpdate,
  type PlatformUpdater,
  type UpdaterCheckResult,
} from '../shared/update-transport.js';
import type {VerifiedUpdate} from '../shared/update-manifest.js';

const feedBaseUrl = 'https://updates.oceanpartner.example/releases/';
const packageBytes = Buffer.from('signed desktop installation package');

function update(): VerifiedUpdate {
  return {
    version: '0.1.1',
    protocolVersion: 2,
    backendSchema: 'ocean-desktop-backend/v1',
    package: {
      platform: 'darwin',
      architecture: 'arm64',
      url: `${feedBaseUrl}0.1.1/Ocean-Partner-arm64.zip`,
      sha256: createHash('sha256').update(packageBytes).digest('hex'),
      sizeBytes: packageBytes.byteLength,
    },
  };
}

function feed(): UpdaterCheckResult['updateInfo'] {
  return {
    version: '0.1.1',
    files: [{url: '0.1.1/Ocean-Partner-arm64.zip', size: packageBytes.byteLength}],
  };
}

class FakeUpdater implements PlatformUpdater {
  autoDownload = true;
  autoInstallOnAppQuit = true;
  allowDowngrade = true;
  allowPrerelease = true;
  disableWebInstaller = false;
  disableDifferentialDownload = false;
  feedUrl: string | null = null;
  installs = 0;

  constructor(
    private readonly result: UpdaterCheckResult | null,
    private readonly downloadedPaths: string[],
  ) {}

  setFeedURL(url: string): void {
    this.feedUrl = url;
  }

  async checkForUpdates(): Promise<UpdaterCheckResult | null> {
    return this.result;
  }

  async downloadUpdate(): Promise<string[]> {
    return this.downloadedPaths;
  }

  quitAndInstall(): void {
    this.installs += 1;
  }
}

function expectTransportError(action: () => unknown, code: UpdateTransportError['code']): void {
  expect(action).toThrow(expect.objectContaining({name: 'UpdateTransportError', code}));
}

describe('desktop update transport coordinator', () => {
  it('requires the updater feed to select the exact signed package', () => {
    assertUpdaterFeedMatchesSignedUpdate(update(), feedBaseUrl, feed());
    expectTransportError(
      () => assertUpdaterFeedMatchesSignedUpdate(update(), feedBaseUrl, {...feed(), version: '0.1.2'}),
      'transport_mismatch',
    );
    expectTransportError(
      () => assertUpdaterFeedMatchesSignedUpdate(update(), feedBaseUrl, {...feed(), files: [{url: '0.1.1/other.zip', size: packageBytes.byteLength}]}),
      'transport_mismatch',
    );
    expectTransportError(
      () => assertUpdaterFeedMatchesSignedUpdate(update(), feedBaseUrl, {...feed(), files: [{url: '0.1.1/Ocean-Partner-arm64.zip'}]}),
      'transport_mismatch',
    );
  });

  it('checks the updater-owned package again before accepting or applying it', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'ocean-update-transport-'));
    const packagePath = join(directory, 'Ocean-Partner-arm64.zip');
    await writeFile(packagePath, packageBytes);
    await assertUpdaterDownloadedFileMatchesSignedUpdate(update(), packagePath);
    await writeFile(packagePath, 'tampered package');
    await expect(assertUpdaterDownloadedFileMatchesSignedUpdate(update(), packagePath))
      .rejects.toMatchObject({name: 'UpdateTransportError', code: 'downloaded_file_integrity'});
  });

  it('refuses a symbolic-link updater result even when it points to matching bytes', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'ocean-update-transport-'));
    const packagePath = join(directory, 'Ocean-Partner-arm64.zip');
    const linkPath = join(directory, 'linked-package.zip');
    await writeFile(packagePath, packageBytes);
    await symlink(packagePath, linkPath);
    await expect(assertUpdaterDownloadedFileMatchesSignedUpdate(update(), linkPath))
      .rejects.toMatchObject({name: 'UpdateTransportError', code: 'downloaded_file_integrity'});
  });

  it('disables implicit updater behavior and installs only a freshly reverified package', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'ocean-update-transport-'));
    const packagePath = join(directory, 'Ocean-Partner-arm64.zip');
    await writeFile(packagePath, packageBytes);
    const updater = new FakeUpdater({isUpdateAvailable: true, updateInfo: feed()}, [packagePath]);
    const coordinator = new DesktopUpdateCoordinator(updater, feedBaseUrl);

    expect(updater).toMatchObject({
      autoDownload: false,
      autoInstallOnAppQuit: false,
      allowDowngrade: false,
      allowPrerelease: false,
      disableWebInstaller: true,
      disableDifferentialDownload: true,
    });
    await expect(coordinator.installPrepared()).rejects.toMatchObject({code: 'not_prepared'});
    await coordinator.prepare(update());
    expect(updater.feedUrl).toBe(feedBaseUrl);
    await coordinator.installPrepared();
    expect(updater.installs).toBe(1);

    await writeFile(packagePath, 'tampered after prepare');
    await expect(coordinator.installPrepared()).rejects.toMatchObject({code: 'downloaded_file_integrity'});
    expect(updater.installs).toBe(1);
  });

  it('fails closed when the updater has no matching package or produces extra files', async () => {
    const noUpdate = new FakeUpdater(null, []);
    const unavailable = new DesktopUpdateCoordinator(noUpdate, feedBaseUrl);
    await expect(unavailable.prepare(update())).rejects.toMatchObject({code: 'transport_unavailable'});

    const directory = await mkdtemp(join(tmpdir(), 'ocean-update-transport-'));
    const packagePath = join(directory, 'Ocean-Partner-arm64.zip');
    await writeFile(packagePath, packageBytes);
    const multiple = new DesktopUpdateCoordinator(
      new FakeUpdater({isUpdateAvailable: true, updateInfo: feed()}, [packagePath, packagePath]),
      feedBaseUrl,
    );
    await expect(multiple.prepare(update())).rejects.toMatchObject({code: 'unexpected_downloads'});
  });
});
