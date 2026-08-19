import { Link } from "@northstar/ui-primitives";

export interface NavItem {
  label: string;
  href: string;
  /** Marks the current route for `aria-current` + active styling. */
  current?: boolean;
}

export interface SiteHeaderProps {
  items: readonly NavItem[];
  /** Optional primary call-to-action shown at the end of the bar. */
  cta?: { label: string; href: string };
}

/**
 * Branded top bar: the `banner` landmark with the Bestinfopages wordmark and the
 * `Primary` navigation. Semantics (landmark role, nav label, link roles) are
 * preserved exactly; only presentation is added.
 */
export function SiteHeader({ items, cta }: SiteHeaderProps): React.JSX.Element {
  return (
    <header className="ns-header">
      <div className="ns-header__inner">
        <a className="bip-wordmark" href="#/">
          Bestinfopages
        </a>
        <div className="ns-header__nav">
          <nav aria-label="Primary">
            <ul>
              {items.map((item) => (
                <li key={item.href + item.label}>
                  <Link href={item.href} aria-current={item.current ? "page" : undefined}>
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          {cta ? (
            <Link className="bip-cta" href={cta.href}>
              {cta.label}
            </Link>
          ) : null}
        </div>
      </div>
    </header>
  );
}
