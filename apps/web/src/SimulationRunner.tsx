import { color, size, space } from "@northstar/design-tokens";
import { Status } from "@northstar/ui-primitives";
import type { CSSProperties } from "react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  type SimulationDefinition,
  type SimulationState,
  announcementFor,
  chooseAction,
  createInitialState,
  getOutcome,
  getStep,
  renderTextEquivalent,
  stepPosition,
} from "./simulation/model";
import { type ShortcutBinding, simulationShortcuts } from "./simulation/reference-simulation";

/**
 * Accessible simulation runner (EVAL-SIM-004, FR-SIM-002 / NFR-A11Y-001).
 *
 * Renders the reference simulation view-model as a keyboard- and
 * screen-reader-operable state machine:
 * - semantic region with a name/description and heading structure (WCAG 1.3.1);
 * - every control is a real <button> operable with Tab + Enter/Space, with a
 *   visible focus ring and a logical focus order, and no keyboard trap
 *   (2.1.1 / 2.1.2 / 2.4.3 / 2.4.7);
 * - state transitions are announced through a polite live region (4.1.3) and
 *   focus is moved to the new step/outcome heading (focus management);
 * - Alt-modified shortcuts (not single keys, so 2.1.4 is satisfied);
 * - a text-only equivalent of the whole state/outcome (the documented
 *   equivalent accommodation);
 * - relative units / token spacing and wrapping text so it reflows at 400% zoom
 *   / 320px with no horizontal scrolling (1.4.10).
 */

const regionStyle: CSSProperties = {
  maxWidth: "100%",
  boxSizing: "border-box",
};

const toolbarStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: space.sm,
  margin: `${space.md}px 0`,
};

const choiceListStyle: CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  display: "flex",
  flexDirection: "column",
  gap: space.sm,
};

const stageStyle: CSSProperties = {
  marginTop: space.md,
  padding: space.md,
  border: `1px solid ${color.border}`,
  borderRadius: 4,
  background: color.surfaceMuted,
  maxWidth: "100%",
  boxSizing: "border-box",
};

const preStyle: CSSProperties = {
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
  wordBreak: "break-word",
  maxWidth: "100%",
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  margin: 0,
};

/**
 * :focus-visible and target-size rules cannot be expressed as inline styles, so
 * they are scoped to `.ns-sim` here (theme-safe presentation only — no DOM or
 * focus-order change). Values come from accessibility design tokens.
 */
const scopedCss = `
.ns-sim :focus-visible {
  outline: ${size.focusRingWidthPx}px solid ${color.focusRing};
  outline-offset: 2px;
}
.ns-sim button {
  min-height: ${size.minTargetPx}px;
  min-width: ${size.minTargetPx}px;
  padding: ${space.xs}px ${space.sm}px;
}
`;

export interface SimulationRunnerProps {
  definition: SimulationDefinition;
  shortcuts?: readonly ShortcutBinding[];
}

export function SimulationRunner({
  definition,
  shortcuts = simulationShortcuts,
}: SimulationRunnerProps): React.JSX.Element {
  const [state, setState] = useState<SimulationState>(() => createInitialState(definition));
  const [announcement, setAnnouncement] = useState<string>("");
  const [showText, setShowText] = useState<boolean>(false);

  const regionRef = useRef<HTMLElement>(null);
  const stageHeadingRef = useRef<HTMLHeadingElement>(null);
  // Only move focus after a learner-initiated transition, never on first mount
  // (the page shell owns initial focus).
  const shouldFocusStage = useRef<boolean>(false);

  const titleId = useId();
  const objectiveId = useId();
  const textId = useId();
  const shortcutsId = useId();
  const stepHeadingId = useId();

  const restart = useCallback(() => {
    const next = createInitialState(definition);
    shouldFocusStage.current = true;
    setState(next);
    setAnnouncement(
      `Simulation restarted. Step 1 of ${definition.steps.length}: ${getStep(definition, definition.startStepId).title}.`,
    );
  }, [definition]);

  const choose = useCallback(
    (choiceId: string) => {
      setState((current) => {
        const next = chooseAction(definition, current, choiceId);
        shouldFocusStage.current = true;
        setAnnouncement(announcementFor(definition, next));
        return next;
      });
    },
    [definition],
  );

  const toggleText = useCallback(() => setShowText((value) => !value), []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: focus must be moved after every state transition; `state` is the intentional trigger.
  useEffect(() => {
    if (shouldFocusStage.current) {
      shouldFocusStage.current = false;
      stageHeadingRef.current?.focus();
    }
  }, [state]);

  // Shortcuts are registered as a native listener (not a JSX handler) so they
  // never appear as a click-less interactive element. Alt is required, so the
  // bindings do not intercept AT or typing keystrokes (WCAG 2.1.4).
  useEffect(() => {
    const node = regionRef.current;
    if (!node) {
      return;
    }
    const handler = (event: KeyboardEvent): void => {
      if (!event.altKey) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === "s") {
        event.preventDefault();
        stageHeadingRef.current?.focus();
      } else if (key === "t") {
        event.preventDefault();
        setShowText((value) => !value);
      } else if (key === "r") {
        event.preventDefault();
        restart();
      }
    };
    node.addEventListener("keydown", handler);
    return () => node.removeEventListener("keydown", handler);
  }, [restart]);

  const finished = state.currentStepId === null && state.outcomeId !== null;
  const textEquivalent = renderTextEquivalent(definition, state);

  return (
    <section
      ref={regionRef}
      className="ns-sim"
      style={regionStyle}
      aria-labelledby={titleId}
      aria-describedby={objectiveId}
    >
      <style>{scopedCss}</style>
      <h2 id={titleId}>{definition.title}</h2>
      <p id={objectiveId}>
        <strong>Objective: </strong>
        {definition.objective}
      </p>
      <p>{definition.instructions}</p>

      <Status>{announcement}</Status>

      <div role="toolbar" aria-label="Simulation controls" style={toolbarStyle}>
        <button type="button" onClick={restart}>
          Restart simulation
        </button>
        <button type="button" onClick={toggleText} aria-expanded={showText} aria-controls={textId}>
          {showText ? "Hide text-only equivalent" : "Show text-only equivalent"}
        </button>
      </div>

      <div style={stageStyle}>
        {finished && state.outcomeId !== null
          ? (() => {
              const outcome = getOutcome(definition, state.outcomeId);
              return (
                <div>
                  <h3 id={stepHeadingId} ref={stageHeadingRef} tabIndex={-1}>
                    Outcome: {outcome.title}
                  </h3>
                  <p>{outcome.summary}</p>
                  <p>
                    {/* Non-color status cue (WCAG 1.4.1): the result is stated in text. */}
                    <strong>Result: </strong>
                    {outcome.status}
                  </p>
                </div>
              );
            })()
          : (() => {
              const step = getStep(definition, state.currentStepId ?? definition.startStepId);
              const position = stepPosition(definition, state);
              return (
                <div>
                  <h3 id={stepHeadingId} ref={stageHeadingRef} tabIndex={-1}>
                    Step {position} of {definition.steps.length}: {step.title}
                  </h3>
                  <p>{step.prompt}</p>
                  <ul style={choiceListStyle}>
                    {step.choices.map((choice) => (
                      <li key={choice.id}>
                        <button type="button" onClick={() => choose(choice.id)}>
                          {choice.label}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })()}
      </div>

      <section id={textId} aria-label="Text-only equivalent" hidden={!showText}>
        <h3>Text-only equivalent</h3>
        <p>A non-visual, linear equivalent of the full simulation state and outcome.</p>
        <pre style={preStyle}>{textEquivalent}</pre>
      </section>

      <section aria-labelledby={shortcutsId}>
        <h3 id={shortcutsId}>Keyboard shortcuts</h3>
        <dl>
          {shortcuts.map((shortcut) => (
            <div key={shortcut.keys}>
              <dt>{shortcut.keys}</dt>
              <dd>{shortcut.description}</dd>
            </div>
          ))}
        </dl>
      </section>
    </section>
  );
}
