import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { Link } from "./Link";

describe("Link (WCAG 2.4.4 Link Purpose / 4.1.2)", () => {
  it("renders an anchor with href and accessible name", () => {
    render(<Link href="/docs">Read the docs</Link>);
    const link = screen.getByRole("link", { name: /read the docs/i });
    expect(link).toHaveAttribute("href", "/docs");
  });

  it("adds safe rel and a new-tab hint for target=_blank", () => {
    render(
      <Link href="https://example.com" target="_blank">
        External
      </Link>,
    );
    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
    expect(link).toHaveAccessibleName(/opens in a new tab/i);
  });

  it("has zero serious/critical axe violations", async () => {
    const { container } = render(<Link href="/home">Home</Link>);
    expect(await axe(container)).toHaveNoViolations();
  });
});
