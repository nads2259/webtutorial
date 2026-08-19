import { useEffect, useRef, useState } from "react";

let mermaidInited = false;
let seq = 0;

async function renderMermaid(code: string, id: string): Promise<string> {
  const mermaid = (await import("mermaid")).default;
  if (!mermaidInited) {
    mermaid.initialize({
      startOnLoad: false,
      theme: "neutral",
      securityLevel: "strict",
      fontFamily: "Inter, system-ui, sans-serif",
    });
    mermaidInited = true;
  }
  const { svg } = await mermaid.render(id, code);
  return svg;
}

async function renderGraphviz(code: string): Promise<string> {
  const { instance } = await import("@viz-js/viz");
  const viz = await instance();
  const el = viz.renderSVGElement(code);
  return el.outerHTML;
}

export interface DiagramProps {
  code: string;
  language: string; // "mermaid" | "dot" | "graphviz"
}

/** Renders a mermaid or graphviz (dot) diagram to inline SVG, lazy-loading the renderer. */
export function Diagram({ code, language }: DiagramProps): React.JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [svg, setSvg] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setSvg(null);
    const run = async (): Promise<void> => {
      try {
        const out =
          language === "mermaid"
            ? await renderMermaid(code, `bip-mmd-${(seq += 1)}`)
            : await renderGraphviz(code);
        if (!cancelled) setSvg(out);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to render diagram");
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [code, language]);

  if (error) {
    // Fall back to the source so nothing is lost when a diagram can't be parsed.
    return (
      <figure className="bip-diagram bip-diagram--error">
        <pre className="bip-pre" data-lang={language}>
          <code>{code}</code>
        </pre>
        <figcaption>Diagram could not be rendered: {error}</figcaption>
      </figure>
    );
  }

  return (
    <figure
      className="bip-diagram"
      ref={ref}
      // biome-ignore lint/security/noDangerouslySetInnerHtml: mermaid/viz output, securityLevel strict.
      dangerouslySetInnerHTML={svg ? { __html: svg } : undefined}
    >
      {svg ? undefined : <span className="bip-diagram__loading">Rendering diagram…</span>}
    </figure>
  );
}
