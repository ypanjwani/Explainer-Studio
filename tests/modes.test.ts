// The composer's modes (app/src/shell/modes.ts): only Reader and Explorer are on the menu; old pages keep their mode's name.
import { describe, expect, it } from "vitest";
import { DEFAULT_MODE, MODES, howLong, modeInfo, modeName } from "../app/src/shell/modes";

describe("modes", () => {
  it("offers Reader and Explorer only", () => {
    expect(MODES.map((m) => [m.id, m.name])).toEqual([["lite-plus", "Reader"], ["playground", "Explorer"]]);
    expect(modeInfo(DEFAULT_MODE)?.name).toBe("Explorer");
  });

  it("Thinking and review rounds apply to Explorer, not Reader", () => {
    expect(modeInfo("playground")).toMatchObject({ thinking: true, reviewRounds: true });
    expect(modeInfo("lite-plus")).toMatchObject({ thinking: false, reviewRounds: false });
  });

  it("names menu modes and old ones, and says how long they take", () => {
    expect(modeName("lite-plus")).toBe("Reader");
    expect(modeName("code-lab-7")).toBe("Code Lab 7.0");
    expect(modeName("quality-plus")).toBe("Interactive");
    expect(modeName("nonsense")).toBeUndefined();
    expect(modeName(undefined)).toBeUndefined();
    expect(howLong("playground")).toBe("1–3 minutes");
    expect(howLong("lite-plus")).toBe("under a minute");
    expect(howLong("quality-plus")).toBeUndefined();
  });
});
