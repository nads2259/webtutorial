import type { ContrastKind, HexColor } from "./contrast";

/**
 * Semantic design tokens honoring `spec/ux/accessibility-design-tokens.md`.
 * Colors are chosen so every required foreground/background pair meets the
 * WCAG 2.2 AA thresholds asserted in `tokens.test.ts`.
 */
export const color = {
  surface: "#ffffff",
  surfaceMuted: "#f4f5f7",
  text: "#1a1a1a",
  textMuted: "#595959",
  border: "#767676",
  focusRing: "#0b5fff",
  primary: "#1d4ed8",
  onPrimary: "#ffffff",
  danger: "#b00020",
  onDanger: "#ffffff",
} as const satisfies Record<string, HexColor>;

export type ColorToken = keyof typeof color;

/** Spacing scale in CSS pixels; `unit` is the base step. */
export const space = {
  unit: 4,
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
} as const;

/**
 * Typography tokens. `minTargetPx` encodes the WCAG 2.5.8 target-size floor
 * (>= 24x24 CSS px) that interactive components must honor.
 */
export const type = {
  fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
  baseSizePx: 16,
  lineHeight: 1.5,
  largeTextPx: 24,
} as const;

export const size = {
  /** WCAG 2.5.8 minimum interactive target size. */
  minTargetPx: 24,
  /** Recommended primary touch target. */
  recommendedTargetPx: 44,
  /** WCAG 2.4.11 minimum focus indicator thickness. */
  focusRingWidthPx: 2,
} as const;

/**
 * Motion tokens. Durations collapse to 0 when the user prefers reduced motion
 * (WCAG 2.2.2 / 2.3.1); consumers must gate animations behind
 * `prefers-reduced-motion`.
 */
export const motion = {
  durationFastMs: 120,
  durationBaseMs: 200,
  reducedMotionMs: 0,
  easingStandard: "cubic-bezier(0.2, 0, 0, 1)",
} as const;

export interface ContrastRequirement {
  readonly name: string;
  readonly foreground: HexColor;
  readonly background: HexColor;
  readonly kind: ContrastKind;
  /** WCAG success criterion this pair defends. */
  readonly sc: string;
}

/**
 * The contrast contract for the palette. Each entry is enforced by the token
 * contrast test and by `lintContrast`.
 */
export const contrastRequirements: readonly ContrastRequirement[] = [
  {
    name: "body text on surface",
    foreground: color.text,
    background: color.surface,
    kind: "text",
    sc: "1.4.3",
  },
  {
    name: "muted text on surface",
    foreground: color.textMuted,
    background: color.surface,
    kind: "text",
    sc: "1.4.3",
  },
  {
    name: "text on muted surface",
    foreground: color.text,
    background: color.surfaceMuted,
    kind: "text",
    sc: "1.4.3",
  },
  {
    name: "primary button label",
    foreground: color.onPrimary,
    background: color.primary,
    kind: "text",
    sc: "1.4.3",
  },
  {
    name: "danger label",
    foreground: color.onDanger,
    background: color.danger,
    kind: "text",
    sc: "1.4.3",
  },
  {
    name: "component border on surface",
    foreground: color.border,
    background: color.surface,
    kind: "ui",
    sc: "1.4.11",
  },
  {
    name: "focus ring on surface",
    foreground: color.focusRing,
    background: color.surface,
    kind: "ui",
    sc: "1.4.11",
  },
] as const;
