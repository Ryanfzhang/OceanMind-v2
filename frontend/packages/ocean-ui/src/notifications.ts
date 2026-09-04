export type NotificationLevel = 'info' | 'warning' | 'error';

export type AppNotification = {
  id: number;
  level: NotificationLevel;
  message: string;
};

export const NOTIFICATION_STACK_LIMIT = 4;

/** Auto-dismiss delay per level; errors stay until dismissed manually. */
export const NOTIFICATION_DISMISS_MS: Record<NotificationLevel, number | null> = {
  info: 6_000,
  warning: 9_000,
  error: null,
};

const ERROR_PATTERN = /could not|invalid|unavailable|withheld|denied|incomplete|exited|does not|different|without|did not complete/i;
const WARNING_PATTERN = /did not finish|recovered|was reset|reloading|sequence gap/i;

/**
 * Legacy setDiagnostic call sites carry no severity. Classify the message text
 * so genuine failures persist as errors while progress notes fade as info.
 */
export function diagnosticLevelFor(message: string): NotificationLevel {
  if (ERROR_PATTERN.test(message)) return 'error';
  if (WARNING_PATTERN.test(message)) return 'warning';
  return 'info';
}

/**
 * Append a notification, dropping the oldest beyond the stack limit. A repeat
 * of the newest message is ignored so retry loops do not flood the stack.
 */
export function pushNotification(stack: AppNotification[], notification: AppNotification): AppNotification[] {
  const last = stack.at(-1);
  if (last && last.message === notification.message && last.level === notification.level) return stack;
  return [...stack, notification].slice(-NOTIFICATION_STACK_LIMIT);
}

export function dismissNotification(stack: AppNotification[], id: number): AppNotification[] {
  return stack.filter((notification) => notification.id !== id);
}
