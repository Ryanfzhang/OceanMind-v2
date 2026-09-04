import {generateKeyPairSync} from 'node:crypto';

import {describe, expect, it} from 'vitest';

import {
  DESKTOP_UPDATE_CONFIG_SCHEMA,
  parseDesktopUpdateConfig,
} from '../shared/update-config.js';

const keys = generateKeyPairSync('ed25519');

describe('desktop update configuration', () => {
  it('keeps updates disabled unless an exact enabled configuration is shipped', () => {
    expect(parseDesktopUpdateConfig({schema_version: DESKTOP_UPDATE_CONFIG_SCHEMA, enabled: false})).toEqual({enabled: false});
    expect(() => parseDesktopUpdateConfig({schema_version: DESKTOP_UPDATE_CONFIG_SCHEMA, enabled: false, feed_url: 'https://updates.example/'}))
      .toThrow('unsupported or missing');
  });

  it('loads only exact HTTPS endpoints and Ed25519 public keys', () => {
    const config = parseDesktopUpdateConfig({
      schema_version: DESKTOP_UPDATE_CONFIG_SCHEMA,
      enabled: true,
      manifest_url: 'https://updates.oceanpartner.example/manifests/stable.json',
      feed_url: 'https://updates.oceanpartner.example/releases/',
      trusted_keys: {'release-2026-a': keys.publicKey.export({type: 'spki', format: 'pem'}).toString()},
    });
    expect(config).toMatchObject({
      enabled: true,
      manifestUrl: 'https://updates.oceanpartner.example/manifests/stable.json',
      feedBaseUrl: 'https://updates.oceanpartner.example/releases/',
    });
    if (config.enabled) {
      const trustedKey = config.trustedKeys['release-2026-a'];
      expect(typeof trustedKey).toBe('object');
      if (trustedKey && typeof trustedKey !== 'string') expect(trustedKey.asymmetricKeyType).toBe('ed25519');
    }
  });

  it('rejects clear-text endpoints, credentials, and non-Ed25519 keys', () => {
    const base = {
      schema_version: DESKTOP_UPDATE_CONFIG_SCHEMA,
      enabled: true,
      manifest_url: 'https://updates.oceanpartner.example/manifests/stable.json',
      feed_url: 'https://updates.oceanpartner.example/releases/',
      trusted_keys: {'release-2026-a': keys.publicKey.export({type: 'spki', format: 'pem'}).toString()},
    };
    expect(() => parseDesktopUpdateConfig({...base, manifest_url: 'http://updates.oceanpartner.example/manifest.json'})).toThrow('HTTPS');
    expect(() => parseDesktopUpdateConfig({...base, feed_url: 'https://key@updates.oceanpartner.example/releases/'})).toThrow('credentials');
    const rsa = generateKeyPairSync('rsa', {modulusLength: 2048});
    expect(() => parseDesktopUpdateConfig({...base, trusted_keys: {'release-2026-a': rsa.publicKey.export({type: 'spki', format: 'pem'}).toString()}})).toThrow('Ed25519');
  });
});
