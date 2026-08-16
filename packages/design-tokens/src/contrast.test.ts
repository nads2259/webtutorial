import { describe, expect, it } from "vitest";
import { contrastRatio, meetsContrast, relativeLuminance } from "./contrast";

describe("contrastRatio (WCAG 1.4.3)", () => {
  it("returns 21:1 for black on white", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
  });

  it("returns 1:1 for identical colors", () => {
    expect(contrastRatio("#335cff", "#335cff")).toBeCloseTo(1, 5);
  });

  it("is order independent", () => {
    expect(contrastRatio("#1a1a1a", "#ffffff")).toBeCloseTo(
      contrastRatio("#ffffff", "#1a1a1a"),
      10,
    );
  });

  it("supports 3-digit hex", () => {
    expect(contrastRatio("#000", "#fff")).toBeCloseTo(21, 5);
  });

  it("computes relative luminance bounds", () => {
    expect(relativeLuminance("#000000")).toBeCloseTo(0, 5);
    expect(relativeLuminance("#ffffff")).toBeCloseTo(1, 5);
  });

  it("throws on invalid hex", () => {
    expect(() => contrastRatio("#zzzzzz", "#ffffff")).toThrow();
  });

  it("meetsContrast enforces the correct threshold per kind", () => {
    // #8a8a8a on white is ~3.45:1: passes the 3:1 UI floor but fails the 4.5:1 text floor.
    expect(meetsContrast("#8a8a8a", "#ffffff", "ui")).toBe(true);
    expect(meetsContrast("#8a8a8a", "#ffffff", "text")).toBe(false);
  });
});
