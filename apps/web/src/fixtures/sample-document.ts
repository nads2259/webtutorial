import type { ContentDocument } from "../content-document";

/**
 * Static fixture matching the canonical content-document shape
 * (`spec/contracts/examples/content-document.example.json`). Represents a
 * single published, public knowledge document rendered by the app shell.
 */
export const sampleDocument: ContentDocument = {
  schema_version: "1.0",
  object_id: "01JKNOWLEDGEOBJECT00000001",
  revision_id: "01JKNOWLEDGEREVISION000001",
  document_type: "tutorial",
  locale: "en",
  title: "Understanding Effects",
  summary: "A structured tutorial on synchronizing components with external systems.",
  blocks: [
    {
      id: "01JBLOCK000000000000000001",
      type: "heading",
      version: 1,
      data: { level: 1, text: "What effects do" },
    },
    {
      id: "01JBLOCK000000000000000002",
      type: "paragraph",
      version: 1,
      data: { text: "Effects synchronize a component with an external system." },
    },
    {
      id: "01JBLOCK000000000000000003",
      type: "heading",
      version: 2,
      data: { level: 2, text: "When to use them" },
    },
    {
      id: "01JBLOCK000000000000000004",
      type: "paragraph",
      version: 1,
      data: {
        text: "Reach for an effect only when you need to step outside of React to coordinate with a non-React system.",
      },
    },
  ],
  provenance: {
    created_by: { type: "user", id: "01JUSER000000000000000001", delegated_by: null },
    created_at: "2026-08-14T10:00:00Z",
    content_hash: "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  },
};
