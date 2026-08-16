/**
 * Reference simulation view-model + a pure, deterministic state machine.
 *
 * This is the R0 "deterministic browser" reference shape from
 * spec/docs/15_simulations_and_labs.md (§3): a client-side state machine with a
 * versioned definition, scenario/objective, learner instructions, controls and a
 * terminal outcome. The real control-plane/runtime is IMPL-016 and out of scope
 * here — this module is a mock/reference view-model with NO network, secret or
 * code-execution capability (network/FS/secret access denied by default).
 *
 * All state transitions are pure functions so they can be unit-tested and are
 * equally consumable by the accessible visual path and the text-output path
 * (LAW-04: one capability, one implementation).
 */

/** A single learner-selectable action within a step. */
export interface SimulationChoice {
  readonly id: string;
  /** Visible, self-describing action label (WCAG 2.4.4 / 2.4.6). */
  readonly label: string;
  /** Longer, non-visual description used by the text-output equivalent. */
  readonly detail: string;
  /** Feedback announced after the action is taken. */
  readonly feedback: string;
  /** Target: a step id (`step-*`) or an outcome id (`outcome-*`). */
  readonly next: string;
  readonly correct: boolean;
}

/** A decision point in the scenario. */
export interface SimulationStep {
  readonly id: string;
  readonly title: string;
  readonly prompt: string;
  readonly choices: readonly SimulationChoice[];
}

export type OutcomeStatus = "success" | "partial" | "failure";

/** A terminal state of the scenario. */
export interface SimulationOutcome {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly status: OutcomeStatus;
}

/** The immutable, versioned reference simulation definition. */
export interface SimulationDefinition {
  readonly id: string;
  readonly version: string;
  readonly title: string;
  /** The critical learning task (objective) for this simulation. */
  readonly objective: string;
  readonly instructions: string;
  readonly startStepId: string;
  readonly steps: readonly SimulationStep[];
  readonly outcomes: readonly SimulationOutcome[];
}

/** One recorded decision, used for the transcript / text equivalent. */
export interface HistoryEntry {
  readonly stepId: string;
  readonly stepTitle: string;
  readonly choiceId: string;
  readonly choiceLabel: string;
  readonly feedback: string;
  readonly correct: boolean;
}

/** The current runner state. `currentStepId` is null once an outcome is reached. */
export interface SimulationState {
  readonly currentStepId: string | null;
  readonly outcomeId: string | null;
  readonly history: readonly HistoryEntry[];
}

function isOutcomeId(id: string): boolean {
  return id.startsWith("outcome-");
}

export function getStep(def: SimulationDefinition, stepId: string): SimulationStep {
  const step = def.steps.find((candidate) => candidate.id === stepId);
  if (!step) {
    throw new Error(`Unknown simulation step: ${stepId}`);
  }
  return step;
}

export function getOutcome(def: SimulationDefinition, outcomeId: string): SimulationOutcome {
  const outcome = def.outcomes.find((candidate) => candidate.id === outcomeId);
  if (!outcome) {
    throw new Error(`Unknown simulation outcome: ${outcomeId}`);
  }
  return outcome;
}

export function createInitialState(def: SimulationDefinition): SimulationState {
  return { currentStepId: def.startStepId, outcomeId: null, history: [] };
}

export function isFinished(state: SimulationState): boolean {
  return state.currentStepId === null && state.outcomeId !== null;
}

/** Total number of decision points; used for progress ("Step X of N"). */
export function stepCount(def: SimulationDefinition): number {
  return def.steps.length;
}

/** 1-based index of the current step, or the step count when finished. */
export function stepPosition(def: SimulationDefinition, state: SimulationState): number {
  if (state.currentStepId === null) {
    return def.steps.length;
  }
  const index = def.steps.findIndex((step) => step.id === state.currentStepId);
  return index < 0 ? 0 : index + 1;
}

/**
 * Apply a learner choice. Returns a NEW state (immutability). Throws if the
 * simulation is finished or the choice is not valid for the current step, so
 * invalid transitions cannot silently corrupt state.
 */
export function chooseAction(
  def: SimulationDefinition,
  state: SimulationState,
  choiceId: string,
): SimulationState {
  if (state.currentStepId === null) {
    throw new Error("Cannot choose an action: the simulation has already finished.");
  }
  const step = getStep(def, state.currentStepId);
  const choice = step.choices.find((candidate) => candidate.id === choiceId);
  if (!choice) {
    throw new Error(`Unknown choice "${choiceId}" for step "${step.id}".`);
  }

  const entry: HistoryEntry = {
    stepId: step.id,
    stepTitle: step.title,
    choiceId: choice.id,
    choiceLabel: choice.label,
    feedback: choice.feedback,
    correct: choice.correct,
  };
  const history = [...state.history, entry];

  if (isOutcomeId(choice.next)) {
    getOutcome(def, choice.next);
    return { currentStepId: null, outcomeId: choice.next, history };
  }
  getStep(def, choice.next);
  return { currentStepId: choice.next, outcomeId: null, history };
}

/**
 * Build the polite live-region announcement for the state that results from a
 * transition (WCAG 4.1.3 Status Messages). Announces the feedback for the
 * action taken plus the learner's new location in the scenario.
 */
export function announcementFor(
  def: SimulationDefinition,
  state: SimulationState,
): string {
  const last = state.history[state.history.length - 1];
  const feedback = last ? `${last.feedback} ` : "";
  if (state.outcomeId !== null) {
    const outcome = getOutcome(def, state.outcomeId);
    return `${feedback}Simulation complete. Outcome: ${outcome.title}. ${outcome.summary}`.trim();
  }
  if (state.currentStepId !== null) {
    const step = getStep(def, state.currentStepId);
    const position = stepPosition(def, state);
    return `${feedback}Now on step ${position} of ${def.steps.length}: ${step.title}.`.trim();
  }
  return feedback.trim();
}

/**
 * Non-visual, plain-text equivalent of the ENTIRE simulation state (the
 * "documented equivalent accommodation" for EVAL-SIM-004). It reads the
 * objective, the current prompt with all available actions, the full decision
 * transcript, and the outcome — everything a sighted learner can perceive,
 * expressed as linear text a screen reader or braille display renders directly.
 */
export function renderTextEquivalent(
  def: SimulationDefinition,
  state: SimulationState,
): string {
  const lines: string[] = [];
  lines.push(`Simulation: ${def.title} (version ${def.version})`);
  lines.push(`Objective: ${def.objective}`);
  lines.push("");

  if (state.history.length > 0) {
    lines.push("Decisions so far:");
    state.history.forEach((entry, index) => {
      const marker = entry.correct ? "recommended" : "not recommended";
      lines.push(`  ${index + 1}. On "${entry.stepTitle}", you chose "${entry.choiceLabel}" (${marker}).`);
      lines.push(`     Feedback: ${entry.feedback}`);
    });
    lines.push("");
  }

  if (state.currentStepId !== null) {
    const step = getStep(def, state.currentStepId);
    const position = stepPosition(def, state);
    lines.push(`Current step ${position} of ${def.steps.length}: ${step.title}`);
    lines.push(`Prompt: ${step.prompt}`);
    lines.push("Available actions:");
    step.choices.forEach((choice, index) => {
      lines.push(`  ${index + 1}. ${choice.label} — ${choice.detail}`);
    });
  } else if (state.outcomeId !== null) {
    const outcome = getOutcome(def, state.outcomeId);
    lines.push(`Result: ${outcome.title} (${outcome.status})`);
    lines.push(outcome.summary);
    lines.push("Use the Restart control to run the scenario again.");
  }

  return lines.join("\n");
}
