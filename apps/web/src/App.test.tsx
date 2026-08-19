import { render, screen, within } from "@testing-library/react";
import { axe } from "jest-axe";
import { beforeEach, describe, expect, it } from "vitest";
import { App } from "./App";

// The docs shell fetches session/categories on mount; in jsdom those requests just fail and are
// swallowed, so the shell renders its accessible skeleton. Reset to the home path between tests.
beforeEach(() => {
  window.history.pushState({}, "", "/");
});

describe("Docs app shell accessibility", () => {
  it("provides banner, navigation, main and contentinfo landmarks (WCAG 1.3.1 / 2.4.1)", () => {
    render(<App />);
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
  });

  it("exposes the Lessons navigation landmark on a course route (WCAG 2.4.1)", () => {
    // The landing is intentionally full-width with no sidebar; the "Lessons" navigation appears once
    // a course/topic is selected.
    window.history.pushState({}, "", "/c/C00");
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Lessons" })).toBeInTheDocument();
  });

  it("offers a skip link that is the first focusable element and targets main (WCAG 2.4.1)", () => {
    const { container } = render(<App />);
    const skip = screen.getByRole("link", { name: /skip to main content/i });
    expect(skip).toHaveAttribute("href", "#main-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
    const focusables = Array.from(
      container.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input, [tabindex]:not([tabindex="-1"])',
      ),
    );
    expect(focusables[0]).toBe(skip);
  });

  it("moves focus to the main region on load (focus management)", () => {
    render(<App />);
    expect(screen.getByRole("main")).toHaveFocus();
  });

  it("renders exactly one h1 on the home view (WCAG 2.4.6 / 1.3.1)", () => {
    render(<App />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it("exposes a labelled search box in the header (docs-site search)", () => {
    render(<App />);
    const banner = screen.getByRole("banner");
    expect(within(banner).getByRole("searchbox", { name: /search lessons/i })).toBeInTheDocument();
  });

  it("keeps heading levels in order without skips (WCAG 1.3.1)", () => {
    render(<App />);
    const levels = screen.getAllByRole("heading").map((h) => Number(h.tagName.slice(1)));
    for (let i = 1; i < levels.length; i += 1) {
      expect((levels[i] ?? 0) - (levels[i - 1] ?? 0)).toBeLessThanOrEqual(1);
    }
  });

  it("has zero serious/critical axe violations on the rendered page", async () => {
    const { container } = render(<App />);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
