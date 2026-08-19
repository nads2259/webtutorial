import { Fragment } from "react";
import { cleanLessonTitle } from "./tree";

/** Compact global map of lesson id -> { r: revisionId, t: title }. */
export type LessonIndex = Record<string, { r: string; t: string }>;

export interface InlineOptions {
  index: LessonIndex;
  /** category code -> human label (e.g. "C00" -> "Orientation and Learning System"). */
  catLabels: Record<string, string>;
  /** The lesson id of the article currently being read (upper-case), for self-references. */
  currentLessonId: string | null;
}

// A curriculum identifier: PY-C00 (category), PY-C00-M01 (module), PY-C00-M01-L01 (lesson).
const ID_RE = /\bPY-C(\d+)(?:-M(\d+)(?:-L(\d+))?)?\b/gi;

function moduleOf(lessonId: string | null): string | null {
  return lessonId ? lessonId.replace(/-L\d+$/i, "").toUpperCase() : null;
}

/** Turn a single curriculum id into a human hyperlink (or a plain self-reference). */
function refNode(
  match: string,
  cNum: string,
  mNum: string | undefined,
  lNum: string | undefined,
  opts: InlineOptions,
  key: string,
): React.ReactNode {
  const category = `C${cNum}`;
  const id = match.toUpperCase();

  if (lNum) {
    if (opts.currentLessonId && id === opts.currentLessonId) {
      return (
        <em key={key} className="bip-xref bip-xref--self">
          this lesson
        </em>
      );
    }
    const entry = opts.index[id];
    if (entry) {
      return (
        <a key={key} className="bip-xref" href={`/l/${category}/${entry.r}`}>
          {cleanLessonTitle(entry.t)}
        </a>
      );
    }
    return null; // unknown lesson -> leave the original text untouched
  }

  if (mNum) {
    if (moduleOf(opts.currentLessonId) === id) {
      return (
        <em key={key} className="bip-xref bip-xref--self">
          this module
        </em>
      );
    }
    const label = opts.catLabels[category];
    return (
      <a key={key} className="bip-xref" href={`/c/${category}`}>
        {label ? label : `Course ${cNum}`}
      </a>
    );
  }

  const label = opts.catLabels[category];
  return (
    <a key={key} className="bip-xref" href={`/c/${category}`}>
      {label ? label : `Course ${cNum}`}
    </a>
  );
}

/** Markdown `[label](/path)` or `[label](https://...)` plus PY-Cxx curriculum ids. */
const MD_LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]+)\)/g;

function renderTextRun(text: string, opts: InlineOptions, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let n = 0;
  MD_LINK_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  // biome-ignore lint/suspicious/noAssignInExpressions: standard regex exec loop
  while ((m = MD_LINK_RE.exec(text)) !== null) {
    if (m.index > last) {
      nodes.push(...linkIds(text.slice(last, m.index), opts, `${keyBase}-b${n}`));
    }
    const href = m[2];
    const external = href.startsWith("http");
    nodes.push(
      <a
        key={`${keyBase}-a${n}`}
        className="bip-xref"
        href={href}
        {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      >
        {m[1]}
      </a>,
    );
    last = m.index + m[0].length;
    n += 1;
  }
  if (last < text.length) nodes.push(...linkIds(text.slice(last), opts, `${keyBase}-e`));
  if (nodes.length === 0) nodes.push(...linkIds(text, opts, keyBase));
  return nodes;
}
function linkIds(text: string, opts: InlineOptions, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  ID_RE.lastIndex = 0;
  let n = 0;
  // biome-ignore lint/suspicious/noAssignInExpressions: standard regex exec loop
  while ((m = ID_RE.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const node = refNode(m[0], m[1], m[2], m[3], opts, `${keyBase}-x${n}`);
    nodes.push(node === null ? m[0] : node);
    last = m.index + m[0].length;
    n += 1;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/**
 * Render an inline text run: parse `**bold**` and `` `code` ``, and turn curriculum ids (in code or
 * bare text) into human-friendly cross-reference hyperlinks. Everything else is preserved as text.
 */
export function renderInline(text: string, opts: InlineOptions): React.ReactNode {
  if (!text) return text;
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter((p) => p.length > 0);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={i}>{renderTextRun(part.slice(2, -2), opts, `b${i}`)}</strong>;
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          const inner = part.slice(1, -1);
          const single = ID_RE.test(inner) && inner.replace(ID_RE, "").trim() === "";
          ID_RE.lastIndex = 0;
          if (single) {
            // A code span that is exactly a curriculum id -> render as a link, not mono code.
            return <Fragment key={i}>{linkIds(inner, opts, `c${i}`)}</Fragment>;
          }
          return <code key={i}>{inner}</code>;
        }
        return <Fragment key={i}>{renderTextRun(part, opts, `t${i}`)}</Fragment>;
      })}
    </>
  );
}
