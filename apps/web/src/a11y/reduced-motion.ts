import { motion } from "@northstar/design-tokens";

/**
 * Global stylesheet that honors the user's reduced-motion preference
 * (WCAG 2.2.2 Pause, Stop, Hide / 2.3.1 Three Flashes).
 *
 * Inline React styles cannot express a media query, so motion-bearing surfaces
 * (e.g. the skip-link transition) are neutralized here when the OS reports
 * `prefers-reduced-motion: reduce`. Durations collapse to the reduced-motion
 * token so no animation, transition or smooth scroll runs.
 */
export const reducedMotionCss = `
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: ${motion.reducedMotionMs}ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: ${motion.reducedMotionMs}ms !important;
    scroll-behavior: auto !important;
  }
}
`;
