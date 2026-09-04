import {useEffect, useRef, type ReactNode} from 'react';

export function ModalDialog({
  ariaLabel,
  className = '',
  onClose,
  children,
}: {
  ariaLabel: string;
  className?: string;
  onClose: () => void;
  children: ReactNode;
}): React.JSX.Element {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {closeRef.current = onClose;}, [onClose]);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      closeRef.current();
    };
    window.addEventListener('keydown', onKeyDown);
    dialogRef.current?.focus();
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      previousFocus?.focus();
    };
  }, []);

  return <div
    className="import-dialog-backdrop"
    data-modal-backdrop
    role="presentation"
    onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}
  >
    <section
      ref={dialogRef}
      className={`import-dialog${className ? ` ${className}` : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
      tabIndex={-1}
    >{children}</section>
  </div>;
}
