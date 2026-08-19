import type { ContentBlock, ContentDocument } from "./content-document";

/**
 * Projects the document title to <h1> and offsets block heading levels by one so
 * there is exactly one <h1> and no skipped levels (WCAG 1.3.1, 2.4.6, 2.4.10).
 */
function headingTag(level: number): "h2" | "h3" | "h4" | "h5" | "h6" {
  const projected = Math.min(level + 1, 6);
  return `h${projected}` as "h2" | "h3" | "h4" | "h5" | "h6";
}

function renderBlock(block: ContentBlock): React.JSX.Element {
  switch (block.type) {
    case "heading": {
      const Tag = headingTag(block.data.level);
      return <Tag key={block.id}>{block.data.text}</Tag>;
    }
    case "paragraph":
      return <p key={block.id}>{block.data.text}</p>;
    default: {
      const _exhaustive: never = block;
      return _exhaustive;
    }
  }
}

export interface DocumentRendererProps {
  document: ContentDocument;
}

export function DocumentRenderer({ document }: DocumentRendererProps): React.JSX.Element {
  return (
    <article className="ns-article" aria-labelledby="doc-title">
      <h1 id="doc-title">{document.title}</h1>
      {document.summary ? <p>{document.summary}</p> : null}
      {document.blocks.map(renderBlock)}
    </article>
  );
}
