import { describe, expect, it } from "vitest";
import { CONTRAST_MINIMUM, contrastRatio } from "./contrast";
import { contrastRequirements, motion, size } from "./tokens";
import { lintContrast } from "./token-lint";

describe("design token contrast contract (WCAG 1.4.3 / 1.4.11)", () => {
  it.each(contrastRequirements)(
    "$name meets the $kind minimum (SC $sc)",
    ({ foreground, background, kind }) => {
      expect(contrastRatio(foreground, background)).toBeGreaterThanOrEqual(
        CONTRAST_MINIMUM[kind],
      );
    },
  );

  it("text pairs meet >= 4.5:1 and large/UI pairs meet >= 3:1", () => {
    for (const req of contrastRequirements) {
      const ratio = contrastRatio(req.foreground, req.background);
      if (req.kind === "text") {
        expect(ratio).toBeGreaterThanOrEqual(4.5);
      } else {
        expect(ratio).toBeGreaterThanOrEqual(3);
      }
    }
  });

  it("the palette produces zero contrast violations", () => {
    expect(lintContrast(contrastRequirements)).toEqual([]);
  });

  it("interactive target-size floor honors WCAG 2.5.8 (>= 24px)", () => {
    expect(size.minTargetPx).toBeGreaterThanOrEqual(24);
  });

  it("reduced-motion duration collapses to 0ms (WCAG 2.2.2 / 2.3.1)", () => {
    expect(motion.reducedMotionMs).toBe(0);
  });
});
