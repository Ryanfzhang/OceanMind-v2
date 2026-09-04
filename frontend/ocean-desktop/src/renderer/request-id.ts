export function requestId(prefix: string): string {
  return `req_desktop_${prefix}_${crypto.randomUUID().replaceAll('-', '')}`;
}
