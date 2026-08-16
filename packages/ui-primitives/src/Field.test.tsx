import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { Field } from "./Field";

describe("Field (WCAG 1.3.1 / 3.3.1 / 3.3.2 / 4.1.2)", () => {
  it("associates the label with the input", () => {
    render(<Field label="Email" type="email" />);
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("marks the input invalid and links the error message", () => {
    render(<Field label="Email" error="Enter a valid email" />);
    const input = screen.getByLabelText("Email");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const error = screen.getByRole("alert");
    expect(input).toHaveAttribute("aria-describedby", expect.stringContaining(error.id));
  });

  it("links helper description via aria-describedby", () => {
    render(<Field label="Password" description="At least 12 characters" />);
    const input = screen.getByLabelText("Password");
    expect(input.getAttribute("aria-describedby")).toBeTruthy();
  });

  it("has zero serious/critical axe violations (valid and error states)", async () => {
    const { container } = render(
      <>
        <Field label="Name" />
        <Field label="Email" error="Required" />
      </>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
