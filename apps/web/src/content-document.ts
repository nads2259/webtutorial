/**
 * Local read model for the canonical content document
 * (`spec/contracts/schemas/content-document.schema.json`, schema_version "1.0").
 * The app only consumes published, public content and never executes remote
 * code from block data (FR-CMS-007) — blocks are rendered as inert semantic HTML.
 */

export interface HeadingBlockData {
  level: 1 | 2 | 3 | 4 | 5 | 6;
  text: string;
}

export interface ParagraphBlockData {
  text: string;
}

export interface HeadingBlock {
  id: string;
  type: "heading";
  version: number;
  data: HeadingBlockData;
}

export interface ParagraphBlock {
  id: string;
  type: "paragraph";
  version: number;
  data: ParagraphBlockData;
}

export type ContentBlock = HeadingBlock | ParagraphBlock;

export interface ContentDocument {
  schema_version: "1.0";
  object_id: string;
  revision_id: string;
  document_type:
    | "tutorial"
    | "lesson"
    | "course_outline"
    | "research_document"
    | "report"
    | "simulation_guide"
    | "knowledge_page";
  locale: string;
  title: string;
  summary?: string | null;
  blocks: ContentBlock[];
  provenance: {
    created_by: { type: string; id: string; delegated_by?: string | null };
    created_at: string;
    content_hash: string;
  };
}
