// Reading and changing a block's values by path, with each value's kind. The fact check (factcheck.ts) finds a
// block's text through leaves() and applies its fixes with applyChanges(); every result then goes through checkEdit
// (validate.ts). Python port: server/explainer/core/edit.py.

import { BlockSchemas, type Block, type BlockType } from "./spec";

export type Path = (string | number)[];

/**
 * What a value in a block is. Only text and prose may change in a Writing edit;
 * id and data values are never shown in the editor.
 */
export type Kind = "text" | "prose" | "latex" | "number" | "answer" | "bool" | "enum" | "expr" | "id" | "data";

export interface Leaf { path: Path; kind: Kind; value: string | number | boolean; options?: string[] }

const EXPR_KEYS = new Set(["expr", "trial", "markerX", "init", "truth", "surface"]);
const PROSE_KEYS = new Set(["md", "back", "explain", "desc", "detail", "focus", "meaning"]);
const ID_KEYS = new Set(["var", "id", "stage", "sweepVar"]);

/** A path's last named key (array indices skipped), e.g. ["series", 0, "expr"] → "expr", ["labels", 2] → "labels". */
const keyOf = (path: Path) => [...path].reverse().find((k): k is string => typeof k === "string") ?? "";

function kindOf(type: BlockType, path: Path, value: unknown): Kind {
  const key = keyOf(path);
  const top = path[0];
  if (typeof value === "boolean") return "bool";
  if (enumOptions(type, path)) return "enum";
  if (typeof value === "number") {
    if (key === "answer") return "answer";
    if (type === "attention" || (type === "sampling" && key === "logit")) return "data";
    return "number";
  }
  const s = String(value);
  if (ID_KEYS.has(key)) return "id";
  // A chart's axes name slider variables (a heatmap's or trajectory's x/y are formulas instead).
  if (type === "chart" && (key === "x" || key === "by")) return "id";
  // A scene's ids, targets, axes and fixed choices (enums inside its lists aren't found by enumOptions); its live properties are formulas.
  if (type === "scene") {
    if (["target", "x", "by", "kind", "when", "view", "format"].includes(key)) return "id";
    if (["visible", "disabled", "highlight", "if", "values"].includes(key)) return "expr";
    if (key === "rows" && s.startsWith("=")) return "expr";
  }
  // Names of derived and state variables: formulas refer to them, so renaming one would break them.
  if (key === "name" && (top === "defs" || top === "state" || top === "update")) return "id";
  if ((type === "conceptmap" || type === "architecture") && top === "edges" && (key === "from" || key === "to")) return "id";
  if (key === "latex" || key === "symbol") return "latex";
  if (EXPR_KEYS.has(key)) return "expr";
  if (type === "trajectory" && path.length === 1 && key === "steps") return "expr";
  if ((key === "from" || key === "to") && (top === "shade" || top === "band")) return "expr";
  if ((key === "x" || key === "y") && (type === "trajectory" || type === "heatmap")) return "expr";
  if (type === "bars" && top === "values") return "expr";
  if (type === "samples" && key === "values") return "expr";
  if (type === "table" && top === "rows" && s.startsWith("=")) return "expr";
  if ((type === "tokens" && top === "tokens") || ((type === "attention" || type === "sampling") && (top === "tokens" || key === "token"))) return "data";
  if (PROSE_KEYS.has(key) || (type === "reveal" && key === "answer")) return "prose";
  return "text";
}

/** Menu options for an enum field, read from the block's zod schema so they never drift. */
export function enumOptions(type: BlockType, path: Path): string[] | undefined {
  const unwrap = (s: any) => { while (s && !Array.isArray(s.options) && typeof s.unwrap === "function") s = s.unwrap(); return s; };
  let s: any = BlockSchemas[type];
  for (const k of path) {
    s = unwrap(s);
    s = typeof k === "number" ? s?.element : s?.shape?.[k];
    if (!s) return undefined;
  }
  s = unwrap(s);
  return Array.isArray(s?.options) && s.options.every((o: unknown) => typeof o === "string") ? s.options : undefined;
}

/** Every leaf value of a block with its kind, in document order. */
export function leaves(b: Block): Leaf[] {
  const out: Leaf[] = [];
  const walk = (v: unknown, path: Path) => {
    if (v === undefined || v === null) return;
    if (Array.isArray(v)) { v.forEach((x, i) => walk(x, [...path, i])); return; }
    if (typeof v === "object") { for (const [k, x] of Object.entries(v)) if (!(path.length === 0 && k === "type")) walk(x, [...path, k]); return; }
    const kind = kindOf(b.type, path, v);
    out.push({ path, kind, value: v as Leaf["value"], ...(kind === "enum" ? { options: enumOptions(b.type, path) } : {}) });
  };
  walk(b, []);
  return out;
}

// ---------- reading and writing by path ----------

export function getAt(b: unknown, path: Path): unknown {
  return path.reduce<any>((o, k) => (o == null ? undefined : o[k]), b);
}

/** A copy of the block with the value at path replaced (structural sharing elsewhere). */
export function setAt<T>(b: T, path: Path, value: unknown): T {
  if (!path.length) return value as T;
  const [k, ...rest] = path;
  const src: any = b;
  const copy: any = Array.isArray(src) ? [...src] : { ...src };
  copy[k] = setAt(src?.[k], rest, value);
  return copy;
}

// ---------- labels ----------

const NAMES: Record<string, string> = {
  md: "Text", q: "Question", expr: "Formula", markerX: "Marker at x", xMin: "X from", xMax: "X to", yMin: "Y from", yMax: "Y to",
  xLabel: "X axis label", yLabel: "Y axis label", zLabel: "Colour label", logY: "Log scale (y)", showValidation: "Show validation curve",
  normalize: "Show as % of total", diverging: "Two-colour scale", digits: "Decimals", value: "Value", valueLabel: "What one run measures",
  breaksIf: "Breaks when", front: "Front", back: "Back", explain: "Explanation", latex: "Maths", sub: "Subtitle", desc: "Description",
  init: "Start value", trial: "One run", truth: "True value", repeats: "Run buttons", on: "On label", off: "Off label",
  lesson: "What it should show", view: "Chart view", at: "Values shown",
};
const SINGULAR: Record<string, string> = { series: "Series", items: "Item", questions: "Question", cards: "Card", tabs: "Tab", options: "Option", nodes: "Node", edges: "Link", rows: "Row", steps: "Step", points: "Point", choices: "Choice", labels: "Label", values: "Value", stats: "Statistic", markers: "Marker", candidates: "Candidate", state: "State variable", update: "Update rule", terms: "Symbol", outcomes: "Outcome", tokens: "Token" };

export const humanize = (k: string) => NAMES[k] ?? (k.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, (c) => c.toUpperCase()));
export const itemName = (listKey: string, i: number) => `${SINGULAR[listKey] ?? humanize(listKey)} ${i + 1}`;

// ---------- applying changes ----------

const WRITABLE = new Set<Kind>(["text", "prose"]);
/** Kinds that may change; ids and data stay fixed. */
const EDITABLE = new Set<Kind>(["text", "prose", "latex", "number", "answer", "bool", "enum", "expr"]);

/**
 * Apply changes to a copy of the block. Only existing, editable paths may change and each value must match its kind.
 * Returns the new block and whether any text changed.
 */
export function applyChanges(b: Block, changes: { path: Path; value: unknown }[]): { block: Block; textChanged: boolean } | { error: string } {
  let next = b;
  let textChanged = false;
  for (const ch of changes) {
    if (!Array.isArray(ch.path) || !ch.path.length) return { error: "Each change needs a path (a JSON array)." };
    const leaf = leaves(next).find((l) => JSON.stringify(l.path) === JSON.stringify(ch.path));
    if (!leaf) return { error: `${JSON.stringify(ch.path)} is not a value of this part. Use a path from the Values list.` };
    if (!EDITABLE.has(leaf.kind)) return { error: `${fieldLabel(ch.path)} is locked (variable names and ids can't change).` };
    let v = ch.value;
    if (leaf.kind === "number" || leaf.kind === "answer") {
      v = typeof v === "string" ? Number(v) : v;
      if (typeof v !== "number" || !Number.isFinite(v)) return { error: `${fieldLabel(ch.path)} must be a number.` };
    } else if (leaf.kind === "bool") {
      if (typeof v !== "boolean") return { error: `${fieldLabel(ch.path)} must be true or false.` };
    } else {
      if (typeof v !== "string") return { error: `${fieldLabel(ch.path)} must be text.` };
      if (leaf.kind === "enum" && !leaf.options?.includes(v)) return { error: `${fieldLabel(ch.path)} must be one of: ${leaf.options?.join(", ")}.` };
    }
    if (WRITABLE.has(leaf.kind) && v !== leaf.value) textChanged = true;
    next = setAt(next, ch.path, v);
  }
  return { block: next, textChanged };
}

/** "Question 2 › Choice 3", "X axis label". */
export function fieldLabel(path: Path): string {
  const parts: string[] = [];
  for (let i = 0; i < path.length; i++) {
    const k = path[i];
    if (typeof k === "number") continue;
    const next = path[i + 1];
    parts.push(typeof next === "number" ? itemName(k, next) : humanize(k));
  }
  return parts.join(" › ") || "Value";
}
