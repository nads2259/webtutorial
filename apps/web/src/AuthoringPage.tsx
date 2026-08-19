import { type Block, StructuredEditor } from "@northstar/editor-adapter";
import { Status, VisuallyHidden } from "@northstar/ui-primitives";
import { useEffect, useMemo, useRef, useState } from "react";
import { SiteFooter } from "./components/SiteFooter";
import { SiteHeader } from "./components/SiteHeader";
import { SkipLink } from "./components/SkipLink";

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
    document.title = "Authoring — Bestinfopages";
    mainRef.current?.focus();
  }, []);

  const outline = useMemo(
    () => blocks.map((block) => ({ id: block.id, type: block.type, text: excerpt(block) })),
    [blocks],
  );

  return (
    <div className="ns-shell">
      <SkipLink target="authoring-main">Skip to editor</SkipLink>
      <SiteHeader
        items={[
          { label: "Read", href: "#/" },
          { label: "Author", href: "#/authoring", current: true },
          { label: "Simulation", href: "#/simulation" },
        ]}
        cta={{ label: "Get started", href: "#/authoring" }}
      />
      <main
        id="authoring-main"
        ref={mainRef}
        tabIndex={-1}
        className="ns-main ns-main--wide"
        aria-labelledby="authoring-title"
      >
        <div className="bip-kicker">
          <span className="bip-chip bip-chip--edit">Structured editor</span>
          <span className="bip-meta-sep" aria-hidden="true" />
          <span>Keyboard-first authoring</span>
        </div>
        <h1 id="authoring-title">Authoring</h1>
        <p>
          Edit the structured document below. Use the toolbar (or Alt+Arrow keys) to add, reorder
          and remove blocks without a mouse.
        </p>
        <div className="ns-panel">
          <StructuredEditor
            initialBlocks={INITIAL_BLOCKS}
            onChange={setBlocks}
            label="Document body"
          />
        </div>
        <section className="ns-panel ns-outline" aria-labelledby="outline-title">
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
      <SiteFooter>
        The Bestinfopages structured editor — produce clean, typed content, never arbitrary markup.
      </SiteFooter>
    </div>
  );
}
