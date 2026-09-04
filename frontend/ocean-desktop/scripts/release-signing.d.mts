export function releaseSigningRequirements(platform: string): string[];

export function assertReleaseSigningEnvironment(
  platform: string,
  environment?: Record<string, string | undefined>,
): {platform: string; required: string[]};

export function signedElectronBuilderArguments(
  platform: string,
  environment?: Record<string, string | undefined>,
): string[];
