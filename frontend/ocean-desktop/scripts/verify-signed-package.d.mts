type FileFinder = (directory: string, matcher: (file: string) => boolean) => string[];

type MacSignedPackageLayout = {
  app: string;
  executables: string[];
  dmg: string;
  zip: string;
};

type WindowsSignedPackageLayout = {
  unpacked: string;
  executables: string[];
  installer: string;
};

export function signedPackageLayout(options: {
  platform: 'darwin' | 'win32';
  root?: string;
  name?: string;
  files?: FileFinder;
}): MacSignedPackageLayout | WindowsSignedPackageLayout;

export function hasDeveloperIdAuthority(output: string): boolean;

export function verifySignedPackage(options?: {
  platform?: 'darwin' | 'win32';
  root?: string;
  name?: string;
}): MacSignedPackageLayout | WindowsSignedPackageLayout;
