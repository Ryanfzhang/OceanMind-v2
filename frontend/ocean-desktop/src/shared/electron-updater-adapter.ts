import type {PlatformUpdater} from './update-transport.js';

type ElectronUpdaterModule = {
  default?: {autoUpdater?: unknown};
  autoUpdater?: unknown;
};

function isPlatformUpdater(value: unknown): value is PlatformUpdater {
  if (value === null || typeof value !== 'object') return false;
  const updater = value as Partial<PlatformUpdater>;
  return typeof updater.setFeedURL === 'function'
    && typeof updater.checkForUpdates === 'function'
    && typeof updater.downloadUpdate === 'function'
    && typeof updater.quitAndInstall === 'function';
}

export async function loadElectronPlatformUpdater(): Promise<PlatformUpdater> {
  const module = await import('electron-updater') as ElectronUpdaterModule;
  const updater = module.default?.autoUpdater ?? module.autoUpdater;
  if (!isPlatformUpdater(updater)) {
    throw new Error('electron-updater did not expose a compatible platform updater.');
  }
  return updater;
}
