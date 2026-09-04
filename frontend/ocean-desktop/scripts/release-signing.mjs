const macRequiredVariables = [
  'CSC_LINK',
  'CSC_KEY_PASSWORD',
  'APPLE_ID',
  'APPLE_APP_SPECIFIC_PASSWORD',
  'APPLE_TEAM_ID',
];

const windowsRequiredVariables = [
  'WIN_CSC_LINK',
  'WIN_CSC_KEY_PASSWORD',
];

function missingVariables(required, environment) {
  return required.filter((name) => typeof environment[name] !== 'string' || !environment[name].trim());
}

export function releaseSigningRequirements(platform) {
  if (platform === 'darwin') return [...macRequiredVariables];
  if (platform === 'win32') return [...windowsRequiredVariables];
  throw new Error(`Signed Ocean Desktop packaging supports macOS and Windows only; received ${platform}.`);
}

export function assertReleaseSigningEnvironment(platform, environment = process.env) {
  const required = releaseSigningRequirements(platform);
  const missing = missingVariables(required, environment);
  if (missing.length) {
    const target = platform === 'darwin' ? 'macOS signing and notarization' : 'Windows Authenticode signing';
    throw new Error(`Missing required ${target} environment variables: ${missing.join(', ')}.`);
  }
  return {platform, required};
}

export function signedElectronBuilderArguments(platform, environment = process.env) {
  assertReleaseSigningEnvironment(platform, environment);
  // electron-builder normally skips signing when no identity is found. A release
  // build must instead fail before an unsigned artifact can escape CI.
  return ['-c.forceCodeSigning=true'];
}
