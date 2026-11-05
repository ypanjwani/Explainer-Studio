// Playground mode: the runtime copied from the ai-playground experiment (shared/playground), its validator and prompt.
import { createRequire } from "node:module";
import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";
import { PLAYGROUND2_SYSTEM, PLAYGROUND_SYSTEM } from "../prompts/prompts";
// The runtime exactly as the frame gets it (same files and order).
import { PLAYGROUND_SOURCES as RUNTIME } from "../app/src/render/components/playground-src";

// The validator and helpers as the server's checker loads them (CommonJS; they require each other).
const require = createRequire(import.meta.url);
const { validate } = require("../shared/playground/validator.cjs");
const helpers = require("../shared/playground/helpers.cjs");

// The ai-playground experiment's hand-written examples.
const files = import.meta.glob("./fixtures/playground/*.json", { eager: true, import: "default" }) as Record<string, unknown>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const specs: [string, any][] = Object.entries(files).map(([path, spec]) => [path.split("/").pop() ?? path, spec]);

/** A frame like widget.ts builds: the runtime, then the widget's html (the server's playground_html shape). */
function frame(spec: unknown) {
  const dom = new JSDOM(`<!doctype html><html><body><div id="pg"></div></body></html>`, { runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window as unknown as { eval: (s: string) => unknown; XW: { playground: (s: unknown) => unknown }; document: Document };
  w.eval("window.XW = {};");  // the widget kit sets XW first in the real frame
  for (const src of RUNTIME) w.eval(src);
  const errors: string[] = [];
  (dom.window as unknown as { console: Console }).console.warn = (...a: unknown[]) => { errors.push(a.join(" ")); };
  w.XW.playground(spec);
  return { doc: w.document, errors, dom };
}

describe("playground runtime", () => {
  it("has at least one example spec", () => expect(specs.length).toBeGreaterThan(5));

  it.each(specs)("validator passes %s and reports facts for the reviewer", (_, spec) => {
    const r = validate(spec);
    expect(r.errors).toEqual([]);
    expect(r.ok).toBe(true);
    expect(r.facts.controls.length).toBeGreaterThan(0);
    expect(r.facts.changes.some((c: { redrawn: boolean }) => c.redrawn)).toBe(true);
  });

  it("rejects a control that changes nothing anyone sees, in words for the retry", () => {
    const spec = {
      title: "t", state: { a: 1, b: 2 },
      components: [
        { id: "s", type: "slider", label: "A", key: "a", min: 0, max: 5 },
        { id: "r", type: "readout", label: "B", value: "return state.b;" },
      ],
    };
    const r = validate(spec);
    expect(r.ok).toBe(false);
    expect(r.errors.join(" ")).toMatch(/no chart or readout depends on that/);
    expect(r.facts).toBeUndefined();
  });

  it("rejects a chart that never draws anything, but not one that fills in after a control", () => {
    const withDraw = (draw: string) => ({
      title: "t", state: { a: 0 },
      components: [
        { id: "s", type: "slider", label: "A", key: "a", min: 0, max: 5 },
        { id: "r", type: "readout", label: "A", value: "return state.a;" },
        { id: "c", type: "chart", title: "C", x: [0, 5], y: [0, 5], draw },
      ],
    });
    for (const draw of ["return [];", "return [line([[state.a, 1]])];", "return [points([]), text(1, 1, ' ')];"]) {
      const r = validate(withDraw(draw));
      expect(r.ok).toBe(false);
      expect(r.errors.join(" ")).toMatch(/c: the chart stays empty/);
    }
    // empty at a = 0, drawn once the slider moves
    expect(validate(withDraw("return state.a > 0 ? [points([[state.a, 1]])] : [];")).errors).toEqual([]);
  });

  it.each(specs)("draws %s in a frame without errors", (_, spec) => {
    const { doc, errors } = frame(spec);
    expect(errors).toEqual([]);
    expect(doc.querySelector(".pg")).not.toBeNull();
    expect(doc.querySelectorAll(".pg-chart svg, .pg-readout").length).toBeGreaterThan(0);
  });

  it("redraws when a slider moves", () => {
    const spec = specs.find(([f]) => f === "gini-impurity.json")![1];
    const { doc, dom } = frame(spec);
    const slider = doc.querySelector<HTMLInputElement>('.pg-slider input[type="range"]')!;
    const before = doc.querySelector(".pg-charts")!.innerHTML;
    slider.value = "8";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    expect(doc.querySelector(".pg-charts")!.innerHTML).not.toBe(before);
  });
});

// A diagram made only of shapes and links: a slider moves one node, a button-free chart drag moves another.
const DIAGRAM = {
  title: "Diagram",
  plan: { insight: "i", mechanism: "m", user_changes: "u", reacts: "r" },
  state: { w: 3, ax: 2, ay: 2 },
  components: [
    { id: "s1", type: "slider", label: "Width", key: "w", min: 1, max: 5, step: 1 },
    {
      id: "d1", type: "chart", title: "Flow", x: [0, 10], y: [0, 6],
      onDrag: "return { ax: pos.x, ay: pos.y };",
      draw: [
        "return [",
        "  node('a', state.ax, state.ay, { label: 'In', color: 'blue', draggable: true }),",
        "  box(4, 2, 4 + state.w, 4, { id: 'b', label: 'Layer' }),",
        "  diamond(8, 1, 9.5, 3, { id: 'c', label: 'ok?', color: 'orange' }),",
        "  circle(8, 4, 9.5, 5.5, { id: 'e', dashed: true }),",
        "  link('a', 'b'), link('b', 'c', { label: 'go', curve: 0.3 }), link('c', 'e', { directed: false }), link('e', 'e'),",
        "];",
      ].join("\n"),
    },
  ],
};

describe("playground diagram marks (shapes and links)", () => {
  it("passes the validator, including the drag probe on a node", () => {
    const r = validate(DIAGRAM);
    expect(r.errors).toEqual([]);
    expect(r.ok).toBe(true);
    expect(r.facts.labels).toEqual(expect.arrayContaining(["In", "Layer", "ok?", "go"]));
  });

  it("draws each shape and every link, with no axes", () => {
    const { doc, errors } = frame(DIAGRAM);
    expect(errors).toEqual([]);
    const svg = doc.querySelector(".pg-chart svg")!;
    expect(svg.querySelectorAll("ellipse.st-node").length).toBe(2);   // node a, circle e
    expect(svg.querySelectorAll("rect.st-node").length).toBe(1);      // box
    expect(svg.querySelectorAll("polygon.st-node").length).toBe(1);   // diamond
    expect(svg.querySelectorAll("path.st-link").length).toBe(4);      // incl. the self-loop
    expect(svg.querySelectorAll("path.st-link[marker-end]").length).toBe(3);  // the plain link has no arrowhead
    expect(svg.querySelector(".ax-line")).toBeNull();
  });

  it("starts a link on the outline, not the centre", () => {
    const { doc } = frame(DIAGRAM);
    const link = doc.querySelectorAll("path.st-link")[0]!;                    // a -> b
    const [x1] = link.getAttribute("d")!.match(/-?[\d.]+/g)!.map(Number);
    const node = doc.querySelector("ellipse[data-drag]")!;                    // node a (draggable shapes are drawn on top)
    const cx = Number(node.getAttribute("cx")), rx = Number(node.getAttribute("rx"));
    expect(Math.abs(x1 - cx)).toBeGreaterThan(rx * 0.9);                      // about rx away from the centre
    expect(Math.abs(x1 - cx)).toBeLessThan(rx * 1.1);
  });

  it("moves a link with its shape when the slider changes the box", () => {
    const { doc, dom } = frame(DIAGRAM);
    const before = doc.querySelectorAll("path.st-link")[1]!.getAttribute("d");  // b -> c
    const slider = doc.querySelector<HTMLInputElement>('.pg-slider input[type="range"]')!;
    slider.value = "5";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    expect(doc.querySelectorAll("path.st-link")[1]!.getAttribute("d")).not.toBe(before);
  });

  it.each([
    ["a link to an id that doesn't exist", "return [node('a', 1, 1), link('a', 'zzz')];", /no shape in this draw has that id/],
    ["two shapes with one id", "return [node('a', 1, 1), box(2, 2, 3, 3, { id: 'a' })];", /repeats the id/],
    ["a shape with no position", "return [{ type: 'shape', shape: 'box', id: 'a' }];", /needs a position/],
    ["a size in the wrong unit", "return [node('a', 1, 1, { r: 0.5 })];", /size in pixels/],
    ["a curve out of range", "return [node('a', 1, 1), node('b', 2, 2), link('a', 'b', { curve: 3 })];", /curve must be/],
  ])("rejects %s", (_, draw, message) => {
    const spec = {
      ...DIAGRAM,
      components: [DIAGRAM.components[0], { ...DIAGRAM.components[1], draw, onDrag: undefined, onClick: "return { w: 2 };" }],
    };
    const r = validate(spec);
    expect(r.ok).toBe(false);
    expect(r.errors.join(" ")).toMatch(message);
  });
});

describe("playground prompt", () => {
  it("lists exactly the runtime's helpers", () => {
    const line = PLAYGROUND_SYSTEM.split("\n").find((l) => l.startsWith("seed, rand,"));
    expect(line?.split(", ")).toEqual(Object.keys(helpers));
  });

  it("stays topic-neutral and has no template leftovers", () => {
    expect(PLAYGROUND_SYSTEM).not.toContain("${");
    expect(PLAYGROUND_SYSTEM).toContain('{"error": "no_playground"}');
  });
});

describe("Playground 2.0 prompt (playground-2: Jev picks the parts, Haiku fills the blueprint)", () => {
  it("lists exactly the runtime's helpers and has no template leftovers", () => {
    const line = PLAYGROUND2_SYSTEM.split("\n").find((l) => l.startsWith("seed, rand,"));
    expect(line?.split(", ")).toEqual(Object.keys(helpers));
    expect(PLAYGROUND2_SYSTEM).not.toContain("${");
    expect(PLAYGROUND2_SYSTEM).toContain('{"error": "no_playground"}');
  });

  it("follows the blueprint instead of choosing the views", () => {
    expect(PLAYGROUND2_SYSTEM).toContain("## Your blueprint");
    expect(PLAYGROUND2_SYSTEM).toContain("plan.deviations");
    expect(PLAYGROUND2_SYSTEM).not.toContain("## Your views");
    expect(PLAYGROUND2_SYSTEM).not.toMatch(/7\. covers:/);
    expect(PLAYGROUND2_SYSTEM).not.toContain("Choose freely");
  });

  it("shares Playground's reference sections word for word", () => {
    const ref = PLAYGROUND_SYSTEM.slice(PLAYGROUND_SYSTEM.indexOf("## Spec format"), PLAYGROUND_SYSTEM.indexOf("- Choose freely"));
    expect(ref.length).toBeGreaterThan(5000);
    expect(PLAYGROUND2_SYSTEM).toContain(ref);
  });
});

describe("playground views beyond plotted charts (Oct 30)", () => {
  it("prompt names all three view families and its diagram skeleton passes the checker", () => {
    expect(PLAYGROUND_SYSTEM).toContain("## Your views");
    for (const w of ["mlpGraph", "sequence with links", "matrix", "node, box, circle, diamond and link"]) expect(PLAYGROUND_SYSTEM).toContain(w);
    const line = PLAYGROUND_SYSTEM.split("\n").find((l) => l.includes('"id": "c2"'));
    const chart = JSON.parse(line!.trim());
    const spec = {
      title: "t", state: { a: 2 },
      components: [{ id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 5, step: 0.5 }, chart],
    };
    const r = validate(spec);
    expect(r.errors).toEqual([]);
    expect(r.ok).toBe(true);
  });

  it("gives the reviewer the labels inside graph, sequence and tree marks", () => {
    const spec = {
      title: "t", state: { k: 1 },
      components: [
        { id: "s1", type: "slider", label: "k", key: "k", min: 0, max: 2, step: 1 },
        {
          id: "c1", type: "chart", title: "Net",
          draw: "return [graph({ nodes: [{ label: 'input', layer: 0 }, { label: 'hidden', layer: 1 }], edges: [[0, 1, state.k]] }), sequence(['the', 'cat'], { box: [0, 0.8, 1, 1], highlight: [state.k % 2] }), tree({ label: 'root', children: [{ label: 'leaf', edge: 'yes' }] }, { box: [0, 0, 0.3, 0.3] })];",
        },
      ],
    };
    const r = validate(spec);
    expect(r.errors).toEqual([]);
    expect(r.facts.labels).toEqual(expect.arrayContaining(["input", "hidden", "the", "cat", "root", "leaf", "yes"]));
  });

  it("thins bar labels when many bars would make them run together", () => {
    const n = 30;
    const labelled = (count: number) => ({
      title: "t", state: { a: 1 },
      components: [
        { id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 },
        { id: "c1", type: "chart", title: "Bars", x: [-1, count], y: [0, 3],
          draw: `return [bars(range(${count}).map((i) => [i, state.a]), { labels: range(${count}).map(String) })];` },
      ],
    });
    const many = frame(labelled(n)).doc.querySelectorAll(".mk-barlabel").length;
    expect(many).toBeGreaterThan(3);
    expect(many).toBeLessThan(n);
    expect(frame(labelled(4)).doc.querySelectorAll(".mk-barlabel").length).toBe(4);
  });
});

// Soft layout check (validator layoutProblems): measured with the renderer's own geometry (structures.chartFrame).
const layoutFixture = (name: string) => require(`./fixtures/playground-layout/${name}.json`);

describe("playground layout check", () => {
  it("reports overlapping shapes, a shape past the edge and a hidden arrow, but still passes", () => {
    const r = validate(layoutFixture("pipeline-overlap"));
    expect(r.ok).toBe(true);
    const all = r.layout.join("\n");
    expect(all).toMatch(/'ret' and 'rr' overlap/);
    expect(all).toMatch(/'rr' and 'pk' overlap/);
    expect(all).toMatch(/'gen' sticks out \d+ px past the right edge/);
    expect(all).toMatch(/link from 'q' to 'ret' has only \d+ px/);
    expect(all).toMatch(/1 x unit = 41\.6 px/);  // 416 px of drawing area over x [0, 10]
  });

  it("is clean once the diagram is spread out", () => {
    expect(validate(layoutFixture("pipeline-fixed")).layout).toEqual([]);
  });

  it("is clean for every hand-written example (no false alarms)", () => {
    for (const [name, spec] of specs) expect([name, validate(spec).layout]).toEqual([name, []]);
  });

  const chartSpec = (draw: string, extra: Record<string, unknown> = {}) => ({
    title: "t", state: { a: 1 },
    components: [
      { id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 },
      { id: "c1", type: "chart", title: "C", x: [0, 10], y: [0, 6], draw, ...extra },
    ],
  });

  it("reports a label cut by a narrow shape and a link label on a shape", () => {
    const r = validate(chartSpec("return [node('a', 2, 3, { label: 'Concat and heads', w: 60 }), node('b', 4, 3, { label: 'b', w: 60 + 10 * state.a }), link('a', 'b', { label: 'long link text' })];"));
    const all = r.layout.join("\n");
    expect(all).toMatch(/'Concat and heads' shows as 'Concat …' because shape 'a' is too narrow; make it wider \(w: 116\)/);
    // a link label with no room between the shapes is moved above them (Nov 2), so it is not reported
    expect(all).not.toMatch(/label 'long link text'/);
    const structures = require("../shared/playground/structures.cjs");
    const geom = (cx: number) => ({ shape: "box", cx, cy: 100, hw: 30, hh: 20 });
    const geoms = new Map([["a", geom(100)], ["b", geom(170)]]);
    expect(structures.linkGeom({ from: "a", to: "b", label: "long link text" }, geoms).ly).toBeLessThanOrEqual(80);
    expect(structures.linkGeom({ from: "a", to: "b", label: "w" }, geoms).ly).toBe(100);
  });

  it("reports a line outside the y range and a line capped at the top edge", () => {
    // (a line wholly outside the range is already a hard error; this one is mostly outside)
    const off = validate(chartSpec("return [line(range(11).map((i) => [i, i < 4 ? 2 : 50 + state.a]), { label: 'res' })];"));
    expect(off.layout.join("\n")).toMatch(/line 'res' lies outside the y range \[0,6\] for 7 of 11 points/);
    const capped = validate(chartSpec("return [line(range(11).map((i) => [i, Math.min(6, 20 * state.a + i)]), { label: 'res' })];"));
    expect(capped.layout.join("\n")).toMatch(/line 'res' sits on the top edge/);
  });

  it("reports empty space only when no state ever fills the x range", () => {
    // data reaches x 0..2 at most: mostly empty
    const empty = validate(chartSpec("return [bars(range(3).map((i) => [i, 1 + state.a]))];"));
    expect(empty.layout.join("\n")).toMatch(/never fills more than \d+% of the x range/);
    // a slider grows the data to the whole range: fine even though it starts small
    const grows = {
      ...chartSpec("return [bars(range(state.a).map((i) => [i, 5]))];"),
      state: { a: 3 },
    };
    grows.components[0] = { id: "s1", type: "slider", label: "a", key: "a", min: 1, max: 11, step: 1 };
    expect(validate(grows).layout).toEqual([]);
  });
});

describe("playground layout check: text, heatmaps, axis names, links through shapes", () => {
  const spec = (draw: string, extra: Record<string, unknown> = {}) => ({
    title: "t", state: { a: 1 },
    components: [
      { id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 },
      { id: "c1", type: "chart", title: "C", x: [0, 10], y: [0, 6], draw, ...extra },
    ],
  });

  it("reports a text mark wider than the chart (one near an edge is moved inside instead)", () => {
    const r = validate(spec("return [line([[0, state.a], [10, 5]]), text(1, 3, 'a sentence far too long to fit in this chart even when moved inside')];"));
    expect(r.layout.join("\n")).toMatch(/the text 'a sentence far too long.*runs \d+ px past the edge/);
    expect(validate(spec("return [line([[0, state.a], [10, 5]]), text(5, 3, 'short')];")).layout).toEqual([]);
    // centred on the left edge, at the top, or following a point past the right edge: placeText moves it inside
    expect(validate(spec("return [line([[0, state.a], [10, 5]]), text(0, 3, 'sigmoid'), text(5, 6, 'top'), text(10 + state.a, 2, 'moving')];")).layout).toEqual([]);
  });

  it("nudges a label off the line it is drawn across, and reports one with no free spot", () => {
    const structures = require("../shared/playground/structures.cjs");
    const across = spec("return [line([[0, state.a], [10, 5]]), hline(2, { dashed: true }), text(1, 1.8, 'mode 2')];");
    expect(validate(across).layout).toEqual([]);
    const c = across.components[1] as { x: number[]; y: number[] };
    const marks = [{ type: "hline", y: 2 }, { type: "text", x: 1, y: 1.8, text: "mode 2" }];
    const f = structures.chartFrame(c, marks);
    const t = structures.placeLabels(marks, f).get(marks[1]);
    expect(t.y).not.toBeCloseTo(f.sy(1.8));  // moved up or down, off the rule
    expect(t.onLine).toBeUndefined();
    // rules every 0.2: no spot near the label is free
    const crowded = spec("return [line([[0, state.a], [10, 5]]), ...range(31).map((i) => hline(i * 0.2)), text(5, 3, 'stuck')];");
    expect(validate(crowded).layout.join("\n")).toMatch(/the text 'stuck' is drawn across a line/);
  });

  it("reports a y range the data never fills, like the x range", () => {
    const r = validate(spec("return [area(range(11).map((i) => [i, 0.2 * state.a * Math.exp(-((i - 5) ** 2) / 8)]))];", { y: [0, 6] }));
    expect(r.layout.join("\n")).toMatch(/never fills more than \d+% of the y range \[0,6\] in any state.*narrow y to about \[/);
  });

  it("reports a problem that appears only after a control moves, marked as later", () => {
    const capped = validate(spec("return [line(range(11).map((i) => [i, Math.min(6, state.a * state.a * i)]))];"));
    expect(capped.layout.join("\n")).toMatch(/sits on the top edge.*\(after the controls move, not at the start\)/);
    const first = validate(spec("return [heatmap([[state.a, 2], [3, 4]], { x: [0, 12], y: [0, 6] })];"));
    expect(first.layout).toHaveLength(1);
    expect(first.layout[0]).not.toMatch(/after the controls move/);
  });

  it("reports a heatmap bigger than the fixed range", () => {
    const r = validate(spec("return [heatmap([[state.a, 2], [3, 4]], { x: [0, 12], y: [0, 6] })];", { yLabel: "query token" }));
    const all = r.layout.join("\n");
    expect(all).toMatch(/the heatmap covers x \[0,12\] but the chart's x range is \[0,10\]/);
    expect(all).not.toMatch(/y-axis name/);  // drawn above the plot since Nov 2: it can't sit on the cells
    expect(validate(spec("return [heatmap([[state.a, 2], [3, 4]], { x: [0, 10], y: [0, 6] })];")).layout).toEqual([]);
  });

  it("reports a link that runs through a third shape, not one that bends around it", () => {
    const shapes = "node('a', 1, 3, { w: 50 }), node('mid', 5, 3, { w: 50, label: 'mid' }), node('b', 9, 3, { w: 50 + state.a })";
    const straight = validate(spec(`return [${shapes}, link('a', 'b')];`));
    expect(straight.layout.join("\n")).toMatch(/link from 'a' to 'b' runs through shape 'mid'/);
    const bent = validate(spec(`return [${shapes}, link('a', 'b', { curve: 0.6 })];`));
    expect(bent.layout.join("\n")).not.toMatch(/runs through/);
  });
});

// Nov 1 fixes from the rubric eval (.claude/playground-rubric-eval-results.md), measured on 206 saved playgrounds.
describe("playground: growing charts, legends, numbers, dots, bar headroom, fitted labels", () => {
  const structures = require("../shared/playground/structures.cjs");
  // one chart; extra components (a Step button, Play...) come first
  const withChart = (draw: string, chart: Record<string, unknown>, state: Record<string, unknown>, ...controls: Record<string, unknown>[]) => ({
    title: "t", state, components: [...controls, { id: "c1", type: "chart", title: "C", draw, ...chart }],
  });

  it("does not call a chart that grows with each Step press empty (the dry run presses only twice)", () => {
    const step = { id: "b1", type: "button", label: "Step", onClick: "return { hist: [...state.hist, state.hist.length * 0.5] };" };
    const grows = withChart("return [line(state.hist.map((v, i) => [i, v]))];", { x: [0, 30], y: [0, 20] }, { hist: [0, 0.5] }, step);
    expect(validate(grows).layout.join("\n")).not.toMatch(/x range/);
    // the same chart driven by Play
    const play = { id: "p1", type: "play", label: "Play", onTick: "return state.hist.length > 80 ? null : { hist: [...state.hist, 1] };" };
    expect(validate(withChart("return [line(state.hist.map((v, i) => [i, v]))];", { x: [0, 300], y: [0, 2] }, { hist: [1, 1] }, play)).layout.join("\n")).not.toMatch(/x range/);
    // a button that only changes values (x stays 0..2) is still reported
    const bump = { id: "b1", type: "button", label: "More", onClick: "return { a: state.a + 1 };" };
    const fixed = withChart("return [bars(range(3).map((i) => [i, 1 + state.a]))];", { x: [0, 30], y: [0, 20] }, { a: 1 }, bump);
    expect(validate(fixed).layout.join("\n")).toMatch(/never fills more than \d+% of the x range/);
  });

  it("asks for legend words when 2+ lines in different colours have none", () => {
    const s1 = { id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 };
    const two = "line([[0, 1], [10, 2 + state.a]], { color: 'blue' }), line([[0, 3], [10, 4]], { color: 'orange' })";
    expect(validate(withChart(`return [${two}];`, { x: [0, 10], y: [0, 6] }, { a: 1 }, s1)).layout.join("\n")).toMatch(/2 lines in different colours but nothing says which is which/);
    expect(validate(withChart(`return [${two}, text(8, 2.2, 'train'), text(8, 4.2, 'test')];`, { x: [0, 10], y: [0, 6] }, { a: 1 }, s1)).layout).toEqual([]);
    // same colour: one family of lines, nothing to tell apart
    const same = "line([[0, 1], [10, 2 + state.a]]), line([[0, 3], [10, 4]])";
    expect(validate(withChart(`return [${same}];`, { x: [0, 10], y: [0, 6] }, { a: 1 }, s1)).layout).toEqual([]);
  });

  it("gives bars on a fixed y range room above the top, and leaves other charts alone", () => {
    expect(structures.chartFrame({ y: [0, 1] }, [{ type: "bars", items: [[0, 1]] }]).dom.y).toEqual([0, 1.04]);
    expect(structures.chartFrame({ y: [0, 1] }, [{ type: "line", points: [[0, 1], [1, 0]] }]).dom.y).toEqual([0, 1]);
    expect(structures.chartFrame({ y: [-2, -1] }, [{ type: "bars", items: [[0, -1.5]] }]).dom.y).toEqual([-2, -1]);
  });

  it("writes big readouts in full and big axis ticks short", () => {
    const s1 = { id: "s1", type: "slider", label: "r", key: "r", min: 1, max: 64, step: 1 };
    const spec = {
      title: "t", state: { r: 8 }, components: [s1,
        { id: "r1", type: "readout", label: "Params", value: "return state.r * 32768;" },
        { id: "c1", type: "chart", title: "C", x: [0, 64], y: [0, 2200000], draw: "return [line([[0, 0], [state.r, state.r * 32768]])];" }],
    };
    const { doc } = frame(spec);
    expect(doc.querySelector(".pg-readout-value")?.textContent).toBe("262,144");
    const ticks = [...doc.querySelectorAll("svg .ax-tick")].map((t) => t.textContent);
    expect(ticks.some((t) => /^\d+(\.\d+)?M$/.test(t ?? ""))).toBe(true);
    expect(ticks.join(" ")).not.toMatch(/e\+/);
  });

  it("draws a dot on the plot's edge whole (its own clip, grown by its radius)", () => {
    const s1 = { id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 };
    const { doc } = frame(withChart("return [line([[0, 0], [10, state.a]]), points([[0, 0], [5, 1]], { r: 5 })];", { x: [0, 10], y: [0, 2] }, { a: 1 }, s1));
    const dotClip = [...doc.querySelectorAll("clipPath")].find((c) => c.id.endsWith("-dot"));
    expect(dotClip).toBeTruthy();
    const circles = doc.querySelectorAll(`g[clip-path="url(#${dotClip?.id})"] circle`);
    expect(circles.length).toBe(2);
  });

  it("fits numbers and labels before cutting them", () => {
    const fmt = (v: number) => String(Number(v.toPrecision(3)));
    expect(structures.fitNum(-0.5312, 4, fmt)).toBe("-.53");
    expect(structures.fitNum(1.2345, 3, fmt)).toBe("1.2");
    expect(structures.fitNum(0.0033, 4, fmt)).toBe(".003");  // a small value never shows as 0
    expect(structures.fitNum(262144, 4, (v: number) => String(v))).toBe("262k");
    expect(structures.fitNum(7, 3, fmt)).toBe("7");
    expect(structures.fitNum(-0.642, 2, fmt)).toBe("-…");  // no room: cut, never rounded to -1
    expect(structures.fitNum(12.6, 2, fmt)).toBe("13");
    expect(structures.fitText("newest low", 8, 6.5)[0]).toBe("newest low");  // squeezed, not cut
    expect(structures.fitText("newest low", 8, 6.5)[1]).toMatch(/textLength="52"/);
    expect(structures.fitText("a much longer label here", 8, 6.5)[0]).toMatch(/…$/);
    expect(structures.fitText("short", 8, 6.5)).toEqual(["short", ""]);
  });
});

// Nov 2 fixes from the rubric eval: hand-placed graph nodes too close (row 18), lines past the y range (rows 1, 4).
describe("playground: crowded manual graphs and lines past the y range", () => {
  const structures = require("../shared/playground/structures.cjs");
  const s1 = { id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 };
  const chart = (draw: string, extra: Record<string, unknown> = {}) => ({
    title: "t", state: { a: 1 }, components: [s1, { id: "c1", type: "chart", title: "C", draw, ...extra }],
  });
  // three columns of nodes, `gap` y units apart, on y [0, 7]: 1 unit = 34.6 px
  const column = (gap: number, label = "the cat") =>
    `return [graph({ nodes: [0, 1, 2, 3].map((i) => ({ label: '${label}' + i, x: 1 + state.a * 0.01, y: 1 + i * ${gap} })), edges: [] }, { layout: 'manual' })];`;
  const box = { x0: 46, y0: 16, x1: 426, y1: 258 };

  it("shrinks hand-placed nodes so circles and the names under them don't collide", () => {
    const sy = (v: number) => 258 - v / 7 * 242;
    const m = { type: "graph", layout: "manual", nodes: [0, 1].map((i) => ({ label: "the cat", x: 1, y: 1 + i })), edges: [] };
    const g = structures.manualGraph(m, (v: number) => v * 40, sy, box);
    expect(g.r).toBeLessThan(10);  // was 18: 36 px circles 35 px apart
    expect(g.r).toBeGreaterThanOrEqual(structures.GRAPH_MIN_R);
    expect(g.clash).toEqual([]);
    // short names sit inside the node: only the circles limit the size
    const inside = structures.manualGraph({ ...m, nodes: m.nodes.map((nd) => ({ ...nd, label: "a" })) }, (v: number) => v * 40, sy, box);
    expect(inside.r).toBeGreaterThan(g.r);
  });

  it("reports nodes too close even at the smallest size, with the distance that fits", () => {
    const tight = validate(chart(column(0.4), { x: [0, 3], y: [0, 7], axes: false }));
    expect(tight.ok).toBe(true);
    expect(tight.layout.join("\n")).toMatch(/the name under graph node 'the cat\d' lands on node 'the cat\d' below it.*at least [\d.]+ y units apart/);
    expect(validate(chart(column(1.5), { x: [0, 3], y: [0, 7], axes: false })).layout).toEqual([]);
  });

  it("reports names side by side that run into each other", () => {
    const draw = "return [graph({ nodes: [{ label: 'left words', x: 1, y: 3 + state.a * 0.01 }, { label: 'right words', x: 2.5, y: 3 }], edges: [] }, { layout: 'manual' })];";
    expect(validate(chart(draw, { x: [0, 10], y: [0, 6], axes: false })).layout.join("\n")).toMatch(/names under graph nodes 'left words' and 'right words' run into each other/);
  });

  it("reports a text mark drawn on a node or on its name", () => {
    const nodes = "graph({ nodes: [{ label: 'start', x: 2, y: 3 + state.a * 0.01 }, { label: 'end', x: 8, y: 3 }], edges: [[0, 1]] }, { layout: 'manual' })";
    expect(validate(chart(`return [${nodes}, text(2, 3, '0.5')];`, { x: [0, 10], y: [0, 6], axes: false })).layout.join("\n")).toMatch(/the text '0.5' is drawn on graph node 'start'/);
    expect(validate(chart(`return [${nodes}, text(5, 5, '0.5')];`, { x: [0, 10], y: [0, 6], axes: false })).layout).toEqual([]);
  });

  it("marks where a line leaves the y range, with its furthest value, in the line's colour", () => {
    const { doc } = frame(chart("return [line([[0, 1], [4, 2], [6, 40 + state.a], [8, 2]], { color: 'orange' })];", { x: [0, 10], y: [0, 6] }));
    const arrows = [...doc.querySelectorAll("svg path.mk-clip")];
    expect(arrows).toHaveLength(1);
    expect(arrows[0].getAttribute("style")).toContain("var(--c-orange)");
    expect([...doc.querySelectorAll("svg .mk-cliplabel")].map((t) => t.textContent)).toEqual(["41"]);
  });

  it("marks each stretch above and below once, and skips a straight guide line", () => {
    const zig = "line([[0, 3], [1, 9], [2, -4], [3, 12], [4, -8 - state.a]]), points([[1, 9], [3, 12]])";
    const { doc } = frame(chart(`return [${zig}, line([[0, -50], [10, 50]], { dashed: true })];`, { x: [0, 10], y: [0, 6] }));
    expect(doc.querySelectorAll("svg path.mk-clip")).toHaveLength(4);  // 2 up, 2 down; the dots add none, the guide none
    expect([...doc.querySelectorAll("svg .mk-cliplabel")].map((t) => t.textContent).sort()).toEqual(["-9", "12"]);
    // inside the range: nothing
    const { doc: ok } = frame(chart("return [line([[0, 1], [10, 5 + state.a * 0]])];", { x: [0, 10], y: [0, 6] }));
    expect(ok.querySelectorAll("svg .mk-clip, svg .mk-cliplabel")).toHaveLength(0);
  });
});

// Nov 2 (2): bar values (rubric eval rows 10, 29) and series legends (rows 3, 6, 12, 28).
describe("playground: bar values and series legends", () => {
  const s1 = { id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 };
  const chart = (draw: string, extra: Record<string, unknown> = { x: [-0.5, 3.5], y: [0, 1] }) => ({
    title: "t", state: { a: 1 }, components: [s1, { id: "c1", type: "chart", title: "C", draw, ...extra }],
  });
  const vals = (doc: Document) => [...doc.querySelectorAll("svg .mk-barval")].map((t) => t.textContent);

  it("writes each bar's value when there are few bars and no text marks", () => {
    expect(vals(frame(chart("return [bars([[0, 0.7], [1, 0.2 * state.a], [2, 0.1]], { labels: ['cat', 'dog', 'fox'] })];")).doc)).toEqual(["0.7", "0.2", "0.1"]);
    // the chart's own text marks, many bars, or showValues: false -> none
    expect(vals(frame(chart("return [bars([[0, 0.7], [1, 0.2 * state.a]]), text(2, 0.5, 'note')];")).doc)).toEqual([]);
    expect(vals(frame(chart("return [bars(range(13).map((i) => [i, 0.05 * state.a]))];", { x: [-1, 13], y: [0, 1] })).doc)).toEqual([]);
    expect(vals(frame(chart("return [bars([[0, 0.7 * state.a]], { showValues: false })];")).doc)).toEqual([]);
  });

  it("shortens a value to the bar's width and puts it inside a bar that reaches the top", () => {
    const { doc } = frame(chart("return [bars(range(12).map((i) => [i, i === 0 ? 0.98 : 0.123456 * state.a]))];", { x: [-0.5, 11.5], y: [0, 1] }));
    const t = [...doc.querySelectorAll("svg .mk-barval")];
    expect(t).toHaveLength(12);
    expect(t[0].getAttribute("class")).toContain("is-in");
    expect(t[1].textContent!.length).toBeLessThanOrEqual(4);
  });

  it("draws a legend for 2+ named series in different colours, not for names already written", () => {
    const two = "bars([[0, 0.5], [1, 0.3 * state.a]], { color: 'gray', label: 'Original' }), bars([[0, 0.4], [1, 0.2]], { color: 'orange', label: 'Average' })";
    const { doc } = frame(chart(`return [${two}];`));
    expect([...doc.querySelectorAll("svg .mk-legendtext")].map((t) => t.textContent)).toEqual(["Original", "Average"]);
    expect(validate(chart(`return [${two}];`)).layout.join(" | ")).not.toMatch(/which is which/);
    const written = frame(chart(`return [${two}, text(0, 0.9, 'Original'), text(1, 0.9, 'Average')];`)).doc;
    expect(written.querySelectorAll("svg .mk-legendtext")).toHaveLength(0);
    // one colour: nothing to tell apart
    expect(frame(chart("return [line([[0, 0.1], [3, 0.5 * state.a]], { label: 'a' }), line([[0, 0.2], [3, 0.6]], { label: 'b' })];")).doc.querySelectorAll("svg .mk-legendtext")).toHaveLength(0);
  });

  it("asks for names on unnamed bar or dot series", () => {
    const sets = "points([[0, 0.2], [1, 0.3 * state.a]], { color: 'blue' }), points([[2, 0.6], [3, 0.7]], { color: 'orange' })";
    expect(validate(chart(`return [${sets}];`)).layout.join("\n")).toMatch(/2 sets of dots in different colours but nothing says which is which/);
    const bars2 = "bars([[0, 0.5], [1, 0.3 * state.a]], { color: 'gray' }), bars([[0, 0.4], [1, 0.2]], { color: 'orange' })";
    expect(validate(chart(`return [${bars2}];`)).layout.join("\n")).toMatch(/2 bar series in different colours/);
    // one colour per set, or named: fine
    expect(validate(chart("return [points([[0, 0.2], [1, 0.3 * state.a]]), points([[2, 0.6], [3, 0.7]])];")).layout).toEqual([]);
  });
});

describe("playground: axis names", () => {
  const s1 = { id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 };
  const chart = (draw: string, extra: Record<string, unknown> = {}) => ({
    title: "t", state: { a: 1 }, components: [s1, { id: "c1", type: "chart", title: "C", x: [0, 10], y: [0, 1], xLabel: "w", yLabel: "L(w)", draw, ...extra }],
  });
  const names = (doc: Document) => [...doc.querySelectorAll("svg .ax-label")].map((t) => [t.textContent, Number(t.getAttribute("y"))] as const);

  it("draws the y-axis name above the plot, so nothing plotted sits on it", () => {
    const { doc } = frame(chart("return [line([[0, 0.9], [10, 0.1 * state.a]]), text(0.2, 0.95, 'start')];"));
    const y = names(doc).find(([t]) => t === "L(w)")!;
    const top = Math.min(...[...doc.querySelectorAll("svg .ax-grid")].map((l) => Number(l.getAttribute("y1"))));
    expect(y[1]).toBeLessThan(top);
    const structures = require("../shared/playground/structures.cjs");
    expect(structures.chartFrame({ x: [0, 1], y: [0, 1], yLabel: "L" }, [{ type: "line", points: [] }]).M.t)
      .toBeGreaterThan(structures.chartFrame({ x: [0, 1], y: [0, 1] }, [{ type: "line", points: [] }]).M.t);
  });

  it("leaves out an axis name a text mark already writes", () => {
    const { doc } = frame(chart("return [line([[0, 0.9], [10, 0.1 * state.a]]), text(8, 0.5, 'L(w)')];"));
    expect(names(doc).map(([t]) => t)).toEqual(["w"]);
    expect(doc.querySelectorAll("svg text.mk-text")).toHaveLength(1);
  });
});

describe("playground: ranges that follow the data, and small drawing fixes", () => {
  const structures = require("../shared/playground/structures.cjs");
  const bars = (ys: number[]) => [{ type: "bars", items: ys.map((y, i) => [i, y]) }];

  it("zooms a signed y range in by halves when the data fits a quarter of it", () => {
    expect(structures.zoomRange({ y: [-8, 8] }, bars([0.8, -2.1])).y).toEqual([-2.5, 1.5]);
    expect(structures.zoomRange({ y: [-8, 8] }, bars([6, -5])).y).toBeUndefined();          // fills it
    expect(structures.zoomRange({ y: [0, 10] }, bars([0.3, 0.2])).y).toBeUndefined();       // a size scale from 0: stays
    expect(structures.zoomRange({ y: [-8, 8], zoom: false }, bars([0.8, -2.1])).y).toBeUndefined();
    expect(structures.zoomRange({ y: [-8, 8], onDrag: "return null;" }, bars([0.8, -2.1])).y).toBeUndefined();
    expect(structures.zoomRange({ y: [-8, 8] }, bars([0, 0])).y).toBeUndefined();          // nothing to show yet
    // a 2-point guide line drawn across the chart doesn't hold the range open
    const guide = { type: "line", points: [[0, -50], [1, 50]] };
    expect(structures.zoomRange({ y: [-8, 8] }, [...bars([0.8, -2.1]), guide]).y).toEqual([-2.5, 1.5]);
  });

  it("fits a chart's range to a heatmap smaller than it", () => {
    const hm = { type: "heatmap", values: [[1, 2], [3, 4]], x: [0, 16], y: [0, 40] };
    expect(structures.zoomRange({ x: [0, 48], y: [0, 40] }, [hm])).toEqual({ x: [0, 16] });
  });

  it("stops an arrow at the edge of a dot on its tip, and puts the agent beside a grid arrow", () => {
    const c = (draw: string) => ({ title: "t", state: { a: 1 }, components: [{ id: "s", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 },
      { id: "c1", type: "chart", title: "C", x: [0, 10], y: [0, 10], draw }] });
    const { doc } = frame(c("return [arrow(0, 0, 5, 5 * state.a * 0.2 + 4), point(5, 5 * state.a * 0.2 + 4, { r: 6 })];"));
    const ar = doc.querySelector("svg .mk-arrow")!, pt = doc.querySelector("svg .mk-pt")!;
    const gap = Math.hypot(Number(ar.getAttribute("x2")) - Number(pt.getAttribute("cx")), Number(ar.getAttribute("y2")) - Number(pt.getAttribute("cy")));
    expect(gap).toBeGreaterThanOrEqual(7);
    const g = frame(c("return [grid([[0, 0], [0, 0]], { arrows: [['up', null], [null, null]], agents: [{ r: 0, c: state.a > 5 ? 1 : 0 }] })];")).doc;
    const ag = g.querySelector("svg .st-agent")!;
    expect(Number(ag.getAttribute("r"))).toBeLessThan(20);
  });

  it("draws a line lying under another wide and faint, and hides y numbers on a strip of dots", () => {
    const c = (draw: string) => ({ title: "t", state: { a: 1 }, components: [{ id: "s", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 },
      { id: "c1", type: "chart", title: "C", x: [0, 6], y: [-1, 8], draw }] });
    const lines = frame(c("return [line([[0, 1], [6, 4 * state.a]], { color: 'orange' }), line([[0, 1], [6, 4 * state.a]], { color: 'gray', dashed: true })];")).doc.querySelectorAll("svg .mk-line");
    expect(lines[0].getAttribute("stroke-width")).toBe("7");
    expect(lines[1].getAttribute("stroke-width")).toBeNull();
    const strip = frame(c("return [points([[1, 1], [2, 1], [3 * state.a, 1]]), points([[1, 0], [2, 0]], { color: 'orange' })];")).doc;
    expect(strip.querySelectorAll("svg .ax-grid")).toHaveLength(0);
    const scatter = frame(c("return [points([[1, 1], [2, 3], [3 * state.a, 5], [4, 2]])];")).doc;
    expect(scatter.querySelectorAll("svg .ax-grid").length).toBeGreaterThan(0);
  });
});

describe("playground: zero bars, cell names, equal scales, byte units", () => {
  const slider = { id: "s", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 };
  const one = (comp: Record<string, unknown>) => ({ title: "t", state: { a: 1 }, components: [slider, comp] });
  const chart = (draw: string, extra: Record<string, unknown> = {}) => one({ id: "c1", type: "chart", title: "C", draw, ...extra });

  it("draws a bar of 0 as a thin stub, so the series still shows", () => {
    const { doc } = frame(chart("return [bars([[0, 0.5 * state.a], [1, 0]], { label: 'p' })];", { x: [-0.5, 1.5], y: [0, 1] }));
    const h = [...doc.querySelectorAll("svg .mk-bar")].map((r) => Number(r.getAttribute("height")));
    expect(h[1]).toBe(2);
  });

  it("names heatmap cells with cellLabels, above their values", () => {
    const { doc } = frame(chart("return [heatmap([[3 * state.a, 1], [2, 4]], { showValues: true, cellLabels: [['TP', 'FN'], ['FP', 'TN']] })];", { x: [0, 2], y: [0, 2] }));
    const names = [...doc.querySelectorAll("svg .mk-cellname")].map((t) => t.textContent);
    expect(names).toEqual(["TP", "FN", "FP", "TN"]);
    expect([...doc.querySelectorAll("svg .mk-cellval:not(.mk-cellname)")].map((t) => t.textContent)).toEqual(["3", "1", "2", "4"]);
    expect(validate(chart("return [heatmap([[state.a]], { cellLabels: 'TP' })];", { x: [0, 1], y: [0, 1] })).errors.join(" ")).toMatch(/cellLabels must be a 2D array/);
  });

  it("draws a plane with the same range on both axes at equal scales", () => {
    const structures = require("../shared/playground/structures.cjs");
    const marks = [{ type: "points", points: [[1, 1]] }];
    const f = structures.chartFrame({ x: [-4, 4], y: [-4, 4] }, marks);
    const ux = (f.sx(1) - f.sx(0)), uy = (f.sy(0) - f.sy(1));
    expect(Math.abs(ux - uy)).toBeLessThan(0.01);
    expect(f.dom.x[0]).toBeLessThan(-4);
    expect(structures.chartFrame({ x: [-4, 4], y: [-4, 4], aspect: "free" }, marks).dom.x).toEqual([-4, 4]);
    expect(structures.chartFrame({ x: [0, 1], y: [0, 1] }, marks).dom.x).toEqual([0, 1]);  // a [0, 1] square is not a plane
    expect(structures.chartFrame({ x: [-4, 4], y: [-4, 4] }, [{ type: "bars", items: [[0, 1]] }]).dom.x).toEqual([-4, 4]);
  });

  it("moves a byte readout to the next unit from 10,000 up", () => {
    const ro = (value: string, label: string) => {
      const d = frame(one({ id: "r", type: "readout", label, value })).doc;
      return [d.querySelector(".pg-readout-value")!.textContent, d.querySelector(".pg-readout-label")!.textContent];
    };
    expect(ro("return 20480 * state.a;", "Cache (KB)")).toEqual(["20", "Cache (MB)"]);
    expect(ro("return 512 * state.a;", "Cache (KB)")).toEqual(["512", "Cache (KB)"]);
    expect(ro("return 3 * 1024 ** 3 * state.a;", "Size (bytes)")).toEqual(["3", "Size (GB)"]);
    expect(ro("return 20480 * state.a;", "Tokens")).toEqual(["20,480", "Tokens"]);
  });
});

// plan.covers (Nov 2): the builder lists what the section asks for and which components deliver it; the validator
// reports items listed but not built, as soft "coverage" messages (never a failure).
describe("playground coverage list (plan.covers)", () => {
  const gini = () => JSON.parse(JSON.stringify(specs.find(([n]) => n === "gini-impurity.json")![1]));
  const withCovers = (covers: unknown) => { const s = gini(); s.plan = { ...(s.plan || {}), covers }; return s; };

  it("asks for the list when it is missing, without failing", () => {
    const r = validate(gini());
    expect(r.ok).toBe(true);
    expect(r.coverage).toHaveLength(1);
    expect(r.coverage[0]).toMatch(/^plan\.covers is missing/);
  });

  it("is quiet when every item is delivered by a component of the right kind", () => {
    const r = validate(withCovers([
      { need: "split threshold", kind: "control", by: ["t"] },
      { need: "weighted Gini", kind: "number", by: ["wg"] },
      { need: "impurity per side", kind: "view", by: ["bars"] },
      { need: "class to place", kind: "control", by: ["cls", "data"] },
    ]));
    expect(r.coverage).toEqual([]);
    expect(r.facts.covers.map((c: { need: string }) => c.need)).toContain("weighted Gini");
  });

  it("names items with unknown ids, no ids or the wrong kind of component", () => {
    const r = validate(withCovers([
      { need: "test mode", kind: "control", by: ["mode"] },
      { need: "n_left and n_right", kind: "number" },
      { need: "split threshold", kind: "control", by: ["wg"] },
      { need: "best split", kind: "mark", by: ["t"] },
    ]));
    const all = r.coverage.join("\n");
    expect(r.ok).toBe(true);
    expect(all).toMatch(/"test mode" lists mode, but there is no component with that id/);
    expect(all).toMatch(/"n_left and n_right" names no component/);
    expect(all).toMatch(/"split threshold" is a control, but wg is a readout/);
    expect(all).toMatch(/"best split" is a mark, but t is a slider: it needs a chart/);
  });

  it("asks the builder for the list in the prompt", () => {
    expect(PLAYGROUND_SYSTEM).toMatch(/7\. covers:/);
    expect(PLAYGROUND_SYSTEM).toMatch(/"covers": \[/);
    expect(PLAYGROUND_SYSTEM).not.toMatch(/a focused set of controls/);
  });
});

// Worked-example checks (Nov 2): spec.checks sets state as a control would, reads a readout and compares it with the
// section's number. Soft "checks" messages, never a failure.
describe("playground worked-example checks", () => {
  const base = {
    title: "Loss", state: { p: 0.5, y: 1, q: 0 },
    components: [
      { id: "s", type: "slider", label: "p", key: "p", min: 0.01, max: 0.99, step: 0.01 },
      { id: "ys", type: "select", label: "label", key: "y", options: [{ value: 0, label: "0" }, { value: 1, label: "1" }], onChange: "return { y: value, q: value * 2 };" },
      { id: "r", type: "readout", label: "Loss", value: "return -(state.y * Math.log(state.p) + (1 - state.y) * Math.log(1 - state.p));" },
      { id: "r2", type: "readout", label: "q", value: "return String(state.q) + ' of 4';" },
    ],
  };
  const run = (checks: unknown) => validate({ ...base, checks });

  it("passes when the readout shows the section's number to its precision", () => {
    expect(run([{ set: { p: 0.7, y: 1 }, readout: "r", expect: 0.36 }]).checks).toEqual([]);
    expect(run([{ set: { y: 1 }, readout: "r2", expect: 2 }]).checks).toEqual([]);  // onChange ran (q = 2)
  });

  it("reports a readout that disagrees, without failing", () => {
    const r = run([{ set: { p: 0.2, y: 1 }, readout: "r", expect: 0.36 }]);
    expect(r.ok).toBe(true);
    expect(r.checks[0]).toMatch(/with p = 0\.2, y = 1, readout r \(Loss\) shows 1\.609, but the section's worked example gives 0\.36/);
  });

  it("reports malformed checks", () => {
    const all = run([{ set: { zz: 1 }, readout: "r", expect: 1 }, { set: { p: 0.5 }, readout: "s", expect: 1 }, { readout: "r" }]).checks.join("\n");
    expect(all).toMatch(/"zz" is not a state key/);
    expect(all).toMatch(/"s" is not a readout id/);
    expect(all).toMatch(/check 3 must be/);
  });

  it("is empty without checks, and the prompt describes them", () => {
    expect(validate(base).checks).toEqual([]);
    expect(PLAYGROUND_SYSTEM).toMatch(/Worked-example checks:/);
  });
});

// Nov 4: heatmap values by default, a flat-heatmap message, Reset greyed until something changes, a hint for drag-only.
describe("playground: heatmap values, Reset and the drag hint", () => {
  const s1 = { id: "s1", type: "slider", label: "a", key: "a", min: 0, max: 2, step: 1 };
  const chart = (draw: string, extra: Record<string, unknown> = { x: [0, 2], y: [0, 2] }) => ({
    title: "t", state: { a: 1 }, components: [s1, { id: "c1", type: "chart", title: "C", draw, ...extra }],
  });
  const cells = (doc: Document) => [...doc.querySelectorAll("svg .mk-cellval")].map((t) => t.textContent);

  it("writes a small heatmap's values unless showValues is false", () => {
    expect(cells(frame(chart("return [heatmap([[0.9 * state.a, 0.1], [0.25, 0.7]])];")).doc)).toEqual(["0.9", "0.1", "0.25", "0.7"]);
    expect(cells(frame(chart("return [heatmap([[0.9 * state.a, 0.1], [0.25, 0.7]], { showValues: false })];")).doc)).toEqual([]);
  });

  it("leaves big grids, tiny cells and charts with their own text marks colour only", () => {
    const big = "return [heatmap(range(30).map((r) => range(30).map((c) => state.a * (r + c))))];";
    expect(cells(frame(chart(big, { x: [0, 30], y: [0, 30] })).doc)).toEqual([]);
    expect(cells(frame(chart("return [heatmap([[0.9 * state.a, 0.1], [0.25, 0.7]]), text(1, 1, 'note')];")).doc)).toEqual([]);
  });

  it("shortens long values to fit narrow cells, never showing a non-zero as 0", () => {
    const draw = "return [heatmap(range(4).map((r) => range(12).map((c) => state.a * (r * 12 + c + 1) / 7.123456)))];";
    const vals = cells(frame(chart(draw, { x: [0, 12], y: [0, 4] })).doc);
    expect(vals).toHaveLength(48);
    expect(vals.every((v) => v!.length <= 4 && !/^-?0?\.?0*$/.test(v!))).toBe(true);
  });

  it("reports a heatmap whose cells are all the same in every state, not one that changes", () => {
    const flat = validate(chart("return [heatmap([[0, 0], [0, 0]]), point(state.a, 1)];"));
    expect(flat.ok).toBe(true);
    expect(flat.layout.join("\n")).toMatch(/every cell of the heatmap has the same value \(0\)/);
    const moves = validate(chart("return [heatmap([[state.a, 0], [0, 0]])];"));
    expect(moves.layout.join("\n")).not.toMatch(/same value/);
    // a grid whose size follows the slider is the lesson itself (n x n pairs), even with every cell 1
    const grows = validate(chart("return [heatmap(range(state.a + 1).map(() => range(state.a + 1).map(() => 1)))];"));
    expect(grows.layout.join("\n")).not.toMatch(/same value/);
  });

  it("greys Reset until something changes, and again after pressing it", () => {
    const { doc, dom } = frame(chart("return [heatmap([[state.a, 0], [0, 1]])];"));
    const reset = [...doc.querySelectorAll("button.pg-btn")].find((b) => b.textContent === "Reset") as HTMLButtonElement;
    expect(reset.disabled).toBe(true);
    const slider = doc.querySelector(".pg-slider input") as HTMLInputElement;
    slider.value = "2";
    slider.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
    expect(reset.disabled).toBe(false);
    reset.click();
    expect(reset.disabled).toBe(true);
  });

  it("adds a hint only when dragging or clicking a chart is the only control", () => {
    const dragOnly = { ...DIAGRAM, components: DIAGRAM.components.filter((c) => c.type !== "slider") };
    expect(frame(dragOnly).doc.querySelector(".pg-hint")?.textContent).toBe("Drag the points on the chart.");
    expect(frame(DIAGRAM).doc.querySelector(".pg-hint")).toBeNull();
  });
});
