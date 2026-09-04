import {createPublicKey, type KeyObject} from 'node:crypto';

import type {TrustedUpdateKeys} from './update-manifest.js';

export const DESKTOP_UPDATE_CONFIG_SCHEMA = 'ocean-desktop-update-config/v1';

export type DisabledDesktopUpdateConfig = {
  enabled: false;
};

export type EnabledDesktopUpdateConfig = {
  enabled: true;
  manifestUrl: string;
  feedBaseUrl: string;
  trustedKeys: TrustedUpdateKeys;
};

export type DesktopUpdateConfig = DisabledDesktopUpdateConfig | EnabledDesktopUpdateConfig;

function record(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${path} must be an object.`);
  }
  return value as Record<string, unknown>;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], path: string): void {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${path} has unsupported or missing fields.`);
  }
}

function httpsUrl(value: unknown, path: string): string {
  if (typeof value !== 'string' || !value || value.length > 2_048) throw new Error(`${path} is invalid.`);
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${path} is invalid.`);
  }
  if (url.protocol !== 'https:' || url.username || url.password || url.hash) {
    throw new Error(`${path} must be an HTTPS URL without credentials or a fragment.`);
  }
  return url.toString();
}

function trustedKeys(value: unknown): TrustedUpdateKeys {
  const keys = record(value, 'Desktop update trusted_keys');
  const entries = Object.entries(keys);
  if (!entries.length || entries.length > 16) throw new Error('Desktop update trusted_keys must contain between one and sixteen keys.');
  const parsed: Record<string, KeyObject> = {};
  for (const [keyId, pem] of entries) {
    if (!/^[A-Za-z0-9._-]{1,96}$/.test(keyId) || typeof pem !== 'string' || pem.length > 8_192) {
      throw new Error('Desktop update trusted_keys contains an invalid key identifier or value.');
    }
    let key: KeyObject;
    try {
      key = createPublicKey(pem);
    } catch {
      throw new Error(`Desktop update trusted key ${keyId} is not a public key.`);
    }
    if (key.asymmetricKeyType !== 'ed25519') {
      throw new Error(`Desktop update trusted key ${keyId} must be Ed25519.`);
    }
    parsed[keyId] = key;
  }
  return parsed;
}

export function parseDesktopUpdateConfig(value: unknown): DesktopUpdateConfig {
  const config = record(value, 'Desktop update configuration');
  if (config.enabled === false) {
    exactKeys(config, ['enabled', 'schema_version'], 'Desktop update configuration');
    if (config.schema_version !== DESKTOP_UPDATE_CONFIG_SCHEMA) {
      throw new Error('Desktop update configuration schema is unsupported.');
    }
    return {enabled: false};
  }
  exactKeys(config, ['enabled', 'feed_url', 'manifest_url', 'schema_version', 'trusted_keys'], 'Desktop update configuration');
  if (config.schema_version !== DESKTOP_UPDATE_CONFIG_SCHEMA || config.enabled !== true) {
    throw new Error('Desktop update configuration schema or enabled flag is unsupported.');
  }
  return {
    enabled: true,
    manifestUrl: httpsUrl(config.manifest_url, 'Desktop update manifest_url'),
    feedBaseUrl: httpsUrl(config.feed_url, 'Desktop update feed_url'),
    trustedKeys: trustedKeys(config.trusted_keys),
  };
}
