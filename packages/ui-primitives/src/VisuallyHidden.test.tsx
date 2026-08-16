import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { VisuallyHidden } from "./VisuallyHidden";

describe("VisuallyHidden (WCAG 1.3.1 / 2.4.4)", () => {
  it("keeps content in the accessibility tree", () => {
    render(<VisuallyHidden>Screen reader only</VisuallyHidden>);
    expect(screen.getByText("Screen reader only")).toBeInTheDocument();
  });

  it("has zero serious/critical axe violations", async () => {
    const { container } = render(
      <button type="button">
        <VisuallyHidden>Close</VisuallyHidden>
      </button>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
