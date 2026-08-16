import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { Status } from "./Status";

describe("Status (WCAG 4.1.3 Status Messages)", () => {
  it("exposes a polite live region by default", () => {
    render(<Status>Saved</Status>);
    const region = screen.getByRole("status");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveTextContent("Saved");
  });

  it("supports assertive urgency", () => {
    render(<Status urgency="assertive">Upload failed</Status>);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "assertive");
  });

  it("has zero serious/critical axe violations", async () => {
    const { container } = render(<Status>Loading complete</Status>);
    expect(await axe(container)).toHaveNoViolations();
  });
});
