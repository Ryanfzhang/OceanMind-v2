import {useEffect} from 'react';
import {X} from 'lucide-react';

import {NOTIFICATION_DISMISS_MS, type AppNotification} from './notifications.js';

function NotificationItem({
  notification,
  onDismiss,
}: {
  notification: AppNotification;
  onDismiss: (id: number) => void;
}): React.JSX.Element {
  useEffect(() => {
    const duration = NOTIFICATION_DISMISS_MS[notification.level];
    if (duration === null) return;
    const timer = window.setTimeout(() => onDismiss(notification.id), duration);
    return () => window.clearTimeout(timer);
  }, [notification, onDismiss]);
  return <div className={'notification ' + notification.level} role={notification.level === 'error' ? 'alert' : 'status'}>
    <span>{notification.message}</span>
    <button type="button" onClick={() => onDismiss(notification.id)} title="Dismiss notification" aria-label="Dismiss notification"><X size={13} /></button>
  </div>;
}

export function NotificationStack({
  notifications,
  onDismiss,
}: {
  notifications: AppNotification[];
  onDismiss: (id: number) => void;
}): React.JSX.Element | null {
  if (!notifications.length) return null;
  return <div className="notification-stack" aria-label="Notifications">{notifications.map((notification) => <NotificationItem key={notification.id} notification={notification} onDismiss={onDismiss} />)}</div>;
}
