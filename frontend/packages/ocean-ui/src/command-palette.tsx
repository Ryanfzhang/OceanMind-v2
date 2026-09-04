import {useEffect, useRef, useState} from 'react';
import {Command, Search, X} from 'lucide-react';

import {filterCommandActions} from './command-filter.js';

export function CommandPalette({
  open,
  onClose,
  actions,
}: {
  open: boolean;
  onClose: () => void;
  actions: Array<{id: string; label: string; shortcut?: string; disabled?: boolean; run: () => void}>;
}): React.JSX.Element | null {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (open) setQuery('');
  }, [open]);
  if (!open) return null;
  const visible = filterCommandActions(actions, query);
  // The filter input stays out of the Tab order (the contract's focus cycle is
  // unchanged); arrow keys cycle through it and the enabled action buttons.
  const navigationTargets = () => {
    const buttons = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('button:not([disabled])') ?? []);
    return inputRef.current ? [inputRef.current, ...buttons] : buttons;
  };
  const moveFocus = (delta: number) => {
    const targets = navigationTargets();
    if (!targets.length) return;
    const focused = document.activeElement;
    const index = focused instanceof HTMLElement ? targets.indexOf(focused as HTMLButtonElement) : -1;
    const next = index < 0
      ? (delta > 0 ? 0 : targets.length - 1)
      : (index + delta + targets.length) % targets.length;
    targets[next]?.focus();
  };
  const onSearchKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); moveFocus(1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); moveFocus(-1); }
    else if (event.key === 'Enter') {
      event.preventDefault();
      const first = visible.find((action) => !action.disabled);
      if (first) { first.run(); onClose(); }
    }
  };
  const onListKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'ArrowDown') { event.preventDefault(); moveFocus(1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); moveFocus(-1); }
  };
  return <div className="command-palette-backdrop" role="presentation"><section className="command-palette" role="dialog" aria-modal="true" aria-label="Commands"><header><Command size={16} /><strong>Commands</strong><button onClick={onClose} title="Close commands" aria-label="Close commands"><X size={15} /></button></header><label className="command-palette-search"><Search size={13} /><input ref={inputRef} tabIndex={-1} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={onSearchKeyDown} placeholder="Filter commands" aria-label="Filter commands" /></label><div ref={listRef} onKeyDown={onListKeyDown}>{visible.map((action) => <button key={action.id} disabled={action.disabled} onClick={(event) => {event.stopPropagation(); action.run(); onClose();}}><span>{action.label}</span>{action.shortcut ? <kbd>{action.shortcut}</kbd> : null}</button>)}{!visible.length ? <p className="command-palette-empty">No matching commands</p> : null}</div></section></div>;
}
