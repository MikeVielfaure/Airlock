import { describe, it, expect } from "vitest";
import { canOpenTab } from "./tabs";

describe("canOpenTab", () => {
  it("allows any count when the cap is 0 (unlimited)", () => {
    expect(canOpenTab(0, 0)).toBe(true);
    expect(canOpenTab(0, 999)).toBe(true);
  });

  it("allows opening while under the cap", () => {
    expect(canOpenTab(3, 0)).toBe(true);
    expect(canOpenTab(3, 2)).toBe(true);
  });

  it("refuses once the cap is reached", () => {
    expect(canOpenTab(3, 3)).toBe(false);
    expect(canOpenTab(1, 1)).toBe(false);
  });

  it("refuses when already over the cap (e.g. the cap was lowered after tabs were opened)", () => {
    expect(canOpenTab(2, 5)).toBe(false);
  });
});
