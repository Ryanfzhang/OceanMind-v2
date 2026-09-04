export type PackageVerificationAttestation = {
  schema_version: 'ocean-desktop-package-verification/v1';
  platform: string;
  architecture: string;
  backend_schema: string;
  sandbox: unknown;
  sandbox_self_check: unknown;
  scientific_runtime: unknown;
};

export function packageVerificationAttestation(input: {
  platform: string;
  architecture: string;
  backendSchema: string;
  sandbox: unknown;
  sandboxSelfCheck: unknown;
  scientificRuntime: unknown;
}): PackageVerificationAttestation;

export function writePackageVerificationAttestation(
  path: string,
  report: PackageVerificationAttestation,
  cwd?: string,
): string;

export function writeEvidenceOnce(path: string, report: unknown, cwd?: string): string;
