import type { AnchorHTMLAttributes, ReactNode } from "react";

export interface LinkProps extends AnchorHTMLAttributes<HTMLAnchorElement> {
  href: string;
  children: ReactNode;
}

/**
 * Accessible link primitive. Requires an `href` and visible content so the
 * link purpose is clear (WCAG 2.4.4). External links that open in a new tab
 * get safe `rel` and an announced hint (2.4.4 / 4.1.2).
 */
export function Link({ href, target, rel, children, ...rest }: LinkProps): React.JSX.Element {
  const isNewTab = target === "_blank";
  const safeRel = isNewTab ? [rel, "noopener", "noreferrer"].filter(Boolean).join(" ") : rel;
  return (
    <a href={href} target={target} rel={safeRel} {...rest}>
      {children}
      {isNewTab ? <span> (opens in a new tab)</span> : null}
    </a>
  );
}
