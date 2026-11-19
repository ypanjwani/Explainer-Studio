// The locked card's progress estimate (app/src/shell/progress.ts): weighted by step and time, never backwards,
// held at 95% until done; a revise follow-up spins with no number.
import { describe, expect, it } from "vitest";
import { HOLD, estimate, expectedSteps, percent, previewOf, spins, stepShare } from "../app/src/shell/progress";
import type { ExplainerRecord } from "../app/src/store/history";

type Rec = Parameters<typeof percent>[0];
let n = 0;
const rec = (quality: string, steps: ExplainerRecord["steps"], extra: Partial<Rec> = {}): Rec =>
  ({ id: `r${n++}`, request: { mode: "concept", quality } as ExplainerRecord["request"], steps, ...extra });

const T0 = 1_000_000;

describe("progress estimate", () => {
  it("starts at 0 and grows with the running step's time", () => {
    const r = rec("playground", { plan: { status: "running", at: T0 } });
    expect(estimate(r, T0)).toBe(0);
    const a = estimate(r, T0 + 10_000), b = estimate(r, T0 + 20_000);
    expect(a).toBeGreaterThan(0);
    expect(b).toBeGreaterThan(a);
  });

  it("a running step never reaches its full share, however long it runs", () => {
    expect(stepShare(1e9, 30)).toBeLessThanOrEqual(0.95);
    expect(stepShare(24, 30)).toBeCloseTo(0.8);
  });

  it("holds at 95% until done, even with every step done", () => {
    const all = Object.fromEntries(expectedSteps(rec("playground", {})).map(([s]) => [s, { status: "done" as const, at: T0 }]));
    expect(estimate(rec("playground", all), T0)).toBe(HOLD);
  });

  it("counts skipped steps before the furthest one reached as done (jumps forward)", () => {
    const r = rec("playground", { plan: { status: "done", at: T0 }, design: { status: "running", at: T0 } });
    const plain = rec("playground", { plan: { status: "done", at: T0 }, interaction: { status: "done", at: T0 }, design: { status: "running", at: T0 } });
    expect(estimate(r, T0)).toBeCloseTo(estimate(plain, T0));
  });

  it("never goes backwards when an unexpected step appears", () => {
    const done = { status: "done" as const, at: T0 };
    const steps: ExplainerRecord["steps"] = { plan: done, interaction: done, design: done, build: done };
    const r = rec("playground", steps);
    const before = percent(r, T0 + 40_000);
    steps.examples = { status: "running", at: T0 + 40_000 }; // adds to the total, in its place after build
    expect(estimate(r, T0 + 40_000)).toBeLessThan(before);
    expect(percent(r, T0 + 40_000)).toBe(before);
  });

  it("an unexpected step goes in its pipeline place, not at the end", () => {
    const names = expectedSteps(rec("playground", { examples: { status: "running" } })).map(([s]) => s);
    expect(names.indexOf("examples")).toBe(names.indexOf("build") + 1);
  });

  it("Reader pages have no playground or check steps", () => {
    const names = expectedSteps(rec("lite-plus", {})).map(([s]) => s);
    expect(names).toEqual(["plan", "design", "build"]);
  });

  it("paper pages read and summarize first", () => {
    const r = { ...rec("lite-plus", {}), request: { mode: "paper", quality: "lite-plus" } as ExplainerRecord["request"] };
    expect(expectedSteps(r).map(([s]) => s).slice(0, 2)).toEqual(["read", "summarize"]);
  });

  it("a revise follow-up spins; a new page doesn't", () => {
    expect(spins({ followUp: { kind: "revise", baseId: "x" } as ExplainerRecord["followUp"] })).toBe(true);
    expect(spins({ followUp: undefined })).toBe(false);
  });

  it("a new page spins while only its message is being read (FIRST_TALK), then shows a number", () => {
    expect(spins({ followUp: undefined, steps: { intake: { status: "running" } } })).toBe(true);
    expect(spins({ followUp: undefined, steps: { intake: { status: "done" } } })).toBe(true);
    expect(spins({ followUp: undefined, steps: { intake: { status: "done" }, plan: { status: "running" } } })).toBe(false);
    expect(spins({ followUp: undefined, steps: {} })).toBe(false);
    expect(expectedSteps(rec("lite-plus", { intake: { status: "done" } })).map(([s]) => s)).toEqual(["intake", "plan", "design", "build"]);
  });

  it("merges the steps' previews", () => {
    const pv = previewOf({ plan: { status: "done", preview: { title: "T", subtitle: "S" } }, design: { status: "done", preview: { theme: "ocean", icons: ["🔍"] } } });
    expect(pv).toEqual({ title: "T", subtitle: "S", theme: "ocean", icons: ["🔍"] });
  });
});
