import { color, motion, size, space, type as typeTokens } from "@northstar/design-tokens";
import { Link, Status, VisuallyHidden } from "@northstar/ui-primitives";
import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { AuthoringPage } from "./AuthoringPage";
import { DocumentRenderer } from "./DocumentRenderer";
import { SimulationPage } from "./SimulationPage";
import { reducedMotionCss } from "./a11y/reduced-motion";
import { sampleDocument } from "./fixtures/sample-document";

type Route = "reader" | "authoring" | "simulation";

function routeFromHash(): Route {
  if (typeof window === "undefined") {
    return "reader";
  }
  switch (window.location.hash) {
    case "#/authoring":
      return "authoring";
    case "#/simulation":
      return "simulation";
    default:
      return "reader";
  }
}

const skipLinkStyle: CSSProperties = {
  position: "absolute",
  left: space.sm,
  top: space.sm,
  padding: space.sm,
  background: color.surface,
  color: color.primary,
  border: `${size.focusRingWidthPx}px solid ${color.focusRing}`,
  borderRadius: 4,
  transform: "translateY(-200%)",
  transition: `transform ${motion.durationFastMs}ms ${motion.easingStandard}`,
  zIndex: 10,
};

const shellStyle: CSSProperties = {
  fontFamily: typeTokens.fontFamily,
  fontSize: typeTokens.baseSizePx,
  lineHeight: typeTokens.lineHeight,
  color: color.text,
  background: color.surface,
  minHeight: "100vh",
};

const contentStyle: CSSProperties = {
  maxWidth: 720,
  margin: "0 auto",
  padding: space.lg,
};

function ReaderView(): React.JSX.Element {
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    document.title = `${sampleDocument.title} — Northstar Knowledge`;
    mainRef.current?.focus();
  }, []);

  return (
    <div style={shellStyle}>
      <a href="#main-content" style={skipLinkStyle} data-testid="skip-link">
        Skip to main content
      </a>
      <header>
        <nav aria-label="Primary">
          <ul>
            <li>
              <Link href="/learn">Learn</Link>
            </li>
            <li>
              <Link href="/library">Library</Link>
            </li>
            <li>
              <Link href="#/authoring">Author</Link>
            </li>
            <li>
              <Link href="#/simulation">Simulation</Link>
            </li>
          </ul>
        </nav>
      </header>
      <main id="main-content" ref={mainRef} tabIndex={-1} style={contentStyle}>
        <Status>Published knowledge document loaded.</Status>
        <DocumentRenderer document={sampleDocument} />
      </main>
      <footer>
        <p>
          <VisuallyHidden>Footer: </VisuallyHidden>
          Northstar knowledge environment.
        </p>
      </footer>
    </div>
  );
}

export function App(): React.JSX.Element {
  const [route, setRoute] = useState<Route>(routeFromHash);

  useEffect(() => {
    const onHashChange = (): void => setRoute(routeFromHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const view = ((): React.JSX.Element => {
    switch (route) {
      case "authoring":
        return <AuthoringPage />;
      case "simulation":
        return <SimulationPage />;
      default:
        return <ReaderView />;
    }
  })();

  return (
    <>
      <style>{reducedMotionCss}</style>
      {view}
    </>
  );
}
