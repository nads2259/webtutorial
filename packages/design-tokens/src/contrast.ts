/**
 * WCAG 2.2 contrast utilities (SC 1.4.3 / 1.4.11).
 * Implements the relative-luminance and contrast-ratio algorithm defined at
 * https://www.w3.org/TR/WCAG22/#dfn-relative-luminance and
 * https://www.w3.org/TR/WCAG22/#dfn-contrast-ratio
 */

/** A 3- or 6-digit hex color, with leading `#`. */
export type HexColor = `#${string}`;

/** Minimum ratios required by `spec/ux/accessibility-design-tokens.md`. */
export const CONTRAST_MINIMUM = {
  /** Body text vs background (WCAG 1.4.3). */
  text: 4.5,
  /** Large text (>= 24px, or >= 18.66px bold) (WCAG 1.4.3). */
  largeText: 3,
  /** UI component boundaries, states, meaningful graphics (WCAG 1.4.11). */
  ui: 3,
} as const;

export type ContrastKind = keyof typeof CONTRAST_MINIMUM;

interface Rgb {
  r: number;
  g: number;
  b: number;
}

function parseHex(hex: HexColor): Rgb {
  const value = hex.replace(/^#/, "");
  const normalized =
    value.length === 3
      ? value
          .split("")
          .map((c) => c + c)
          .join("")
      : value;
  if (!/^[0-9a-fA-F]{6}$/.test(normalized)) {
    throw new Error(`Invalid hex color: ${hex}`);
  }
  return {
    r: Number.parseInt(normalized.slice(0, 2), 16),
    g: Number.parseInt(normalized.slice(2, 4), 16),
    b: Number.parseInt(normalized.slice(4, 6), 16),
  };
}

function channelLuminance(channel: number): number {
  const srgb = channel / 255;
  return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
}

/** Relative luminance of an sRGB color in the range [0, 1]. */
export function relativeLuminance(hex: HexColor): number {
  const { r, g, b } = parseHex(hex);
  return (
    0.2126 * channelLuminance(r) +
    0.7152 * channelLuminance(g) +
    0.0722 * channelLuminance(b)
  );
}

/** Contrast ratio between two colors, in the range [1, 21]. */
export function contrastRatio(foreground: HexColor, background: HexColor): number {
  const l1 = relativeLuminance(foreground);
  const l2 = relativeLuminance(background);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

/** Whether a foreground/background pair meets the minimum for the given kind. */
export function meetsContrast(
  foreground: HexColor,
  background: HexColor,
  kind: ContrastKind,
): boolean {
  return contrastRatio(foreground, background) >= CONTRAST_MINIMUM[kind];
}
