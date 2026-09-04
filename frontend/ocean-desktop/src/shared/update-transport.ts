import {createHash, timingSafeEqual} from 'node:crypto';
import {createReadStream} from 'node:fs';
import {lstat} from 'node:fs/promises';

import type {VerifiedUpdate} from './update-manifest.js';

export type UpdaterFeedFile = {
  url: string;
  size?: number;
};

export type UpdaterFeedInfo = {
  version: string;
  files: readonly UpdaterFeedFile[];
};

export type UpdaterCheckResult = {
  isUpdateAvailable: boolean;
  updateInfo: UpdaterFeedInfo;
};

export type PlatformUpdater = {
  autoDownload: boolean;
  autoInstallOnAppQuit: boolean;
  allowDowngrade: boolean;
  allowPrerelease: boolean;
  disableWebInstaller: boolean;
  disableDifferentialDownload: boolean;
  setFeedURL: (url: string) => void;
  checkForUpdates: () => Promise<UpdaterCheckResult | null>;
  downloadUpdate: () => Promise<string[]>;
  quitAndInstall: (isSilent?: boolean, isForceRunAfter?: boolean) => void;
};

export type PreparedDesktopUpdate = {
  update: VerifiedUpdate;
  downloadedPath: string;
};

export class UpdateTransportError extends Error {
  constructor(
    readonly code:
      | 'invalid_feed_url'
      | 'transport_unavailable'
      | 'transport_mismatch'
      | 'unexpected_downloads'
      | 'downloaded_file_integrity'
      | 'not_prepared',
    message: string,
  ) {
    super(message);
    this.name = 'UpdateTransportError';
  }
}

function fail(code: UpdateTransportError['code'], message: string): never {
  throw new UpdateTransportError(code, message);
}

function canonicalHttpsUrl(value: string, path: string): URL {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    fail('invalid_feed_url', `${path} is not a valid URL.`);
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.hash) {
    fail('invalid_feed_url', `${path} must be an HTTPS URL without credentials or a fragment.`);
  }
  return url;
}

function sameUrl(left: URL, right: URL): boolean {
  const leftBytes = Buffer.from(left.toString(), 'utf8');
  const rightBytes = Buffer.from(right.toString(), 'utf8');
  return leftBytes.byteLength === rightBytes.byteLength && timingSafeEqual(leftBytes, rightBytes);
}

export function assertUpdaterFeedMatchesSignedUpdate(
  update: VerifiedUpdate,
  feedBaseUrl: string,
  feed: UpdaterFeedInfo,
): void {
  const base = canonicalHttpsUrl(feedBaseUrl, 'Update feed base URL');
  const expected = canonicalHttpsUrl(update.package.url, 'Signed update package URL');
  if (feed.version !== update.version) {
    fail('transport_mismatch', 'Updater feed version does not match the signed update manifest.');
  }
  if (!Array.isArray(feed.files) || feed.files.length !== 1) {
    fail('transport_mismatch', 'Updater feed must name exactly one installation package.');
  }
  const candidate = feed.files[0];
  if (!candidate || typeof candidate.url !== 'string' || !candidate.url) {
    fail('transport_mismatch', 'Updater feed package URL is invalid.');
  }
  let candidateUrl: URL;
  try {
    candidateUrl = new URL(candidate.url, base);
  } catch {
    fail('transport_mismatch', 'Updater feed package URL cannot be resolved against its feed base URL.');
  }
  if (candidateUrl.protocol !== 'https:' || candidateUrl.username || candidateUrl.password || candidateUrl.hash) {
    fail('transport_mismatch', 'Updater feed package URL must resolve to a credential-free HTTPS URL.');
  }
  if (!sameUrl(candidateUrl, expected)) {
    fail('transport_mismatch', 'Updater feed package URL does not match the signed update manifest.');
  }
  if (!Number.isSafeInteger(candidate.size) || candidate.size !== update.package.sizeBytes) {
    fail('transport_mismatch', 'Updater feed package size does not match the signed update manifest.');
  }
}

export async function assertUpdaterDownloadedFileMatchesSignedUpdate(update: VerifiedUpdate, path: string): Promise<void> {
  let details: Awaited<ReturnType<typeof lstat>>;
  try {
    details = await lstat(path);
  } catch {
    fail('downloaded_file_integrity', 'Updater did not produce a readable installation file.');
  }
  if (!details.isFile() || details.isSymbolicLink() || details.size !== update.package.sizeBytes) {
    fail('downloaded_file_integrity', 'Updater installation file does not match the signed file type or byte count.');
  }
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path)) digest.update(chunk);
  const actual = Buffer.from(digest.digest('hex'), 'utf8');
  const expected = Buffer.from(update.package.sha256, 'utf8');
  if (actual.byteLength !== expected.byteLength || !timingSafeEqual(actual, expected)) {
    fail('downloaded_file_integrity', 'Updater installation file does not match the signed SHA-256.');
  }
}

export class DesktopUpdateCoordinator {
  private prepared: PreparedDesktopUpdate | null = null;

  constructor(
    private readonly updater: PlatformUpdater,
    private readonly feedBaseUrl: string,
  ) {
    canonicalHttpsUrl(feedBaseUrl, 'Update feed base URL');
    updater.autoDownload = false;
    updater.autoInstallOnAppQuit = false;
    updater.allowDowngrade = false;
    updater.allowPrerelease = false;
    updater.disableWebInstaller = true;
    updater.disableDifferentialDownload = true;
  }

  async prepare(update: VerifiedUpdate): Promise<PreparedDesktopUpdate> {
    this.prepared = null;
    this.updater.setFeedURL(this.feedBaseUrl);
    const check = await this.updater.checkForUpdates();
    if (!check || !check.isUpdateAvailable) {
      fail('transport_unavailable', 'Updater did not offer the signed update for this installed application.');
    }
    assertUpdaterFeedMatchesSignedUpdate(update, this.feedBaseUrl, check.updateInfo);
    const paths = await this.updater.downloadUpdate();
    if (paths.length !== 1 || typeof paths[0] !== 'string' || !paths[0]) {
      fail('unexpected_downloads', 'Updater did not produce exactly one installation package.');
    }
    const downloadedPath = paths[0];
    await assertUpdaterDownloadedFileMatchesSignedUpdate(update, downloadedPath);
    this.prepared = {update, downloadedPath};
    return this.prepared;
  }

  async installPrepared(): Promise<void> {
    if (!this.prepared) fail('not_prepared', 'No verified desktop update has been prepared for installation.');
    await assertUpdaterDownloadedFileMatchesSignedUpdate(this.prepared.update, this.prepared.downloadedPath);
    this.updater.quitAndInstall();
  }
}
