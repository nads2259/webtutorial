/**
 * URL and markup neutralization for authored/pasted content (LAW-08).
 *
 * Authored input is untrusted (indirect injection surface). The editor schema
 * only recognizes typed block nodes with no marks, so unknown elements and
 * attributes are structurally dropped when parsing. This module additionally
 * neutralizes dangerous URL schemes so a surviving attribute (e.g. an image
 * `src`) can never become an executable/exfiltrating reference.
 */

const SAFE_URL_PREFIX = /^(?:https?:\/\/|\/(?!\/)|#|mailto:)/i;
// biome-ignore lint/suspicious/noControlCharactersInRegex: stripping control chars is the intent.
const CONTROL_CHARS = /[\u0000-\u001f\u007f-\u009f]/g;

/**
 * Returns `url` only if it uses an allowlisted, non-executable scheme; every
 * other input (including `javascript:`, `data:`, `vbscript:`, or malformed
 * values) collapses to an empty string. Deny-by-default (LAW-08).
 */
export function sanitizeUrl(url: unknown): string {
  if (typeof url !== "string") {
    return "";
  }
  const cleaned = url.replace(CONTROL_CHARS, "").trim();
  if (cleaned.length === 0) {
    return "";
  }
  return SAFE_URL_PREFIX.test(cleaned) ? cleaned : "";
}
