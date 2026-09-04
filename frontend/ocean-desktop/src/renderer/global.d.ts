import type {OceanDesktopBridge} from '../shared/bridge.js';

declare global {
  interface Window {
    oceanDesktop: OceanDesktopBridge;
  }
}

export {};
