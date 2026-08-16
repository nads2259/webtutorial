import { describe, expect, it } from "vitest";
import {
  announcementFor,
  chooseAction,
  createInitialState,
  isFinished,
  renderTextEquivalent,
  stepPosition,
} from "./model";
import { referenceSimulation as sim } from "./reference-simulation";

describe("simulation state machine", () => {
  it("starts on the defined start step with an empty history", () => {
    const state = createInitialState(sim);
    expect(state.currentStepId).toBe(sim.startStepId);
    expect(state.outcomeId).toBeNull();
    expect(state.history).toHaveLength(0);
    expect(isFinished(state)).toBe(false);
    expect(stepPosition(sim, state)).toBe(1);
  });

  it("advances deterministically along the success path to a success outcome", () => {
    let state = createInitialState(sim);
    state = chooseAction(sim, state, "choice-detect-dashboards");
    expect(state.currentStepId).toBe("step-mitigate");
    state = chooseAction(sim, state, "choice-mitigate-rollback");
    expect(state.currentStepId).toBe("step-verify");
    state = chooseAction(sim, state, "choice-verify-slo");
    expect(isFinished(state)).toBe(true);
    expect(state.outcomeId).toBe("outcome-success");
    expect(state.history).toHaveLength(3);
    expect(state.history.every((entry) => entry.correct)).toBe(true);
  });

  it("reaches the partial outcome when recovery is not verified", () => {
    let state = createInitialState(sim);
    state = chooseAction(sim, state, "choice-detect-dashboards");
    state = chooseAction(sim, state, "choice-mitigate-rollback");
    state = chooseAction(sim, state, "choice-verify-close");
    expect(state.outcomeId).toBe("outcome-partial");
  });

  it("does not mutate the previous state (immutability)", () => {
    const initial = createInitialState(sim);
    const next = chooseAction(sim, initial, "choice-detect-dashboards");
    expect(initial.history).toHaveLength(0);
    expect(next.history).toHaveLength(1);
    expect(next).not.toBe(initial);
  });

  it("rejects an unknown choice and choosing after completion", () => {
    let state = createInitialState(sim);
    expect(() => chooseAction(sim, state, "does-not-exist")).toThrow(/Unknown choice/);
    state = chooseAction(sim, state, "choice-detect-dashboards");
    state = chooseAction(sim, state, "choice-mitigate-rollback");
    state = chooseAction(sim, state, "choice-verify-slo");
    expect(() => chooseAction(sim, state, "choice-detect-dashboards")).toThrow(/already finished/);
  });

  it("builds a polite announcement with feedback plus the new location", () => {
    let state = createInitialState(sim);
    state = chooseAction(sim, state, "choice-detect-dashboards");
    const announcement = announcementFor(sim, state);
    expect(announcement).toContain("CPU is saturated");
    expect(announcement).toContain("step 2 of 3");
    expect(announcement).toContain("Choose a mitigation");
  });

  it("announces completion and the outcome at the terminal state", () => {
    let state = createInitialState(sim);
    state = chooseAction(sim, state, "choice-detect-dashboards");
    state = chooseAction(sim, state, "choice-mitigate-rollback");
    state = chooseAction(sim, state, "choice-verify-slo");
    const announcement = announcementFor(sim, state);
    expect(announcement).toContain("Simulation complete");
    expect(announcement).toContain("Incident resolved cleanly");
  });

  it("renders a non-visual text equivalent covering objective, prompt and actions", () => {
    const text = renderTextEquivalent(sim, createInitialState(sim));
    expect(text).toContain(`Objective: ${sim.objective}`);
    expect(text).toContain("Current step 1 of 3: Detect and confirm");
    expect(text).toContain("Available actions:");
    expect(text).toContain("Confirm the alert");
  });

  it("renders the outcome and the decision transcript in the text equivalent", () => {
    let state = createInitialState(sim);
    state = chooseAction(sim, state, "choice-detect-dashboards");
    state = chooseAction(sim, state, "choice-mitigate-rollback");
    state = chooseAction(sim, state, "choice-verify-slo");
    const text = renderTextEquivalent(sim, state);
    expect(text).toContain("Decisions so far:");
    expect(text).toContain("Result: Incident resolved cleanly (success)");
  });
});
