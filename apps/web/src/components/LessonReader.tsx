import type { Block, Revision } from "../api/client";
import { type InlineOptions, renderInline } from "../lib/inline";
import { cleanLessonTitle } from "../lib/tree";
import { CodeRunner } from "./CodeRunner";
import { Diagram } from "./Diagram";

const DIAGRAM_LANGS = new Set(["mermaid", "dot", "graphviz"]);
const VIDEO_RE = /\.(mp4|webm|ogg|mov)(\?.*)?$/i;
const THIRD_PARTY_PY = /(?:^|\n)\s*(?:from|import)\s+(?:langgraph|langchain)\b/;

function isBannerBlock(block: Block): boolean {
  if (block.type !== "image") return false;
  return block.data.attributes.role === "banner";
}

function splitBannerBlocks(blocks: Block[]): { banner: Block | null; body: Block[] } {
  const banner = blocks.find(isBannerBlock) ?? null;
  const body = blocks.filter((b) => !isBannerBlock(b));
  return { banner, body };
}

function headingTag(level: number): "h2" | "h3" | "h4" | "h5" | "h6" {
  return `h${Math.min(Math.max(level + 1, 2), 6)}` as "h2" | "h3" | "h4" | "h5" | "h6";
}

function renderBlock(
  block: Block,
  lessonId: string | null,
  authenticated: boolean,
  inline: InlineOptions,
  allowPythonRun: boolean,
): React.JSX.Element {
  const content = block.data.content;
  switch (block.type) {
    case "heading": {
      const Tag = headingTag(Number(block.data.attributes.level ?? 2));
      return <Tag key={block.id}>{renderInline(String(content), inline)}</Tag>;
    }
    case "paragraph":
      return <p key={block.id}>{renderInline(String(content), inline)}</p>;
    case "quote":
      return (
        <blockquote key={block.id} className="bip-quote">
          {renderInline(String(content), inline)}
        </blockquote>
      );
    case "image": {
      const src = String(content);
      const alt = String(block.data.attributes.alt ?? "");
      if (VIDEO_RE.test(src)) {
        return (
          <figure key={block.id} className="bip-media">
            {/* biome-ignore lint/a11y/useMediaCaption: caption is the optional alt/figcaption. */}
            <video className="bip-video" src={src} controls preload="metadata" />
            {alt ? <figcaption>{alt}</figcaption> : null}
          </figure>
        );
      }
      return (
        <figure key={block.id} className="bip-media">
          <img className="bip-img" src={src} alt={alt} loading="lazy" />
          {alt ? <figcaption>{alt}</figcaption> : null}
        </figure>
      );
    }
    case "list": {
      const items = Array.isArray(content) ? (content as unknown[]) : [];
      const ordered = Boolean(block.data.attributes.ordered);
      return ordered ? (
        <ol key={block.id}>
          {items.map((it, i) => (
            <li key={`${block.id}-${i}`}>{renderInline(String(it), inline)}</li>
          ))}
        </ol>
      ) : (
        <ul key={block.id}>
          {items.map((it, i) => (
            <li key={`${block.id}-${i}`}>{renderInline(String(it), inline)}</li>
          ))}
        </ul>
      );
    }
    case "code": {
      const language = String(block.data.attributes.language ?? "text");
      const code = String(content);
      if (DIAGRAM_LANGS.has(language)) {
        return <Diagram key={block.id} code={code} language={language} />;
      }
      if (language === "python" && allowPythonRun && !THIRD_PARTY_PY.test(code)) {
        return (
          <CodeRunner
            key={block.id}
            initialCode={code}
            language={language}
            lessonId={lessonId}
            blockId={block.id}
            authenticated={authenticated}
          />
        );
      }
      return (
        <pre key={block.id} className="bip-pre" data-lang={language}>
          <code>{code}</code>
        </pre>
      );
    }
    default:
      return <p key={(block as Block).id} />;
  }
}

export interface LessonReaderProps {
  revision: Revision;
  authenticated: boolean;
  lessonId: string | null;
  inline: InlineOptions;
  /** When false, python fences render as display-only (LangGraph/LangChain snippets). */
  allowPythonRun?: boolean;
}

export function LessonReader({
  revision,
  authenticated,
  lessonId,
  inline,
  allowPythonRun = true,
}: LessonReaderProps): React.JSX.Element {
  const { body } = splitBannerBlocks(revision.blocks);

  return (
    <article className="bip-article" aria-labelledby="lesson-title">
      <h1 id="lesson-title">{renderInline(cleanLessonTitle(revision.title), inline)}</h1>
      {body.map((b) => renderBlock(b, lessonId, authenticated, inline, allowPythonRun))}
    </article>
  );
}
