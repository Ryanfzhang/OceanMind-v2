export function developmentPython(options?: {
  env?: Record<string, string | undefined>;
  platform?: string;
  exists?: (path: string) => boolean;
}): string;
