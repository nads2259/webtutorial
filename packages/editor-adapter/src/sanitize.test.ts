import { describe, expect, it } from "vitest";
import { htmlToBlocks } from "./convert";
import { sanitizeUrl } from "./sanitize";

describe("sanitizeUrl (LAW-08 deny-by-default)", () => {
  it("allows http, https, root-relative, anchor and mailto references", () => {
    expect(sanitizeUrl("https://example.com/a")).toBe("https://example.com/a");
    expect(sanitizeUrl("http://example.com")).toBe("http://example.com");
    expect(sanitizeUrl("/local/path")).toBe("/local/path");
    expect(sanitizeUrl("#section")).toBe("#section");
    expect(sanitizeUrl("mailto:a@b.com")).toBe("mailto:a@b.com");
  });

  it("collapses dangerous or malformed schemes to an empty string", () => {
    expect(sanitizeUrl("javascript:alert(1)")).toBe("");
    expect(sanitizeUrl("  jAvAsCrIpT:alert(1)")).toBe("");
    expect(sanitizeUrl("java\nscript:alert(1)")).toBe("");
    expect(sanitizeUrl("data:text/html,<script>alert(1)</script>")).toBe("");
    expect(sanitizeUrl("vbscript:msgbox")).toBe("");
    expect(sanitizeUrl("//evil.example.com")).toBe("");
    expect(sanitizeUrl(42)).toBe("");
    expect(sanitizeUrl("")).toBe("");
  });
});

describe("htmlToBlocks neutralizes malicious markup (LAW-08, FR-CNT-003)", () => {
  it("drops scripts, event handlers and executable URLs", () => {
    const html = [
      "<p>Safe text</p>",
      "<script>window.stolen=document.cookie</script>",
      '<img src="javascript:alert(1)" onerror="alert(1)" alt="evil">',
      '<p onclick="alert(2)">More text</p>',
      '<a href="javascript:alert(3)">link</a>',
    ].join("");

    const blocks = htmlToBlocks(html);
    const serialized = JSON.stringify(blocks);

    expect(serialized).not.toContain("javascript:");
    expect(serialized).not.toContain("onerror");
    expect(serialized).not.toContain("onclick");
    expect(serialized.toLowerCase()).not.toContain("<script");

    for (const block of blocks) {
      expect(["heading", "paragraph", "code", "quote", "image", "list"]).toContain(block.type);
    }

    const safe = blocks.find(
      (block) => block.type === "paragraph" && block.data.text.includes("Safe text"),
    );
    expect(safe).toBeDefined();
  });

  it("keeps a pasted image only with a sanitized src", () => {
    const blocks = htmlToBlocks('<img src="javascript:alert(1)" alt="bad">');
    const image = blocks.find((block) => block.type === "image");
    expect(image).toBeDefined();
    if (image?.type === "image") {
      expect(image.data.src).toBe("");
      expect(image.data.alt).toBe("bad");
    }
  });

  it("preserves a safe pasted image src", () => {
    const blocks = htmlToBlocks('<img src="https://example.com/ok.png" alt="ok">');
    const image = blocks.find((block) => block.type === "image");
    expect(image?.type).toBe("image");
    if (image?.type === "image") {
      expect(image.data.src).toBe("https://example.com/ok.png");
    }
  });
});
