import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { SimulationPage } from "./SimulationPage";
import { SimulationRunner } from "./SimulationRunner";
import { referenceSimulation } from "./simulation/reference-simulation";

function renderRunner() {
  return render(<SimulationRunner definition={referenceSimulation} />);
}

describe("SimulationRunner accessibility semantics", () => {
  it("exposes a named region, objective description and the first step (WCAG 1.3.1 / 2.4.6)", () => {
    renderRunner();
    const region = screen.getByRole("region", { name: referenceSimulation.title });
    expect(region).toHaveAttribute("aria-describedby");
    expect(
      screen.getByRole("heading", { level: 3, name: /step 1 of 3: detect and confirm/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("toolbar", { name: "Simulation controls" })).toBeInTheDocument();
  });

  it("renders every action as a keyboard-operable button (WCAG 2.1.1 / 4.1.2)", () => {
    renderRunner();
    expect(
      screen.getByRole("button", { name: /confirm the alert against the latency/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /immediately restart all checkout instances/i }),
    ).toBeInTheDocument();
  });

  it("provides a polite live region for status messages (WCAG 4.1.3)", () => {
    renderRunner();
    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
  });

  it("documents a keyboard shortcut map", () => {
    renderRunner();
    const restartTerm = screen.getByText("Alt+R");
    expect(restartTerm.tagName).toBe("DT");
    expect(screen.getByText("Restart the simulation")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: "Keyboard shortcuts" })).toBeInTheDocument();
  });
});

describe("SimulationRunner keyboard operability", () => {
  it("places controls in a logical tab order (WCAG 2.4.3)", async () => {
    const user = userEvent.setup();
    renderRunner();
    await user.tab();
    expect(screen.getByRole("button", { name: /restart simulation/i })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: /show text-only equivalent/i })).toHaveFocus();
    await user.tab();
    expect(
      screen.getByRole("button", { name: /confirm the alert against the latency/i }),
    ).toHaveFocus();
  });

  it("activates a choice with Enter and moves focus to the new step (focus management)", async () => {
    const user = userEvent.setup();
    renderRunner();
    screen.getByRole("button", { name: /confirm the alert against the latency/i }).focus();
    await user.keyboard("{Enter}");
    const heading = screen.getByRole("heading", { level: 3, name: /step 2 of 3: choose a mitigation/i });
    expect(heading).toHaveFocus();
  });

  it("activates a choice with Space", async () => {
    const user = userEvent.setup();
    renderRunner();
    await user.click(screen.getByRole("button", { name: /confirm the alert against the latency/i }));
    const rollback = screen.getByRole("button", { name: /roll back the pricing-rule/i });
    rollback.focus();
    await user.keyboard("[Space]");
    expect(
      screen.getByRole("heading", { level: 3, name: /step 3 of 3: verify recovery/i }),
    ).toBeInTheDocument();
  });

  it("does not trap keyboard focus (WCAG 2.1.2)", async () => {
    const user = userEvent.setup();
    renderRunner();
    const secondChoice = screen.getByRole("button", {
      name: /immediately restart all checkout instances/i,
    });
    secondChoice.focus();
    await user.tab();
    expect(secondChoice).not.toHaveFocus();
  });

  it("announces the transition and the new location in the live region (WCAG 4.1.3)", async () => {
    const user = userEvent.setup();
    renderRunner();
    await user.click(screen.getByRole("button", { name: /confirm the alert against the latency/i }));
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/cpu is saturated/i);
    expect(status).toHaveTextContent(/step 2 of 3/i);
  });

  it("restarts via the Alt+R shortcut without intercepting typing (WCAG 2.1.4)", async () => {
    const user = userEvent.setup();
    renderRunner();
    await user.click(screen.getByRole("button", { name: /confirm the alert against the latency/i }));
    expect(
      screen.getByRole("heading", { level: 3, name: /step 2 of 3/i }),
    ).toBeInTheDocument();
    await user.keyboard("{Alt>}r{/Alt}");
    expect(
      screen.getByRole("heading", { level: 3, name: /step 1 of 3: detect and confirm/i }),
    ).toBeInTheDocument();
  });
});

describe("SimulationRunner alternative text-output path", () => {
  it("reveals a non-visual equivalent of the whole state and outcome", async () => {
    const user = userEvent.setup();
    renderRunner();
    const toggle = screen.getByRole("button", { name: /show text-only equivalent/i });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const region = screen.getByRole("region", { name: /text-only equivalent/i });
    expect(within(region).getByText(new RegExp(referenceSimulation.objective.slice(0, 20), "i"))).toBeInTheDocument();
    expect(within(region).getByText(/Available actions:/)).toBeInTheDocument();
  });

  it("toggles the text-only view with the Alt+T shortcut", async () => {
    const user = userEvent.setup();
    renderRunner();
    const toggle = screen.getByRole("button", { name: /show text-only equivalent/i });
    toggle.focus();
    await user.keyboard("{Alt>}t{/Alt}");
    expect(screen.getByRole("region", { name: /text-only equivalent/i })).toBeInTheDocument();
  });
});

describe("SimulationPage a11y (axe-core)", () => {
  it("has zero serious/critical axe violations on initial render", async () => {
    const { container } = render(<SimulationPage />);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it("has zero serious/critical axe violations after a state change", async () => {
    const user = userEvent.setup();
    const { container } = render(<SimulationPage />);
    await user.click(screen.getByRole("button", { name: /confirm the alert against the latency/i }));
    await user.click(screen.getByRole("button", { name: /roll back the pricing-rule/i }));
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it("provides page landmarks and a single h1 (WCAG 1.3.1 / 2.4.1)", () => {
    render(<SimulationPage />);
    expect(screen.getByRole("banner")).toBeInTheDocument();
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });
});
