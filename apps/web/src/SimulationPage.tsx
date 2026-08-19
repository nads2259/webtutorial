import { useEffect, useRef } from "react";
import { SimulationRunner } from "./SimulationRunner";
import { SiteFooter } from "./components/SiteFooter";
import { SiteHeader } from "./components/SiteHeader";
import { SkipLink } from "./components/SkipLink";
import { referenceSimulation } from "./simulation/reference-simulation";

/**
 * Simulation route: an accessible learner runner for the reference simulation
 * view-model (EVAL-SIM-004). Provides banner/navigation/main/contentinfo
 * landmarks and a single <h1>, then hands the interactive task to
 * {@link SimulationRunner}. No real runtime executes here (IMPL-016 owns that).
 */

export function SimulationPage(): React.JSX.Element {
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    document.title = "Simulation runner — Bestinfopages";
    mainRef.current?.focus();
  }, []);

  return (
    <div className="ns-shell">
      <SkipLink target="simulation-main">Skip to simulation</SkipLink>
      <SiteHeader
        items={[
          { label: "Read", href: "#/" },
          { label: "Author", href: "#/authoring" },
          { label: "Simulation", href: "#/simulation", current: true },
        ]}
        cta={{ label: "Get started", href: "#/authoring" }}
      />
      <main
        id="simulation-main"
        ref={mainRef}
        tabIndex={-1}
        className="ns-main ns-main--wide"
        aria-labelledby="simulation-title"
      >
        <div className="bip-kicker">
          <span className="bip-chip bip-chip--sim">Interactive drill</span>
          <span className="bip-meta-sep" aria-hidden="true" />
          <span>Incident response</span>
        </div>
        <h1 id="simulation-title">Simulation runner</h1>
        <p>
          Work through the reference incident-response drill. Every control is keyboard operable and
          a text-only equivalent of the whole scenario is available.
        </p>
        <div className="ns-panel">
          <SimulationRunner definition={referenceSimulation} />
        </div>
      </main>
      <SiteFooter>
        Bestinfopages interactive simulations — keyboard-operable drills with a full text-only
        equivalent.
      </SiteFooter>
    </div>
  );
}
