import { describe, expect, it } from "vitest";
import {
  type AxeViolationLike,
  type SurfaceReport,
  buildReport,
  renderManualChecklist,
  summarizeViolations,
} from "./report";

describe("summarizeViolations", () => {
  it("counts violations by impact and keeps only blocking detail", () => {
    const violations: AxeViolationLike[] = [
      { id: "color-contrast", impact: "serious", help: "h", helpUrl: "u", nodes: [1, 2] },
      { id: "region", impact: "moderate", help: "h", helpUrl: "u", nodes: [1] },
      { id: "aria", impact: "critical", help: "h", helpUrl: "u", nodes: [] },
      { id: "minor-thing", impact: "minor", nodes: [] },
    ];
    const summary = summarizeViolations(violations);
    expect(summary.critical).toBe(1);
    expect(summary.serious).toBe(1);
    expect(summary.moderate).toBe(1);
    expect(summary.minor).toBe(1);
    expect(summary.total).toBe(4);
    expect(summary.blocking).toHaveLength(2);
    expect(summary.blocking.map((b) => b.id).sort()).toEqual(["aria", "color-contrast"]);
  });

  it("treats a missing impact as minor (non-blocking)", () => {
    const summary = summarizeViolations([{ id: "x" }]);
    expect(summary.minor).toBe(1);
    expect(summary.blocking).toHaveLength(0);
  });

  it("returns an all-zero summary for a clean surface", () => {
    const summary = summarizeViolations([]);
    expect(summary).toMatchObject({ critical: 0, serious: 0, total: 0, blocking: [] });
  });
});

function cleanSummary() {
  return summarizeViolations([]);
}

describe("buildReport", () => {
  const surfaces: SurfaceReport[] = [
    {
      id: "reader",
      name: "Reader",
      kind: "route",
      initial: cleanSummary(),
      afterInteraction: cleanSummary(),
      interaction: "none",
    },
    {
      id: "ui-button",
      name: "Button",
      kind: "primitive",
      initial: cleanSummary(),
      afterInteraction: null,
      interaction: null,
    },
  ];

  const base = {
    generatedAt: "2026-01-01T00:00:00.000Z",
    tooling: { "axe-core": "4.x" },
    surfaces,
    keyboard: [{ id: "k1", description: "tab order", wcag: "2.4.3", passed: true }],
    focus: [{ id: "f1", description: "focus visible", wcag: "2.4.7", passed: true }],
    reflow: [{ id: "r1", description: "relative units", wcag: "1.4.10", passed: true }],
    reducedMotion: [{ id: "m1", description: "reduced motion", wcag: "2.2.2", passed: true }],
    contrast: [
      { name: "body", sc: "1.4.3", kind: "text", required: 4.5, actual: 12.6, passed: true },
    ],
    wcagMapping: [
      {
        sc: "2.1.1",
        name: "Keyboard",
        level: "A" as const,
        method: "automated" as const,
        status: "pass" as const,
        notes: "",
      },
    ],
    evaluations: [
      {
        evaluation_id: "EVAL-UX-001",
        automation: "hybrid" as const,
        result: "pass" as const,
        note: "",
      },
    ],
  };

  it("aggregates surface totals and marks manual AT as human-required", () => {
    const report = buildReport(base);
    expect(report.summary.surfaces_total).toBe(2);
    expect(report.summary.surfaces_zero_critical_serious).toBe(2);
    expect(report.summary.total_critical).toBe(0);
    expect(report.summary.total_serious).toBe(0);
    expect(report.summary.contrast_pairs_passed).toBe("1/1");
    expect(report.manual_assistive_technology.machine_pass).toBe(false);
    expect(report.manual_assistive_technology.status).toBe("requires_human_at_exercise");
    expect(report.gate).toBe("GATE-ACCESSIBILITY");
    expect(report.standard).toBe("WCAG 2.2 AA");
  });

  it("counts a surface with a blocking violation as not clean", () => {
    const dirty: SurfaceReport = {
      id: "bad",
      name: "Bad",
      kind: "route",
      initial: summarizeViolations([{ id: "x", impact: "critical", nodes: [] }]),
      afterInteraction: null,
      interaction: null,
    };
    const report = buildReport({ ...base, surfaces: [...surfaces, dirty] });
    expect(report.summary.surfaces_total).toBe(3);
    expect(report.summary.surfaces_zero_critical_serious).toBe(2);
    expect(report.summary.total_critical).toBe(1);
  });
});

describe("renderManualChecklist", () => {
  it("is honestly flagged as human-required and not a machine pass", () => {
    const md = renderManualChecklist("2026-01-01T00:00:00.000Z");
    expect(md).toContain("REQUIRES HUMAN AT EXERCISE");
    expect(md).toContain("NOT A MACHINE PASS");
    expect(md).toContain("EVAL-A11Y-006");
    expect(md).toContain("NVDA");
    expect(md).toContain("VoiceOver");
  });
});
