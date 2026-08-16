import type { ReactNode } from "react";

export interface StatusProps {
  children: ReactNode;
  /** `polite` (default) for non-urgent updates, `assertive` for urgent ones. */
  urgency?: "polite" | "assertive";
}

/**
 * Live-region status message (WCAG 4.1.3, Status Messages).
 * Announces async/state changes to assistive technology without moving focus.
 */
export function Status({ children, urgency = "polite" }: StatusProps): React.JSX.Element {
  // <output> has an implicit ARIA role of "status"; aria-live sets the announcement urgency.
  return (
    <output aria-live={urgency} aria-atomic="true">
      {children}
    </output>
  );
}
