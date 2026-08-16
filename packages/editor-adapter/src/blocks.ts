/**
 * Canonical typed block tree consumed and produced by the visual editor.
 *
 * These interfaces are the editor-side projection of the blocks defined by
 * `spec/contracts/schemas/content-document.schema.json` (`$defs.block`):
 * `{ id, type, version, data }`. The editor is an authorized client of the
 * content capability (LAW-05) — it edits this structured tree and never emits
 * arbitrary HTML/MDX. `data` is typed per block so no untyped `object` leaks
 * into the authoring surface.
 */

export type HeadingLevel = 1 | 2 | 3 | 4 | 5 | 6;

export interface HeadingBlock {
  id: string;
  type: "heading";
  version: number;
  data: { level: HeadingLevel; text: string };
}

export interface ParagraphBlock {
  id: string;
  type: "paragraph";
  version: number;
  data: { text: string };
}

export interface CodeBlock {
  id: string;
  type: "code";
  version: number;
  data: { language: string | null; code: string };
}

export interface QuoteBlock {
  id: string;
  type: "quote";
  version: number;
  data: { text: string };
}

export interface ImageBlock {
  id: string;
  type: "image";
  version: number;
  data: { src: string; alt: string };
}

export interface ListBlock {
  id: string;
  type: "list";
  version: number;
  data: { ordered: boolean; items: string[] };
}

export type Block =
  | HeadingBlock
  | ParagraphBlock
  | CodeBlock
  | QuoteBlock
  | ImageBlock
  | ListBlock;

export type BlockType = Block["type"];

export const SUPPORTED_BLOCK_TYPES: readonly BlockType[] = [
  "heading",
  "paragraph",
  "code",
  "quote",
  "image",
  "list",
] as const;

/**
 * Generates a stable block id that satisfies the canonical `stableId`
 * constraint (8–128 chars). Stable ids are preserved across edits; only newly
 * authored blocks receive a freshly generated id.
 */
export function newBlockId(): string {
  return `blk-${crypto.randomUUID()}`;
}
