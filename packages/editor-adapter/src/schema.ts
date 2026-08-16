import {
  type DOMOutputSpec,
  type Node as PMNode,
  Schema,
  DOMParser as PMDOMParser,
} from "prosemirror-model";
import { sanitizeUrl } from "./sanitize";

/**
 * ProseMirror schema whose nodes map 1:1 to the canonical typed blocks
 * (heading/paragraph/code/quote/image/list). It defines NO marks: authored
 * content is plain structured text, so pasted bold/link/script markup cannot
 * survive as executable or styled nodes (LAW-08, FR-CNT-003).
 *
 * Every block node carries `blockId` and `version` attributes so stable block
 * identity is preserved through edits and round-trips.
 */

function idAttrs(dom: HTMLElement): { blockId: string; version: number } {
  const blockId = dom.getAttribute("data-block-id") ?? "";
  const rawVersion = Number.parseInt(dom.getAttribute("data-block-version") ?? "1", 10);
  return { blockId, version: Number.isFinite(rawVersion) ? rawVersion : 1 };
}

function idDataset(node: PMNode): Record<string, string> {
  return {
    "data-block-id": String(node.attrs.blockId ?? ""),
    "data-block-version": String(node.attrs.version ?? 1),
  };
}

export const editorSchema = new Schema({
  nodes: {
    doc: { content: "block+" },

    paragraph: {
      group: "block",
      content: "text*",
      attrs: { blockId: { default: "" }, version: { default: 1 } },
      parseDOM: [{ tag: "p", getAttrs: (dom) => idAttrs(dom as HTMLElement) }],
      toDOM(node): DOMOutputSpec {
        return ["p", idDataset(node), 0];
      },
    },

    heading: {
      group: "block",
      content: "text*",
      defining: true,
      attrs: { blockId: { default: "" }, version: { default: 1 }, level: { default: 1 } },
      parseDOM: [1, 2, 3, 4, 5, 6].map((level) => ({
        tag: `h${level}`,
        getAttrs: (dom: string | HTMLElement) => ({ ...idAttrs(dom as HTMLElement), level }),
      })),
      toDOM(node): DOMOutputSpec {
        return [`h${node.attrs.level as number}`, idDataset(node), 0];
      },
    },

    code_block: {
      group: "block",
      content: "text*",
      marks: "",
      code: true,
      defining: true,
      attrs: { blockId: { default: "" }, version: { default: 1 }, language: { default: null } },
      parseDOM: [
        {
          tag: "pre",
          preserveWhitespace: "full",
          getAttrs: (dom) => {
            const el = dom as HTMLElement;
            const language = el.getAttribute("data-language");
            return { ...idAttrs(el), language: language && language.length > 0 ? language : null };
          },
        },
      ],
      toDOM(node): DOMOutputSpec {
        const language = node.attrs.language as string | null;
        const attrs: Record<string, string> = idDataset(node);
        if (language) {
          attrs["data-language"] = language;
        }
        return ["pre", attrs, ["code", 0]];
      },
    },

    quote: {
      group: "block",
      content: "text*",
      defining: true,
      attrs: { blockId: { default: "" }, version: { default: 1 } },
      parseDOM: [{ tag: "blockquote", getAttrs: (dom) => idAttrs(dom as HTMLElement) }],
      toDOM(node): DOMOutputSpec {
        return ["blockquote", idDataset(node), 0];
      },
    },

    image: {
      group: "block",
      atom: true,
      draggable: false,
      attrs: {
        blockId: { default: "" },
        version: { default: 1 },
        src: { default: "" },
        alt: { default: "" },
      },
      parseDOM: [
        {
          tag: "img[src]",
          getAttrs: (dom) => {
            const el = dom as HTMLElement;
            return {
              ...idAttrs(el),
              src: sanitizeUrl(el.getAttribute("src")),
              alt: el.getAttribute("alt") ?? "",
            };
          },
        },
      ],
      toDOM(node): DOMOutputSpec {
        return [
          "img",
          {
            ...idDataset(node),
            src: sanitizeUrl(node.attrs.src),
            alt: String(node.attrs.alt ?? ""),
          },
        ];
      },
    },

    list: {
      group: "block",
      content: "list_item+",
      attrs: { blockId: { default: "" }, version: { default: 1 }, ordered: { default: false } },
      parseDOM: [
        {
          tag: "ul",
          getAttrs: (dom) => ({ ...idAttrs(dom as HTMLElement), ordered: false }),
        },
        {
          tag: "ol",
          getAttrs: (dom) => ({ ...idAttrs(dom as HTMLElement), ordered: true }),
        },
      ],
      toDOM(node): DOMOutputSpec {
        return [node.attrs.ordered ? "ol" : "ul", idDataset(node), 0];
      },
    },

    list_item: {
      content: "text*",
      parseDOM: [{ tag: "li" }],
      toDOM(): DOMOutputSpec {
        return ["li", 0];
      },
    },

    text: { group: "inline" },
  },
  // No marks: content is plain structured text; styling/links/scripts are not
  // representable and therefore cannot be authored or pasted into the document.
  marks: {},
});

/**
 * Parses untrusted HTML into the schema-constrained document. Only elements the
 * schema recognizes survive; unknown tags, attributes, event handlers and marks
 * are dropped. Returns a schema `doc` node.
 */
export function parseUntrustedHtml(html: string): PMNode {
  const doc = new DOMParser().parseFromString(html, "text/html");
  return PMDOMParser.fromSchema(editorSchema).parse(doc.body);
}
