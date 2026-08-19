export interface SkipLinkProps {
  /** The id (without `#`) of the main landmark to jump to. */
  target: string;
  children: React.ReactNode;
}

/**
 * Keyboard-only "skip to content" link. It is the first focusable element on
 * every page (WCAG 2.4.1 Bypass Blocks) and is revealed on focus via CSS.
 */
export function SkipLink({ target, children }: SkipLinkProps): React.JSX.Element {
  return (
    <a className="ns-skip" href={`#${target}`} data-testid="skip-link">
      {children}
    </a>
  );
}
