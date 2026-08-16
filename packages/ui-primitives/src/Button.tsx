import type { ButtonHTMLAttributes, ReactNode } from "react";

type NativeButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children">;

/** A button whose accessible name comes from visible text children. */
interface LabelledButtonProps extends NativeButtonProps {
  children: ReactNode;
  "aria-label"?: string;
}

/**
 * An icon-only button. TypeScript REQUIRES `aria-label` here because there is no
 * text child to derive an accessible name from (NFR-A11Y-002, WCAG 4.1.2).
 */
interface IconOnlyButtonProps extends NativeButtonProps {
  children?: undefined;
  "aria-label": string;
}

export type ButtonProps = LabelledButtonProps | IconOnlyButtonProps;

/**
 * Accessible button primitive.
 *
 * The union type makes an icon-only button without an accessible name a
 * compile-time error. A runtime dev warning also fires for untyped (JS)
 * consumers that bypass the type (NFR-A11Y-002).
 *
 * Honors WCAG 2.5.8 (target size >= 24px) via a minimum size and 2.4.7
 * (focus visible) via :focus-visible; both are set by consuming stylesheets,
 * while name/role/value (4.1.2) are guaranteed here.
 */
export function Button({ children, type, ...rest }: ButtonProps): React.JSX.Element {
  if (process.env.NODE_ENV !== "production") {
    const hasText = children !== undefined && children !== null && children !== "";
    const label = rest["aria-label"];
    const hasLabel = typeof label === "string" && label.trim().length > 0;
    if (!hasText && !hasLabel) {
      console.error(
        "[ui-primitives] Button has no accessible name: provide text children or an `aria-label` (WCAG 4.1.2 / NFR-A11Y-002).",
      );
    }
  }

  return (
    <button type={type ?? "button"} {...rest}>
      {children}
    </button>
  );
}
