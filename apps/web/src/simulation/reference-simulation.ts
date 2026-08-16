import type { SimulationDefinition } from "./model";

/**
 * A mock/reference simulation used to exercise the accessible runner. It models
 * a short branching incident-response drill (an R0 deterministic-browser
 * scenario). It performs NO real execution — it is a view-model only. The real
 * runtime/control plane is IMPL-016 (out of scope for TASK-SIM-A11Y-016B).
 */
export const referenceSimulation: SimulationDefinition = {
  id: "sim-incident-triage",
  version: "0.1.0",
  title: "Incident response drill: checkout latency spike",
  objective:
    "Triage a sudden checkout-latency incident and choose the mitigation that restores service without losing data.",
  instructions:
    "Read each prompt, then choose one action. Your decisions and the outcome are announced and are also available as a text-only equivalent. This drill has no time limit.",
  startStepId: "step-detect",
  steps: [
    {
      id: "step-detect",
      title: "Detect and confirm",
      prompt:
        "Checkout p95 latency jumped from 200ms to 4s five minutes ago. Error rate is normal. What do you do first?",
      choices: [
        {
          id: "choice-detect-dashboards",
          label: "Confirm the alert against the latency and saturation dashboards",
          detail: "Cross-check the paging alert with independent latency and resource dashboards before acting.",
          feedback: "Confirmed: the checkout service CPU is saturated while upstream traffic is flat.",
          next: "step-mitigate",
          correct: true,
        },
        {
          id: "choice-detect-restart",
          label: "Immediately restart all checkout instances",
          detail: "Restart every checkout instance right away without confirming the signal.",
          feedback: "Restarting blind dropped in-flight checkout sessions and did not address the root cause.",
          next: "step-mitigate",
          correct: false,
        },
      ],
    },
    {
      id: "step-mitigate",
      title: "Choose a mitigation",
      prompt:
        "CPU saturation is caused by a newly deployed pricing rule doing an unbounded loop. How do you mitigate safely?",
      choices: [
        {
          id: "choice-mitigate-rollback",
          label: "Roll back the pricing-rule deployment behind a feature flag",
          detail: "Disable the offending change via its feature flag / roll back to the last healthy revision.",
          feedback: "Rollback via flag is reversible and immediate; latency begins recovering.",
          next: "step-verify",
          correct: true,
        },
        {
          id: "choice-mitigate-scale",
          label: "Scale checkout out to 10x instances and leave the rule enabled",
          detail: "Throw hardware at the problem while leaving the buggy rule running.",
          feedback: "Scaling masked the symptom briefly but cost spiked and latency returned.",
          next: "step-verify",
          correct: false,
        },
      ],
    },
    {
      id: "step-verify",
      title: "Verify recovery",
      prompt: "You applied a mitigation. How do you verify the incident is resolved before standing down?",
      choices: [
        {
          id: "choice-verify-slo",
          label: "Watch p95 latency and checkout success rate return to SLO for two steady intervals",
          detail: "Confirm recovery against the SLO for a sustained window, then record the timeline.",
          feedback: "Latency and success rate held within SLO for two intervals.",
          next: "outcome-success",
          correct: true,
        },
        {
          id: "choice-verify-close",
          label: "Close the incident as soon as the pager stops firing",
          detail: "Assume resolution the moment alerts clear, without a sustained check.",
          feedback: "Closing on the first quiet minute risks re-alerting; recovery was not yet sustained.",
          next: "outcome-partial",
          correct: false,
        },
      ],
    },
  ],
  outcomes: [
    {
      id: "outcome-success",
      title: "Incident resolved cleanly",
      summary:
        "You confirmed the signal, applied a reversible rollback and verified sustained recovery against the SLO. No data was lost.",
      status: "success",
    },
    {
      id: "outcome-partial",
      title: "Incident mitigated but closed early",
      summary:
        "Service recovered, but standing down before a sustained check risks a repeat page. Add a verification window to your runbook.",
      status: "partial",
    },
  ],
};

/** A keyboard shortcut, rendered in the UI and recorded in the a11y report. */
export interface ShortcutBinding {
  readonly keys: string;
  readonly description: string;
}

/**
 * Shortcuts use the Alt modifier (not single-character keys) so they do not
 * intercept assistive-technology or typing keystrokes (WCAG 2.1.4). They are a
 * convenience layer on top of standard Tab/Enter/Space operation, never the
 * only way to operate a control.
 */
export const simulationShortcuts: readonly ShortcutBinding[] = [
  { keys: "Alt+S", description: "Move focus to the current step" },
  { keys: "Alt+T", description: "Toggle the text-only equivalent view" },
  { keys: "Alt+R", description: "Restart the simulation" },
] as const;
