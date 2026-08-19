import { VisuallyHidden } from "@northstar/ui-primitives";

export interface SiteFooterProps {
  /** Short environment/tagline sentence (kept for continuity + screen readers). */
  children: React.ReactNode;
}

const columns: ReadonlyArray<{ heading: string; links: ReadonlyArray<string> }> = [
  { heading: "Product", links: ["Guides", "Library", "Authoring", "Simulations"] },
  { heading: "Resources", links: ["Getting started", "Accessibility", "Changelog", "Status"] },
  { heading: "Company", links: ["About", "Careers", "Privacy", "Contact"] },
];

/** The `contentinfo` landmark. */
export function SiteFooter({ children }: SiteFooterProps): React.JSX.Element {
  return (
    <footer className="ns-footer">
      <div className="ns-footer__inner">
        <div className="ns-footer__brand">
          <span className="bip-wordmark">Bestinfopages</span>
          <p>
            <VisuallyHidden>Footer: </VisuallyHidden>
            {children}
          </p>
        </div>
        {columns.map((col) => (
          <div key={col.heading}>
            <h2>{col.heading}</h2>
            <ul>
              {col.links.map((link) => (
                <li key={link}>
                  <a href="#/">{link}</a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="ns-footer__bar">© 2026 Bestinfopages. Built for accessible knowledge.</div>
    </footer>
  );
}
