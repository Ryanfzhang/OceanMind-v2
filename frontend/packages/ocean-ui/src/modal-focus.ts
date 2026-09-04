const modalDialogSelector = '[role="dialog"][aria-modal="true"]';
const dialogFocusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function isVisibleElement(element: HTMLElement): boolean {
  const view = element.ownerDocument.defaultView;
  if (!view) return false;
  const style = view.getComputedStyle(element);
  const bounds = element.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' && bounds.width > 0 && bounds.height > 0;
}

function dialogFocusableElements(dialog: HTMLElement): HTMLElement[] {
  return Array.from(dialog.querySelectorAll<HTMLElement>(dialogFocusableSelector))
    .filter((element) => isVisibleElement(element) && !element.hasAttribute('inert'));
}

function focusDialogTarget(dialog: HTMLElement, preferLast = false): void {
  const view = dialog.ownerDocument.defaultView;
  const focused = dialog.ownerDocument.activeElement;
  if (view && focused instanceof view.HTMLElement && dialog.contains(focused)) return;
  const preferred = dialog.querySelector<HTMLElement>('[data-dialog-initial-focus]');
  const focusable = dialogFocusableElements(dialog);
  (preferred ?? focusable[preferLast ? focusable.length - 1 : 0] ?? dialog).focus({preventScroll: true});
}

/**
 * Keep the top-most modal dialog keyboard-contained and restore its opener's focus on dismissal.
 */
export function installModalFocusManager(ownerDocument: Document): () => void {
  const view = ownerDocument.defaultView;
  if (!view || !ownerDocument.body) return () => {};
  const schedule = view.queueMicrotask?.bind(view) ?? queueMicrotask;
  let activeDialog: HTMLElement | null = null;
  let restoreTarget: HTMLElement | null = null;

  const currentDialog = () => Array.from(ownerDocument.querySelectorAll<HTMLElement>(modalDialogSelector))
    .filter(isVisibleElement)
    .at(-1) ?? null;
  const restoreFocus = () => {
    const target = restoreTarget;
    restoreTarget = null;
    if (target?.isConnected && isVisibleElement(target)) target.focus({preventScroll: true});
  };
  const sync = () => {
    const nextDialog = currentDialog();
    if (nextDialog === activeDialog) return;
    if (!nextDialog) {
      activeDialog = null;
      schedule(restoreFocus);
      return;
    }
    if (!activeDialog && ownerDocument.activeElement instanceof view.HTMLElement) {
      restoreTarget = ownerDocument.activeElement;
    }
    activeDialog = nextDialog;
    schedule(() => {
      if (activeDialog === nextDialog) focusDialogTarget(nextDialog);
    });
  };
  const onFocusIn = (event: FocusEvent) => {
    const dialog = activeDialog;
    if (!dialog || event.target instanceof view.Node && dialog.contains(event.target)) return;
    schedule(() => {
      if (activeDialog === dialog) focusDialogTarget(dialog);
    });
  };
  const onKeyDown = (event: KeyboardEvent) => {
    const dialog = activeDialog;
    if (!dialog) return;
    if (event.key === 'Escape') {
      const dismiss = dialog.querySelector<HTMLButtonElement>(
        '[data-dialog-dismiss]:not([disabled]), button[aria-label^="Close"]:not([disabled]), button[aria-label^="Cancel"]:not([disabled])',
      );
      if (!dismiss) return;
      event.preventDefault();
      event.stopPropagation();
      dismiss.click();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = dialogFocusableElements(dialog);
    if (!focusable.length) {
      event.preventDefault();
      dialog.focus({preventScroll: true});
      return;
    }
    const focused = ownerDocument.activeElement instanceof view.HTMLElement ? ownerDocument.activeElement : null;
    const index = focused ? focusable.indexOf(focused) : -1;
    if (index === -1 || (event.shiftKey && index === 0) || (!event.shiftKey && index === focusable.length - 1)) {
      event.preventDefault();
      focusable[event.shiftKey ? focusable.length - 1 : 0]?.focus({preventScroll: true});
    }
  };

  const observer = new view.MutationObserver(sync);
  observer.observe(ownerDocument.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['aria-modal', 'class', 'hidden', 'role', 'style']});
  ownerDocument.addEventListener('focusin', onFocusIn, true);
  ownerDocument.addEventListener('keydown', onKeyDown, true);
  sync();
  return () => {
    observer.disconnect();
    ownerDocument.removeEventListener('focusin', onFocusIn, true);
    ownerDocument.removeEventListener('keydown', onKeyDown, true);
  };
}
