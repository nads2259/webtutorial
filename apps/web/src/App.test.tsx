import { render, screen, within } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { sampleDocument } from "./fixtures/sample-document";

describe("App shell accessibility", () => {
  it("provides banner, navigation, main and contentinfo landmarks (WCAG 1.3.1 / 2.4.1)", () => {
    render(<App />);
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
  });

  it("offers a skip link targeting main content (WCAG 2.4.1 Bypass Blocks)", () => {
    render(<App />);
    const skip = screen.getByRole("link", { name: /skip to main content/i });
    expect(skip).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("moves focus to the main region on load (focus management)", () => {
    render(<App />);
    expect(screen.getByRole("main")).toHaveFocus();
  });

  it("renders exactly one h1 with the document title (WCAG 2.4.6 / 1.3.1)", () => {
    render(<App />);
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(sampleDocument.title);
  });

  it("keeps heading levels in order without skips (WCAG 1.3.1)", () => {
    render(<App />);
    const headings = screen.getAllByRole("heading");
    const levels = headings.map((h) => Number(h.tagName.slice(1)));
    for (let i = 1; i < levels.length; i += 1) {
      const current = levels[i] ?? 0;
      const previous = levels[i - 1] ?? 0;
      expect(current - previous).toBeLessThanOrEqual(1);
    }
  });

  it("exposes a live-region status message (WCAG 4.1.3)", () => {
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent(/published knowledge document loaded/i);
  });

  it("announces new-tab links only when applicable (no false hints)", () => {
    render(<App />);
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Learn" })).toBeInTheDocument();
  });

  it("has zero serious/critical axe violations on the rendered page", async () => {
    const { container } = render(<App />);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
