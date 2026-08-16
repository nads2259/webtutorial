/**
 * Pure builders for the machine-readable accessibility evaluation report
 * (GATE-ACCESSIBILITY automatable slice: EVAL-A11Y-001/002/003, EVAL-UX-001/002)
 * and the honest, human-flagged manual assistive-technology checklist
 * (EVAL-A11Y-006 / UX-002 manual portion).
 *
 * This module is side-effect free: it only shapes data. The harness test owns
 * the actual axe runs and the filesystem write (side effects at the edge).
 */

export type AxeImpact = "critical" | "serious" | "moderate" | "minor";

/** The minimal shape of an axe-core violation we consume from jest-axe. */
export interface AxeViolationLike {
  readonly id: string;
  readonly impact?: AxeImpact | null;
  readonly help?: string;
  readonly helpUrl?: string;
  readonly nodes?: readonly unknown[];
}

export interface AxeRunSummary {
  readonly critical: number;
  readonly serious: number;
  readonly moderate: number;
  readonly minor: number;
  readonly total: number;
  /** Detail for the blocking (critical/serious) violations only. */
  readonly blocking: ReadonlyArray<{
    readonly id: string;
    readonly impact: AxeImpact;
    readonly help: string;
    readonly helpUrl: string;
    readonly nodeCount: number;
  }>;
}

export type SurfaceKind = "route" | "primitive";

export interface SurfaceReport {
  readonly id: string;
  readonly name: string;
  readonly kind: SurfaceKind;
  /** Axe on initial render. */
  readonly initial: AxeRunSummary;
  /** Axe after a representative interaction, when one applies. */
  readonly afterInteraction: AxeRunSummary | null;
  readonly interaction: string | null;
}

export interface ChecklistItem {
  readonly id: string;
  readonly description: string;
  readonly wcag: string;
  readonly passed: boolean;
  readonly detail?: string;
}

export interface ContrastPairResult {
  readonly name: string;
  readonly sc: string;
  readonly kind: string;
  readonly required: number;
  readonly actual: number;
  readonly passed: boolean;
}

export interface WcagCriterion {
  readonly sc: string;
  readonly name: string;
  readonly level: "A" | "AA";
  readonly method: "automated" | "automated-partial" | "manual";
  readonly status: "pass" | "requires-human-verification";
  readonly notes: string;
}

export interface EvaluationCoverage {
  readonly evaluation_id: string;
  readonly automation: "automated" | "hybrid" | "manual";
  readonly result: "pass" | "requires-human-verification";
  readonly note: string;
}

export interface BuildReportInput {
  readonly generatedAt: string;
  readonly tooling: Record<string, string>;
  readonly surfaces: readonly SurfaceReport[];
  readonly keyboard: readonly ChecklistItem[];
  readonly focus: readonly ChecklistItem[];
  readonly reflow: readonly ChecklistItem[];
  readonly reducedMotion: readonly ChecklistItem[];
  readonly contrast: readonly ContrastPairResult[];
  readonly wcagMapping: readonly WcagCriterion[];
  readonly evaluations: readonly EvaluationCoverage[];
}

export interface A11yReport {
  readonly report_id: string;
  readonly task_id: string;
  readonly standard: string;
  readonly gate: string;
  readonly generated_at: string;
  readonly tooling: Record<string, string>;
  readonly summary: {
    readonly surfaces_total: number;
    readonly surfaces_zero_critical_serious: number;
    readonly total_critical: number;
    readonly total_serious: number;
    readonly keyboard_checks_passed: string;
    readonly reflow_checks_passed: string;
    readonly contrast_pairs_passed: string;
    readonly manual_at_status: "requires_human_at_exercise";
  };
  readonly surfaces: readonly SurfaceReport[];
  readonly checklist: {
    readonly keyboard_operability: readonly ChecklistItem[];
    readonly focus_management: readonly ChecklistItem[];
    readonly reflow_zoom: readonly ChecklistItem[];
    readonly reduced_motion: readonly ChecklistItem[];
    readonly color_contrast: readonly ContrastPairResult[];
  };
  readonly wcag_2_2_aa_mapping: readonly WcagCriterion[];
  readonly evaluation_coverage: readonly EvaluationCoverage[];
  readonly manual_assistive_technology: {
    readonly evaluation_id: "EVAL-A11Y-006";
    readonly status: "requires_human_at_exercise";
    readonly machine_pass: false;
    readonly checklist_artifact: string;
    readonly note: string;
  };
}

/** Summarize an axe result's violations by impact, keeping blocking detail. */
export function summarizeViolations(violations: readonly AxeViolationLike[]): AxeRunSummary {
  const counts = { critical: 0, serious: 0, moderate: 0, minor: 0 };
  const blocking: Array<{
    id: string;
    impact: AxeImpact;
    help: string;
    helpUrl: string;
    nodeCount: number;
  }> = [];
  for (const violation of violations) {
    const impact = violation.impact ?? "minor";
    counts[impact] += 1;
    if (impact === "critical" || impact === "serious") {
      blocking.push({
        id: violation.id,
        impact,
        help: violation.help ?? "",
        helpUrl: violation.helpUrl ?? "",
        nodeCount: violation.nodes?.length ?? 0,
      });
    }
  }
  return {
    ...counts,
    total: violations.length,
    blocking,
  };
}

function countPassed(items: readonly ChecklistItem[]): string {
  const passed = items.filter((item) => item.passed).length;
  return `${passed}/${items.length}`;
}

/** Assemble the full accessibility evaluation report object (pure). */
export function buildReport(input: BuildReportInput): A11yReport {
  const totalCritical = input.surfaces.reduce(
    (sum, surface) =>
      sum + surface.initial.critical + (surface.afterInteraction?.critical ?? 0),
    0,
  );
  const totalSerious = input.surfaces.reduce(
    (sum, surface) => sum + surface.initial.serious + (surface.afterInteraction?.serious ?? 0),
    0,
  );
  const zeroBlocking = input.surfaces.filter((surface) => {
    const initialClean = surface.initial.critical === 0 && surface.initial.serious === 0;
    const afterClean =
      surface.afterInteraction === null ||
      (surface.afterInteraction.critical === 0 && surface.afterInteraction.serious === 0);
    return initialClean && afterClean;
  }).length;

  const contrastPassed = input.contrast.filter((pair) => pair.passed).length;

  return {
    report_id: "a11y-evaluation",
    task_id: "TASK-A11Y-HARNESS-A2",
    standard: "WCAG 2.2 AA",
    gate: "GATE-ACCESSIBILITY",
    generated_at: input.generatedAt,
    tooling: input.tooling,
    summary: {
      surfaces_total: input.surfaces.length,
      surfaces_zero_critical_serious: zeroBlocking,
      total_critical: totalCritical,
      total_serious: totalSerious,
      keyboard_checks_passed: countPassed([...input.keyboard, ...input.focus]),
      reflow_checks_passed: countPassed([...input.reflow, ...input.reducedMotion]),
      contrast_pairs_passed: `${contrastPassed}/${input.contrast.length}`,
      manual_at_status: "requires_human_at_exercise",
    },
    surfaces: input.surfaces,
    checklist: {
      keyboard_operability: input.keyboard,
      focus_management: input.focus,
      reflow_zoom: input.reflow,
      reduced_motion: input.reducedMotion,
      color_contrast: input.contrast,
    },
    wcag_2_2_aa_mapping: input.wcagMapping,
    evaluation_coverage: input.evaluations,
    manual_assistive_technology: {
      evaluation_id: "EVAL-A11Y-006",
      status: "requires_human_at_exercise",
      machine_pass: false,
      checklist_artifact: "manual-at-checklist.md",
      note: "Automated checks are necessary but NOT sufficient. EVAL-A11Y-006 requires a human to exercise real assistive technology (screen readers, magnification, voice control) per the checklist. This report does not mark A11Y-006 as machine-PASS.",
    },
  };
}

/** Render the human-flagged manual assistive-technology checklist (Markdown). */
export function renderManualChecklist(generatedAt: string): string {
  return `# Manual Assistive-Technology Checklist (EVAL-A11Y-006 / UX-002 manual)

> **STATUS: REQUIRES HUMAN AT EXERCISE — NOT A MACHINE PASS.**
> Automated axe-core, keyboard-simulation and token-contrast checks (see
> \`a11y-evaluation.json\`) cover the automatable slice of GATE-ACCESSIBILITY.
> Per \`spec/docs/12_ux_and_design_system_specification.md\` §15 and the constitution
> (LAW-08), automated checks are *necessary but not sufficient*. The items below
> MUST be performed by a human with real assistive technology and cannot be
> satisfied by CI.

- Generated: ${generatedAt}
- Standard: WCAG 2.2 AA
- Gate: GATE-ACCESSIBILITY
- Manual evaluations: EVAL-A11Y-006 (quality), EVAL-UX-002 (manual keyboard + screen-reader journeys)

## Supported assistive-technology matrix (exercise each, record pass/fail + notes)

| Platform | Screen reader | Browser | Status | Tester | Notes |
|---|---|---|---|---|---|
| Windows | NVDA (latest) | Firefox | ☐ not run | | |
| Windows | JAWS (latest) | Chrome | ☐ not run | | |
| macOS | VoiceOver | Safari | ☐ not run | | |
| iOS | VoiceOver | Safari | ☐ not run | | |
| Android | TalkBack | Chrome | ☐ not run | | |

## Per-surface manual tasks

For **each** surface (reader / authoring editor / simulation runner) confirm with real AT:

- [ ] Screen reader announces landmarks, the single \`<h1>\`, and heading outline in a sensible reading order.
- [ ] Skip link is reachable as the first Tab stop and moves focus to main content.
- [ ] Every interactive control is reachable and operable by keyboard only; focus is always visible.
- [ ] No keyboard trap; focus order is logical and matches the visual order.
- [ ] Live-region status updates are announced without stealing focus.
- [ ] Reflow: at 320 CSS px width **and** 400% browser zoom, no content or function is lost and there is no 2-D scrolling of a single block of content (pixel-accurate — cannot be asserted in jsdom).
- [ ] Text-spacing override bookmarklet (line 1.5×, paragraph 2×, letter 0.12em, word 0.16em) loses no content.
- [ ] Reduced-motion OS setting removes non-essential animation/transition.
- [ ] Color is never the only means of conveying information (verify with grayscale).
- [ ] Voice-control (Dragon / Voice Access) can activate every named control.

## Cognitive / usability review

- [ ] Error messages are specific and suggest correction.
- [ ] Instructions do not rely solely on sensory characteristics.
- [ ] Time limits (if any) are adjustable or absent.

## Sign-off

- Accessibility reviewer: ____________________  Date: __________
- Result recorded in the release evidence pack (this checklist is evidence of *intent + method*, not of a passing manual run until completed).
`;
}
