import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { EditorState } from "prosemirror-state";
import { describe, expect, it } from "vitest";
import type { Block } from "./blocks";
import { docToTree, treeToDoc } from "./convert";
import { corpus } from "./fixtures/corpus";

describe("typed block tree <-> ProseMirror round-trip (EVAL-CNT-004 / FR-CNT-003)", () => {
  it("is the identity for every supported block", () => {
    const roundTripped = docToTree(treeToDoc(corpus));
    expect(roundTripped).toEqual(corpus);
  });

  it("preserves stable block ids through a text edit", () => {
    const state = EditorState.create({ doc: treeToDoc(corpus) });
    // Insert a character inside the first heading's text (positions 1..6).
    const tr = state.tr.insertText("!", 2);
    const edited = docToTree(state.apply(tr).doc);

    expect(edited.map((block) => block.id)).toEqual(corpus.map((block) => block.id));
    const heading = edited[0];
    expect(heading?.type).toBe("heading");
    if (heading?.type === "heading") {
      expect(heading.data.text).not.toBe("Title");
      expect(heading.data.text).toContain("!");
    }
  });

  it("produces blocks that validate against content-document.schema.json", () => {
    const schemaPath = resolve(
      import.meta.dirname,
      "../../../spec/contracts/schemas/content-document.schema.json",
    );
    const schema = JSON.parse(readFileSync(schemaPath, "utf8")) as Record<string, unknown>;
    const ajv = new Ajv2020({ strict: false });
    addFormats(ajv);
    const validate = ajv.compile(schema);

    const blocks: Block[] = docToTree(treeToDoc(corpus));
    const document = {
      schema_version: "1.0",
      object_id: "01JOBJECT0000000000000001",
      revision_id: "01JREVISION00000000000001",
      document_type: "tutorial",
      locale: "en",
      title: "Round-trip corpus document",
      blocks,
      provenance: {
        created_by: { type: "user", id: "01JUSER00000000000000001" },
        created_at: "2026-08-15T10:00:00Z",
        content_hash: `sha256:${"a".repeat(64)}`,
      },
    };

    const valid = validate(document);
    expect(validate.errors ?? []).toEqual([]);
    expect(valid).toBe(true);
  });
});
