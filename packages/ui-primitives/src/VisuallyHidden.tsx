import type { CSSProperties, ReactNode } from "react";

const visuallyHiddenStyle: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};

export interface VisuallyHiddenProps {
  children: ReactNode;
}

/**
 * Content available to assistive technology but hidden visually.
 * Supports WCAG 1.3.1 (Info and Relationships) and 2.4.4 (Link Purpose).
 */
export function VisuallyHidden({ children }: VisuallyHiddenProps): React.JSX.Element {
  return <span style={visuallyHiddenStyle}>{children}</span>;
}
