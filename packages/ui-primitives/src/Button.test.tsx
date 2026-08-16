import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Button, type ButtonProps } from "./Button";
import { VisuallyHidden } from "./VisuallyHidden";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Button (WCAG 4.1.2 Name, Role, Value / 2.1.1 Keyboard)", () => {
  it("renders a native button with an accessible name from text", () => {
    render(<Button>Save changes</Button>);
    expect(screen.getByRole("button", { name: "Save changes" })).toBeInTheDocument();
  });

  it("defaults to type=button to avoid accidental form submission", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });

  it("accepts an icon-only button when aria-label is provided", () => {
    render(
      <Button aria-label="Close">
        <VisuallyHidden>Close</VisuallyHidden>
      </Button>,
    );
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("has zero serious/critical axe violations", async () => {
    const { container } = render(<Button>Continue</Button>);
    expect(await axe(container)).toHaveNoViolations();
  });

  it("FLAGS an accessible-name violation at runtime for JS bypass (NFR-A11Y-002)", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    // Bypass the compile-time union to simulate an untyped (JS) consumer.
    render(<Button {...({} as ButtonProps)} />);
    expect(spy).toHaveBeenCalledWith(
      expect.stringContaining("Button has no accessible name"),
    );
  });

  it("does NOT warn when an accessible name is present", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Button>OK</Button>);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("Button type-level guard (NFR-A11Y-002)", () => {
  it("rejects an icon-only button without an accessible name", () => {
    // @ts-expect-error icon-only button requires an aria-label at compile time.
    const invalid: ButtonProps = {};
    void invalid;
    expect(true).toBe(true);
  });
});
