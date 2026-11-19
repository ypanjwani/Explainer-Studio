// Playground readings (shared/playground/readings.cjs, Nov 13 2026): what a playground really shows at some settings,
// for the follow-up talk step and editor and Jev's text-vs-playground check.
import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";

const require = createRequire(import.meta.url);
const { read, format, summarize } = require("../shared/playground/readings.cjs");

const spec = {
  title: "Sampling",
  state: { t: 1, mode: "greedy", on: false },
  init: "return {noise: rand()}",
  components: [
    { id: "temp", type: "slider", key: "t", min: 0.5, max: 2, label: "Temperature", onChange: "return {t: value, changed: true}" },
    { id: "m", type: "select", key: "mode", options: ["greedy", { value: "topk", label: "Top-k" }] },
    { id: "sw", type: "toggle", key: "on", label: "Show noise" },
    { id: "go", type: "button", label: "Sample", onClick: "return {n: (state.n || 0) + 1}" },
    { id: "r", type: "readout", label: "Top token", value: "return +(1 / (1 + state.t)).toFixed(3)" },
    { id: "n", type: "readout", label: "Samples", value: "return state.n || 0" },
    { id: "c", type: "chart", title: "Odds", draw: "return [bars([[0, 1 / state.t], [1, 1]], {labels: ['the', 'a']}), text(0, 1, state.mode)]" },
  ],
};

describe("drawing readings (Follow-ups v4, phase 3)", () => {
  it("summarises a drawing's parts, words and numbers, so a slider's effect shows", () => {
    const d = {
      title: "Wheel", state: { k: 0.2 },
      components: [
        { id: "s", type: "slider", key: "k", min: 0, max: 1, label: "Weight" },
        { id: "c", type: "chart", title: "Picture", draw: "return [drawing([svgEl('circle', {cx: 50, cy: 50, r: 10 + 40 * state.k}), svgEl('text', {x: 50, y: 90}, 'center')], {label: 'Wheel'})]" },
      ],
    };
    const r = read(d);
    const first = r.runs[0].charts.Picture[0], max = r.runs[2].charts.Picture[0];
    expect(first).toBe('drawing "Wheel": 2 shapes (1 circle, 1 text), texts "center"; circle(cx=50 cy=50 r=18)');
    expect(max).toContain("r=50");
  });
});

describe("playground readings", () => {
  it("runs the default settings: start, slider ends, options, toggle, button presses", () => {
    const r = read(spec);
    expect(r.runs.map((x: { label: string }) => x.label)).toEqual([
      "at the start", "Temperature = 0.5 (min)", "Temperature = 2 (max)", "m = greedy", "m = Top-k", "Show noise on",
      "Sample pressed once", "Sample pressed 3 times",
    ]);
    expect(r.runs[0].readouts).toEqual({ "Top token": 0.5, Samples: 0 });
    expect(r.runs[2].readouts["Top token"]).toBe(0.333);
    expect(r.runs[7].readouts.Samples).toBe(3);
    expect(r.runs[1].charts.Odds[0]).toBe("bars: the: 2, a: 1");
    expect(read(spec)).toEqual(r);  // seeded: the same each time
  });

  it("sets state through the control's onChange and reports a bad run without stopping", () => {
    const r = read(spec, [{ set: { t: 2 } }, { set: { nope: 1 } }, { press: { r: 1 } }, { play: 5 }]);
    expect(r.runs[0].label).toBe("t = 2");
    expect(r.runs[0].readouts["Top token"]).toBe(0.333);
    expect(r.runs[1].error).toMatch(/not a state key/);
    expect(r.runs[2].error).toMatch(/not a button/);
    expect(r.runs[3].error).toMatch(/no Play button/);
    expect(read({ title: "x", state: {}, components: [{ id: "a", type: "readout", value: "nope(" }] }).error).toBeTruthy();
  });

  it("summarizes lines by their shape and formats for a prompt", () => {
    const pts = [1, 2, 3, 4, 5].map((x) => [x, (x - 3) ** 2]);
    expect(summarize({ type: "line", points: pts, label: "test" })).toBe(
      'line "test": 5 points, x 1..5, y 4 → 4, min 0 at x=3, max 4 at x=1, dips at x=3');
    expect(summarize({ type: "line", points: pts.map(([x]) => [x, x]) })).toMatch(/rising$/);
    expect(summarize({ type: "heatmap", values: [[1, 5], [0, 2]] })).toBe("heatmap 2x2, min 0 at row 1, col 0, max 5 at row 0, col 1");
    const text = format(read(spec), 200);
    expect(text.startsWith("- at the start:\n  readouts: Top token = 0.5; Samples = 0")).toBe(true);
    expect(text.length).toBeLessThanOrEqual(200);
  });
});
