import { color, motion, size, space, type as typeTokens } from "@northstar/design-tokens";
import { type Block, StructuredEditor } from "@northstar/editor-adapter";
import { Link, Status, VisuallyHidden } from "@northstar/ui-primitives";
import type { CSSProperties } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

/**
 * Authoring route: the visual structured editor for the canonical typed block
 * tree (FR-CNT-003). Authors edit blocks with keyboard-operable add/move/delete
 * controls; the document outline reflects the live typed tree. The editor is an
 * authorized client of the content capability — it only ever produces the typed
 * block tree, never arbitrary HTML/MDX (LAW-05, LAW-08).
 */

const INITIAL_BLOCKS: readonly Block[] = [
  {
    id: "blk-authoring-00001",
    type: "heading",
    version: 1,
    // Level 2: the page chrome owns the single <h1> ("Authoring"); the draft's
    // own top heading is a section within the editing canvas, so the view keeps
    // exactly one <h1> (WCAG 2.4.6 / 1.3.1).
    data: { level: 2, text: "Draft: Understanding Effects" },
  },
  {
    id: "blk-authoring-00002",
    type: "paragraph",
    version: 1,
    data: { text: "Effects let a component synchronize with an external system." },
  },
  {
    id: "blk-authoring-00003",
    type: "list",
    version: 1,
    data: { ordered: false, items: ["Set up the subscription", "React to changes", "Clean up"] },
  },
];

const skipLinkStyle: CSSProperties = {
  position: "absolute",
  left: space.sm,
  top: space.sm,
  padding: space.sm,
  background: color.surface,
  color: color.primary,
  border: `${size.focusRingWidthPx}px solid ${color.focusRing}`,
  borderRadius: 4,
  transform: "translateY(-200%)",
  transition: `transform ${motion.durationFastMs}ms ${motion.easingStandard}`,
  zIndex: 10,
};

const shellStyle: CSSProperties = {
  fontFamily: typeTokens.fontFamily,
  fontSize: typeTokens.baseSizePx,
  lineHeight: typeTokens.lineHeight,
  color: color.text,
  background: color.surface,
  minHeight: "100vh",
};

const contentStyle: CSSProperties = {
  maxWidth: 820,
  margin: "0 auto",
  padding: space.lg,
};

const outlineStyle: CSSProperties = {
  marginTop: space.lg,
  padding: space.md,
  border: `1px solid ${color.border}`,
  borderRadius: 4,
  background: color.surfaceMuted,
};

function excerpt(block: Block): string {
  switch (block.type) {
    case "heading":
      return `H${block.data.level}: ${block.data.text}`;
    case "paragraph":
    case "quote":
      return block.data.text;
    case "code":
      return block.data.language ? `${block.data.language} code` : "code";
    case "image":
      return block.data.alt ? `image — ${block.data.alt}` : "image";
    case "list":
      return `${block.data.ordered ? "ordered" : "bulleted"} list (${block.data.items.length})`;
    default: {
      const _exhaustive: never = block;
      return _exhaustive;
    }
  }
}

export function AuthoringPage(): React.JSX.Element {
  const mainRef = useRef<HTMLElement>(null);
  const [blocks, setBlocks] = useState<Block[]>(() => INITIAL_BLOCKS.slice());

  useEffect(() => {
    document.title = "Authoring — Northstar Knowledge";
    mainRef.current?.focus();
  }, []);

  const outline = useMemo(
    () => blocks.map((block) => ({ id: block.id, type: block.type, text: excerpt(block) })),
    [blocks],
  );

  return (
    <div style={shellStyle}>
      <a href="#authoring-main" style={skipLinkStyle} data-testid="skip-link">
        Skip to editor
      </a>
      <header>
        <nav aria-label="Primary">
          <ul>
            <li>
              <Link href="#/">Read</Link>
            </li>
            <li>
              <Link href="#/authoring">Author</Link>
            </li>
          </ul>
        </nav>
      </header>
      <main id="authoring-main" ref={mainRef} tabIndex={-1} style={contentStyle} aria-labelledby="authoring-title">
        <h1 id="authoring-title">Authoring</h1>
        <p>
          Edit the structured document below. Use the toolbar (or Alt+Arrow keys) to add, reorder
          and remove blocks without a mouse.
        </p>
        <StructuredEditor initialBlocks={INITIAL_BLOCKS} onChange={setBlocks} label="Document body" />
        <section style={outlineStyle} aria-labelledby="outline-title">
          <h2 id="outline-title">Document outline</h2>
          <Status>{`${blocks.length} blocks in draft`}</Status>
          <ol>
            {outline.map((item) => (
              <li key={item.id}>
                <VisuallyHidden>{item.type}: </VisuallyHidden>
                {item.text}
              </li>
            ))}
          </ol>
        </section>
      </main>
      <footer>
        <p>
          <VisuallyHidden>Footer: </VisuallyHidden>
          Northstar authoring environment.
        </p>
      </footer>
    </div>
  );
}
