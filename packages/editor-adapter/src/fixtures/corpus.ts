import type { Block } from "../blocks";

/**
 * Round-trip corpus covering every supported block type, including edge cases
 * (code with/without language, ordered/unordered lists, non-default versions).
 * `docToTree(treeToDoc(corpus))` must equal this exactly (EVAL-CNT-004).
 */
export const corpus: readonly Block[] = [
  { id: "blk-heading-000001", type: "heading", version: 1, data: { level: 1, text: "Title" } },
  {
    id: "blk-paragraph-0001",
    type: "paragraph",
    version: 2,
    data: { text: "Effects synchronize a component with an external system." },
  },
  {
    id: "blk-code-000000001",
    type: "code",
    version: 1,
    data: { language: "ts", code: "const x = 1;\nconst y = 2;" },
  },
  {
    id: "blk-code-nolang001",
    type: "code",
    version: 4,
    data: { language: null, code: "plain text body" },
  },
  { id: "blk-quote-00000001", type: "quote", version: 3, data: { text: "To be, or not to be." } },
  {
    id: "blk-image-00000001",
    type: "image",
    version: 1,
    data: { src: "https://example.com/diagram.png", alt: "An architecture diagram" },
  },
  {
    id: "blk-list-unord0001",
    type: "list",
    version: 1,
    data: { ordered: false, items: ["one", "two", "three"] },
  },
  {
    id: "blk-list-order0001",
    type: "list",
    version: 2,
    data: { ordered: true, items: ["first", "second"] },
  },
];
