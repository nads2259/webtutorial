import { color, motion, size, space, type as typeTokens } from "@northstar/design-tokens";
import { Link, VisuallyHidden } from "@northstar/ui-primitives";
import type { CSSProperties } from "react";
import { useEffect, useRef } from "react";
import { SimulationRunner } from "./SimulationRunner";
import { referenceSimulation } from "./simulation/reference-simulation";

/**
 * Simulation route: an accessible learner runner for the reference simulation
 * view-model (EVAL-SIM-004). Provides banner/navigation/main/contentinfo
 * landmarks and a single <h1>, then hands the interactive task to
 * {@link SimulationRunner}. No real runtime executes here (IMPL-016 owns that).
 */

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
  maxWidth: 820,
  margin: "0 auto",
  padding: space.lg,
  boxSizing: "border-box",
};

export function SimulationPage(): React.JSX.Element {
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    document.title = "Simulation runner — Northstar Knowledge";
    mainRef.current?.focus();
  }, []);

  return (
    <div style={shellStyle}>
      <a href="#simulation-main" style={skipLinkStyle} data-testid="skip-link">
        Skip to simulation
      </a>
      <header>
        <nav aria-label="Primary">
          <ul>
            <li>
              <Link href="#/">Read</Link>
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
      <main
        id="simulation-main"
        ref={mainRef}
        tabIndex={-1}
        style={contentStyle}
        aria-labelledby="simulation-title"
      >
        <h1 id="simulation-title">Simulation runner</h1>
        <p>
          Work through the reference incident-response drill. Every control is keyboard operable and
          a text-only equivalent of the whole scenario is available.
        </p>
        <SimulationRunner definition={referenceSimulation} />
      </main>
      <footer>
        <p>
          <VisuallyHidden>Footer: </VisuallyHidden>
          Northstar simulation environment.
        </p>
      </footer>
    </div>
  );
}
