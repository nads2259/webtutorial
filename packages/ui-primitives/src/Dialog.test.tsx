import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./Button";
import { Dialog } from "./Dialog";

describe("Dialog (WCAG 2.1.1 / 2.1.2 / 2.4.3 / 4.1.2)", () => {
  it("exposes a modal dialog labelled by its title", () => {
    render(
      <Dialog open onClose={() => {}} title="Confirm delete">
        <p>Are you sure?</p>
      </Dialog>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleName("Confirm delete");
  });

  it("renders nothing when closed", () => {
    render(
      <Dialog open={false} onClose={() => {}} title="Hidden">
        <p>content</p>
      </Dialog>,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("moves focus into the dialog on open", () => {
    render(
      <Dialog open onClose={() => {}} title="Settings">
        <Button>First</Button>
        <Button>Second</Button>
      </Dialog>,
    );
    expect(screen.getByRole("button", { name: "First" })).toHaveFocus();
  });

  it("closes on Escape (no keyboard trap, WCAG 2.1.2)", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <Dialog open onClose={onClose} title="Settings">
        <Button>Only</Button>
      </Dialog>,
    );
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("traps Tab focus within the dialog", async () => {
    const user = userEvent.setup();
    render(
      <Dialog open onClose={() => {}} title="Settings">
        <Button>First</Button>
        <Button>Last</Button>
      </Dialog>,
    );
    const first = screen.getByRole("button", { name: "First" });
    const last = screen.getByRole("button", { name: "Last" });
    expect(first).toHaveFocus();
    await user.tab();
    expect(last).toHaveFocus();
    await user.tab();
    expect(first).toHaveFocus();
  });

  it("restores focus to the opener on close", async () => {
    function Harness(): React.JSX.Element {
      const [open, setOpen] = useState(false);
      return (
        <>
          <Button onClick={() => setOpen(true)}>Open</Button>
          <Dialog open={open} onClose={() => setOpen(false)} title="Settings">
            <Button onClick={() => setOpen(false)}>Done</Button>
          </Dialog>
        </>
      );
    }
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    await user.click(opener);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(opener).toHaveFocus();
  });

  it("has zero serious/critical axe violations", async () => {
    const { container } = render(
      <Dialog open onClose={() => {}} title="Confirm">
        <p>Body text</p>
        <Button>OK</Button>
      </Dialog>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
