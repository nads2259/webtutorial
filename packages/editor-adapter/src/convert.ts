import type { Node as PMNode } from "prosemirror-model";
import type { Block, HeadingLevel } from "./blocks";
import { editorSchema, parseUntrustedHtml } from "./schema";

/**
 * Lossless converters between the canonical typed block tree and the
 * ProseMirror document. `docToTree(treeToDoc(tree))` is the identity for every
 * supported block, preserving stable block ids (FR-CNT-003, EVAL-CNT-004).
 */

function textChildren(value: string): PMNode[] {
  // ProseMirror forbids empty text nodes; an empty string is simply no content.
  return value.length > 0 ? [editorSchema.text(value)] : [];
}

function blockToNode(block: Block): PMNode {
  switch (block.type) {
    case "heading":
      return editorSchema.node(
        "heading",
        { blockId: block.id, version: block.version, level: block.data.level },
        textChildren(block.data.text),
      );
    case "paragraph":
      return editorSchema.node(
        "paragraph",
        { blockId: block.id, version: block.version },
        textChildren(block.data.text),
      );
    case "code":
      return editorSchema.node(
        "code_block",
        { blockId: block.id, version: block.version, language: block.data.language },
        textChildren(block.data.code),
      );
    case "quote":
      return editorSchema.node(
        "quote",
        { blockId: block.id, version: block.version },
        textChildren(block.data.text),
      );
    case "image":
      return editorSchema.node("image", {
        blockId: block.id,
        version: block.version,
        src: block.data.src,
        alt: block.data.alt,
      });
    case "list":
      return editorSchema.node(
        "list",
        { blockId: block.id, version: block.version, ordered: block.data.ordered },
        block.data.items.map((item) =>
          editorSchema.node("list_item", null, textChildren(item)),
        ),
      );
    default: {
      const _exhaustive: never = block;
      return _exhaustive;
    }
  }
}

/** Builds a schema-valid ProseMirror document from a typed block tree. */
export function treeToDoc(blocks: readonly Block[]): PMNode {
  return editorSchema.node("doc", null, blocks.map(blockToNode));
}

function nodeToBlock(node: PMNode): Block {
  const blockId = String(node.attrs.blockId ?? "");
  const version = Number(node.attrs.version ?? 1);
  switch (node.type.name) {
    case "heading":
      return {
        id: blockId,
        type: "heading",
        version,
        data: { level: (node.attrs.level as HeadingLevel) ?? 1, text: node.textContent },
      };
    case "paragraph":
      return { id: blockId, type: "paragraph", version, data: { text: node.textContent } };
    case "code_block":
      return {
        id: blockId,
        type: "code",
        version,
        data: { language: (node.attrs.language as string | null) ?? null, code: node.textContent },
      };
    case "quote":
      return { id: blockId, type: "quote", version, data: { text: node.textContent } };
    case "image":
      return {
        id: blockId,
        type: "image",
        version,
        data: { src: String(node.attrs.src ?? ""), alt: String(node.attrs.alt ?? "") },
      };
    case "list": {
      const items: string[] = [];
      for (let i = 0; i < node.childCount; i += 1) {
        items.push(node.child(i).textContent);
      }
      return {
        id: blockId,
        type: "list",
        version,
        data: { ordered: Boolean(node.attrs.ordered), items },
      };
    }
    default:
      throw new Error(`Unsupported editor node: ${node.type.name}`);
  }
}

/** Reads a typed block tree back out of a ProseMirror document. */
export function docToTree(doc: PMNode): Block[] {
  const blocks: Block[] = [];
  for (let i = 0; i < doc.childCount; i += 1) {
    blocks.push(nodeToBlock(doc.child(i)));
  }
  return blocks;
}

/**
 * Parses untrusted HTML (e.g. clipboard paste) into a typed block tree. Because
 * the schema has no marks and only the supported block nodes, malicious markup
 * (`<script>`, `onerror`, `javascript:` URLs) cannot survive as executable
 * nodes — it is structurally dropped or neutralized (LAW-08).
 */
export function htmlToBlocks(html: string): Block[] {
  return docToTree(parseUntrustedHtml(html));
}
