import { describe, expect, it } from "vitest";
import type { ContrastRequirement } from "./tokens";
import { contrastRequirements } from "./tokens";
import { lintContrast } from "./token-lint";

describe("token lint guard (NFR-A11Y-002)", () => {
  it("passes the real palette with no violations", () => {
    expect(lintContrast(contrastRequirements)).toHaveLength(0);
  });

  it("FLAGS a contrast-failing text pair (negative case)", () => {
    const failing: ContrastRequirement[] = [
      {
        name: "low-contrast grey text on white",
        foreground: "#aaaaaa",
        background: "#ffffff",
        kind: "text",
        sc: "1.4.3",
      },
    ];
    const violations = lintContrast(failing);
    expect(violations).toHaveLength(1);
    expect(violations[0]?.required).toBe(4.5);
    expect(violations[0]?.actual).toBeLessThan(4.5);
  });

  it("FLAGS a UI pair that fails the 3:1 minimum (negative case)", () => {
    const failing: ContrastRequirement[] = [
      {
        name: "faint border on white",
        foreground: "#dddddd",
        background: "#ffffff",
        kind: "ui",
        sc: "1.4.11",
      },
    ];
    expect(lintContrast(failing)).toHaveLength(1);
  });
});
