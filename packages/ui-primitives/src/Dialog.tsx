import type { ReactNode } from "react";
import { useCallback, useEffect, useId, useRef } from "react";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * Modal dialog with focus trap, focus restore and Escape-to-close.
 * Covers WCAG 2.1.1 (Keyboard), 2.1.2 (No Keyboard Trap — Tab cycles but Esc
 * always exits), 2.4.3 (Focus Order), 2.4.11 (Focus Not Obscured) and 4.1.2.
 */
export function Dialog({ open, onClose, title, children }: DialogProps): React.JSX.Element | null {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const titleId = useId();

  const getFocusable = useCallback((): HTMLElement[] => {
    const node = dialogRef.current;
    if (!node) {
      return [];
    }
    return Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    previouslyFocused.current = document.activeElement as HTMLElement | null;
    const focusable = getFocusable();
    (focusable[0] ?? dialogRef.current)?.focus();

    return () => {
      previouslyFocused.current?.focus();
    };
  }, [open, getFocusable]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab") {
        return;
      }
      const focusable = getFocusable();
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first?.focus();
      }
    },
    [onClose, getFocusable],
  );

  if (!open) {
    return null;
  }

  return (
    <div
      ref={dialogRef}
      // biome-ignore lint/a11y/useSemanticElements: role="dialog" with a managed focus trap is the intended ARIA pattern; native <dialog>.showModal() is out of scope for this shell.
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      tabIndex={-1}
      onKeyDown={handleKeyDown}
    >
      <h2 id={titleId}>{title}</h2>
      {children}
    </div>
  );
}
