import { CONTRAST_MINIMUM, contrastRatio } from "./contrast";
import type { ContrastRequirement } from "./tokens";

export interface ContrastViolation {
  readonly name: string;
  readonly sc: string;
  readonly required: number;
  readonly actual: number;
}

/**
 * Token lint (NFR-A11Y-002): returns a violation for every contrast requirement
 * whose measured ratio falls below the WCAG 2.2 AA minimum for its kind. A
 * palette is accessible only when this returns an empty array.
 */
export function lintContrast(
  requirements: readonly ContrastRequirement[],
): ContrastViolation[] {
  const violations: ContrastViolation[] = [];
  for (const requirement of requirements) {
    const actual = contrastRatio(requirement.foreground, requirement.background);
    const required = CONTRAST_MINIMUM[requirement.kind];
    if (actual < required) {
      violations.push({
        name: requirement.name,
        sc: requirement.sc,
        required,
        actual: Math.round(actual * 100) / 100,
      });
    }
  }
  return violations;
}
