import { fireEvent, render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { AuthoringPage } from "./AuthoringPage";

describe("AuthoringPage integration", () => {
  it("renders authoring landmarks, editor and live outline (WCAG 1.3.1 / 2.4.1)", () => {
    render(<AuthoringPage />);
    expect(screen.getByRole("main")).toHaveAttribute("id", "authoring-main");
    expect(screen.getByRole("heading", { level: 1, name: "Authoring" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Document body" })).toBeInTheDocument();
    expect(screen.getByRole("toolbar", { name: "Block operations" })).toBeInTheDocument();
    expect(screen.getByText(/3 blocks in draft/i)).toBeInTheDocument();
  });

  it("updates the live outline when a block is added via keyboard-operable controls", () => {
    render(<AuthoringPage />);
    fireEvent.click(screen.getByRole("button", { name: "Add Paragraph" }));
    expect(screen.getByText(/4 blocks in draft/i)).toBeInTheDocument();
  });

  it("has zero serious/critical axe violations", async () => {
    const { container } = render(<AuthoringPage />);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
