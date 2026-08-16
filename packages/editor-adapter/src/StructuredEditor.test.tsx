import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { beforeEach, describe, expect, it } from "vitest";
import type { Block } from "./blocks";
import { StructuredEditor } from "./StructuredEditor";

const seed: readonly Block[] = [
  { id: "blk-seed-00000001", type: "heading", version: 1, data: { level: 1, text: "Seed heading" } },
  { id: "blk-seed-00000002", type: "paragraph", version: 1, data: { text: "Seed paragraph" } },
];

describe("StructuredEditor accessibility and keyboard block operations", () => {
  let latest: Block[];

  beforeEach(() => {
    latest = seed.slice();
  });

  function renderEditor() {
    return render(<StructuredEditor initialBlocks={seed} onChange={(blocks) => {
      latest = blocks;
    }} />);
  }

  it("exposes a labelled, keyboard-operable editing region and toolbar (WCAG 2.1.1 / 4.1.2)", () => {
    renderEditor();
    expect(screen.getByRole("textbox", { name: "Document body" })).toBeInTheDocument();
    expect(screen.getByRole("toolbar", { name: "Block operations" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move up" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Move down" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete block" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("has zero serious/critical axe violations", async () => {
    const { container } = renderEditor();
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it("inserts a block after the current one, preserving existing stable ids (WCAG 2.5.7 alt)", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(screen.getByRole("button", { name: "Add Paragraph" }));

    expect(latest).toHaveLength(3);
    expect(latest.map((block) => block.id)).toContain("blk-seed-00000001");
    expect(latest.map((block) => block.id)).toContain("blk-seed-00000002");
    // Inserted immediately after the first (current) block.
    expect(latest[0]?.id).toBe("blk-seed-00000001");
    expect(latest[2]?.id).toBe("blk-seed-00000002");
  });

  it("reorders blocks by keyboard without drag (WCAG 2.5.7)", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(screen.getByRole("button", { name: "Move down" }));

    expect(latest.map((block) => block.type)).toEqual(["paragraph", "heading"]);
    expect(latest.map((block) => block.id)).toEqual(["blk-seed-00000002", "blk-seed-00000001"]);
  });

  it("deletes the current block", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(screen.getByRole("button", { name: "Delete block" }));

    expect(latest.map((block) => block.id)).toEqual(["blk-seed-00000002"]);
  });
});
