import {useRef} from 'react';

export function DockResizeHandle({
  height,
  minimum,
  maximum,
  onHeightChange,
}: {
  height: number;
  minimum: number;
  maximum: number;
  onHeightChange: (height: number) => void;
}): React.JSX.Element {
  const drag = useRef<{pointerId: number; startY: number; startHeight: number} | null>(null);
  const finishDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };
  return <div
    className="dock-resize-handle"
    role="separator"
    aria-label="Resize linked visualization dock"
    aria-orientation="horizontal"
    aria-valuemin={minimum}
    aria-valuemax={maximum}
    aria-valuenow={height}
    tabIndex={0}
    onPointerDown={(event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      drag.current = {pointerId: event.pointerId, startY: event.clientY, startHeight: height};
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={(event) => {
      const active = drag.current;
      if (!active || active.pointerId !== event.pointerId) return;
      onHeightChange(active.startHeight + active.startY - event.clientY);
    }}
    onPointerUp={finishDrag}
    onPointerCancel={finishDrag}
    onLostPointerCapture={() => { drag.current = null; }}
    onKeyDown={(event) => {
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        onHeightChange(height + 24);
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        onHeightChange(height - 24);
      } else if (event.key === 'Home') {
        event.preventDefault();
        onHeightChange(minimum);
      } else if (event.key === 'End') {
        event.preventDefault();
        onHeightChange(maximum);
      }
    }}
  ><span /></div>;
}

export function SpatialPaneResizeHandle({
  width,
  minimum,
  maximum,
  onWidthChange,
}: {
  width: number;
  minimum: number;
  maximum: number;
  onWidthChange: (width: number) => void;
}): React.JSX.Element {
  const drag = useRef<{pointerId: number; startX: number; startWidth: number} | null>(null);
  const finishDrag = (event: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };
  return <div
    className="spatial-pane-resize-handle"
    role="separator"
    aria-label="Resize spatial workbench"
    aria-orientation="vertical"
    aria-valuemin={minimum}
    aria-valuemax={maximum}
    aria-valuenow={width}
    aria-valuetext={width + ' pixels'}
    tabIndex={0}
    onPointerDown={(event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      drag.current = {pointerId: event.pointerId, startX: event.clientX, startWidth: width};
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={(event) => {
      const active = drag.current;
      if (!active || active.pointerId !== event.pointerId) return;
      onWidthChange(active.startWidth + active.startX - event.clientX);
    }}
    onPointerUp={finishDrag}
    onPointerCancel={finishDrag}
    onLostPointerCapture={() => { drag.current = null; }}
    onKeyDown={(event) => {
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        onWidthChange(width + 32);
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        onWidthChange(width - 32);
      } else if (event.key === 'Home') {
        event.preventDefault();
        onWidthChange(minimum);
      } else if (event.key === 'End') {
        event.preventDefault();
        onWidthChange(maximum);
      }
    }}
  ><span /></div>;
}
