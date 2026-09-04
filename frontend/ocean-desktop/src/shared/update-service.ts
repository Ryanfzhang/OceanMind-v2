import {
  selectSignedUpdate,
  type TrustedUpdateKeys,
  type UpdateRuntime,
  type VerifiedUpdate,
} from './update-manifest.js';
import {
  DesktopUpdateCoordinator,
  type PlatformUpdater,
  type PreparedDesktopUpdate,
} from './update-transport.js';

export type SignedManifestFetcher = (url: string) => Promise<unknown>;

export type DesktopUpdateServiceOptions = {
  manifestUrl: string;
  feedBaseUrl: string;
  runtime: UpdateRuntime;
  trustedKeys: TrustedUpdateKeys;
  updater: PlatformUpdater;
  fetchManifest: SignedManifestFetcher;
};

export class DesktopUpdateService {
  private readonly coordinator: DesktopUpdateCoordinator;
  private readonly manifestUrl: string;
  private readonly runtime: UpdateRuntime;
  private readonly trustedKeys: TrustedUpdateKeys;
  private readonly fetchManifest: SignedManifestFetcher;
  private prepared: PreparedDesktopUpdate | null = null;

  constructor(options: DesktopUpdateServiceOptions) {
    this.manifestUrl = assertManifestUrl(options.manifestUrl);
    this.runtime = options.runtime;
    this.trustedKeys = options.trustedKeys;
    this.fetchManifest = options.fetchManifest;
    this.coordinator = new DesktopUpdateCoordinator(options.updater, options.feedBaseUrl);
  }

  async checkAndPrepare(): Promise<PreparedDesktopUpdate> {
    this.prepared = null;
    const manifest = await this.fetchManifest(this.manifestUrl);
    const update = selectSignedUpdate(manifest, this.runtime, this.trustedKeys);
    this.prepared = await this.coordinator.prepare(update);
    return this.prepared;
  }

  preparedUpdate(): VerifiedUpdate | null {
    return this.prepared?.update ?? null;
  }

  async installPrepared(): Promise<void> {
    await this.coordinator.installPrepared();
  }
}

function assertManifestUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error('Signed update manifest URL is invalid.');
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.hash) {
    throw new Error('Signed update manifest URL must be an HTTPS URL without credentials or a fragment.');
  }
  return url.toString();
}
