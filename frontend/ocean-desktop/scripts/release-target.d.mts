export function assertSupportedArchitecture(architecture: string): 'x64' | 'arm64';

export function parseMachOArchitectures(description: string): Set<'x64' | 'arm64'>;

export function parseWindowsPeArchitecture(binary: Buffer): 'x64' | 'arm64';

export function sidecarArchitectures(
  platform: 'darwin' | 'win32',
  executable: string,
  dependencies?: {
    file?: (path: string) => string;
    readFile?: (path: string) => Buffer;
  },
): Set<'x64' | 'arm64'>;

export function assertReleaseTarget(input: {
  platform: string;
  nodeArchitecture: string;
  sidecarArchitectures: Set<'x64' | 'arm64'>;
}): {platform: string; architecture: 'x64' | 'arm64'};
