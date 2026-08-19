import { useEffect } from "react";

const SITE_NAME = "Bestinfopages";
const DEFAULT_DESC =
  "A complete, hands-on Python curriculum — from fundamentals to AI systems engineering. Read, search, and run real Python in a sandbox.";

export interface SeoProps {
  title: string;
  description?: string;
  /** Path (e.g. "/l/C00/abc") used to build the canonical URL. */
  path: string;
  /** Optional schema.org JSON-LD object. */
  jsonLd?: Record<string, unknown>;
  type?: "website" | "article";
}

function upsertMeta(attr: "name" | "property", key: string, content: string): void {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

function upsertLink(rel: string, href: string): void {
  let el = document.head.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`);
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", rel);
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
}

/**
 * Per-page SEO: sets the document title, meta description, canonical URL, Open Graph / Twitter tags,
 * and optional JSON-LD structured data. Client-rendered head that modern crawlers (Google) index.
 */
export function Seo({
  title,
  description = DEFAULT_DESC,
  path,
  jsonLd,
  type = "website",
}: SeoProps): null {
  useEffect(() => {
    const fullTitle = title.includes(SITE_NAME) ? title : `${title} — ${SITE_NAME}`;
    const origin = typeof window !== "undefined" ? window.location.origin : "";
    const canonical = `${origin}${path}`;
    document.title = fullTitle;
    upsertMeta("name", "description", description);
    upsertLink("canonical", canonical);
    upsertMeta("property", "og:title", fullTitle);
    upsertMeta("property", "og:description", description);
    upsertMeta("property", "og:type", type);
    upsertMeta("property", "og:url", canonical);
    upsertMeta("property", "og:site_name", SITE_NAME);
    upsertMeta("name", "twitter:card", "summary");
    upsertMeta("name", "twitter:title", fullTitle);
    upsertMeta("name", "twitter:description", description);

    const id = "bip-jsonld";
    document.getElementById(id)?.remove();
    if (jsonLd) {
      const script = document.createElement("script");
      script.type = "application/ld+json";
      script.id = id;
      script.textContent = JSON.stringify(jsonLd);
      document.head.appendChild(script);
    }
    return () => {
      document.getElementById(id)?.remove();
    };
  }, [title, description, path, type, jsonLd]);

  return null;
}
