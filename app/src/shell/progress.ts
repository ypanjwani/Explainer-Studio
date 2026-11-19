// The locked result card shown while an explainer builds: a progress ring on its band, the title/subtitle once the
// plan is written and the theme/icons once the design is chosen (step `preview`s from the server), unlocked at done.
//
// The percent is an estimate: each step has an expected duration per mode, the running step moves with time (from its
// `at`, so a reload resumes in place), it never goes backwards and holds at 95% until the page is done. A revise
// follow-up has no fixed steps (one agent may answer, ask or edit), so its ring spins with no number; so does a new page
// while the first-message step (FIRST_TALK, `intake`) reads the message, which may end in an answer and no page.

import { derivePalette } from "../../../shared/color";
import type { ProgressStep, StepPreview } from "../../../shared/spec";
import { h } from "../render/dom";
import type { ExplainerRecord, StepState } from "../store/history";

/** Expected seconds per step. Steps not listed for a mode (worked examples, notes) are added only once they happen. */
const SECONDS: Record<ProgressStep, number> = {
  intake: 3, read: 8, summarize: 15, plan: 30, components: 30, interaction: 50, design: 3, build: 3,
  examples: 10, clarity: 10, check: 10, notes: 5, improve: 10,
};
const READER_PLAN = 25;
/** Every step in the order the pipelines run them (modes/reader.py, modes/explorer/pipeline.py, modes/common.py). */
const PIPELINE: ProgressStep[] = ["intake", "read", "summarize", "plan", "components", "interaction", "design", "build", "examples", "clarity", "check", "notes", "improve"];
/** The ring never shows more than this until the page is done. */
export const HOLD = 95;

type Rec = Pick<ExplainerRecord, "id" | "request" | "steps" | "followUp">;

/** True while only the first-message step has run: it may answer instead of building. */
export const reading = (rec: Pick<ExplainerRecord, "steps">): boolean => {
  const keys = Object.keys(rec.steps ?? {});
  return keys.length > 0 && keys.every((k) => k === "intake");
};

/** True for a run whose length can't be estimated: a revise follow-up, or a new page whose message is still being read. */
export const spins = (rec: Pick<ExplainerRecord, "followUp"> & Partial<Pick<ExplainerRecord, "steps">>): boolean =>
  rec.followUp?.kind === "revise" || reading({ steps: rec.steps ?? {} });

/** The steps this run is expected to go through, in order, with their expected seconds. */
export function expectedSteps(rec: Rec): [ProgressStep, number][] {
  const reader = rec.request.quality === "lite-plus";
  const expected = new Set<ProgressStep>([
    ...(rec.request.mode === "paper" ? ["read", "summarize"] as const : []),
    "plan",
    ...(reader ? [] : ["interaction"] as const),
    "design", "build",
    ...(reader ? [] : ["clarity", "check"] as const),
  ]);
  // A step that happened but wasn't expected (worked examples, notes) takes its place in the pipeline's order.
  const seen = new Set(Object.keys(rec.steps));
  return PIPELINE.filter((s) => expected.has(s) || seen.has(s)).map((s) => [s, s === "plan" && reader ? READER_PLAN : SECONDS[s] ?? 5]);
}

/** Share (0..0.95) of a running step's expected time used: linear to 80%, then creeping towards 95%. */
export function stepShare(elapsed: number, expected: number): number {
  const t = Math.max(0, elapsed) / expected;
  return t < 0.8 ? t : Math.min(0.95, 0.8 + 0.15 * (1 - Math.exp(-(t - 0.8))));
}

/** The raw estimate in percent (0..HOLD), without the never-backwards rule. */
export function estimate(rec: Rec, now: number): number {
  const steps = expectedSteps(rec);
  const total = steps.reduce((n, [, d]) => n + d, 0);
  // Expected steps before the furthest one reached were skipped (or happened unseen): they count as done.
  let last = -1;
  steps.forEach(([s], i) => { if (rec.steps[s]) last = i; });
  let done = 0;
  steps.forEach(([s, d], i) => {
    const st = rec.steps[s];
    if (st?.status === "done" || (!st && i < last)) done += d;
    else if (st?.status === "running") done += d * stepShare((now - (st.at ?? now)) / 1000, d);
  });
  return Math.min(HOLD, (done / total) * 100);
}

const best = new Map<string, number>();

/** The percent to show: the estimate, never lower than what this tab showed before. */
export function percent(rec: Rec, now = Date.now()): number {
  const p = Math.max(best.get(rec.id) ?? 0, Math.floor(estimate(rec, now)));
  best.set(rec.id, p);
  return p;
}

/** Everything the steps' previews have told so far (later steps win). */
export function previewOf(steps: Partial<Record<ProgressStep, StepState>>): StepPreview {
  return Object.values(steps).reduce<StepPreview>((acc, st) => ({ ...acc, ...(st?.preview ?? {}) }), {});
}

/** The step running now (the last one started), if any. */
export function currentStep(rec: Rec): [ProgressStep, StepState] | undefined {
  const running = (Object.entries(rec.steps) as [ProgressStep, StepState][]).filter(([, st]) => st.status === "running");
  return running.sort((a, b) => (a[1].at ?? 0) - (b[1].at ?? 0)).at(-1);
}

const R = 22;
const CIRC = 2 * Math.PI * R;

function ringSvg(spin: boolean): string {
  return `<svg viewBox="0 0 56 56" width="56" height="56" aria-hidden="true"><circle class="gen-ring-track" cx="28" cy="28" r="${R}"/>`
    + `<circle class="gen-ring-fill" cx="28" cy="28" r="${R}" stroke-dasharray="${CIRC.toFixed(2)}" stroke-dashoffset="${spin ? (CIRC * 0.72).toFixed(2) : CIRC.toFixed(2)}" transform="rotate(-90 28 28)"/></svg>`;
}

function paint(el: HTMLElement, rec: Rec) {
  if (spins(rec) || el.classList.contains("is-failed")) return;
  const p = percent(rec);
  el.querySelector<SVGCircleElement>(".gen-ring-fill")?.setAttribute("stroke-dashoffset", (CIRC * (1 - p / 100)).toFixed(2));
  const num = el.querySelector(".gen-ring-num");
  if (num) num.textContent = `${p}%`;
  el.setAttribute("aria-valuenow", String(p));
}

// One ticker moves every ring on screen between server events; it stops when no ring is left.
const rings = new Map<HTMLElement, Rec>();
let timer: number | undefined;

function tick() {
  for (const [el, rec] of rings) {
    if (el.isConnected) paint(el, rec);
    else rings.delete(el);
  }
  if (!rings.size && timer !== undefined) { clearInterval(timer); timer = undefined; }
}

function ring(rec: Rec, failed: boolean): HTMLElement {
  const spin = spins(rec) && !failed;
  const el = h("div.gen-ring", {
    class: [spin ? "is-spinning" : "", failed ? "is-failed" : ""].filter(Boolean).join(" ") || undefined,
    role: "progressbar", "aria-label": failed ? "Stopped" : "Building your explainer",
    "aria-valuemin": "0", "aria-valuemax": "100",
  }, h("div", { html: ringSvg(spin) }), h("span.gen-ring-num", null, failed ? "!" : spin ? "" : "0%"), h("span.gen-ring-lock", { "aria-hidden": "true" }, "🔒"));
  if (failed) return el;
  paint(el, rec);
  if (!spin) {
    rings.set(el, rec);
    // A ticker from before a hot reload or an earlier render may be gone; start one if none runs.
    timer ??= window.setInterval(tick, 500);
  }
  return el;
}

// Records this tab saw building: their finished card unlocks with an animation once.
const seen = new Set<string>();

/** True the first time a record this tab saw building is drawn finished. */
export function justUnlocked(id: string): boolean {
  return seen.delete(id);
}

/** The locked card: same shape as the finished one, with the ring on its band. */
export function lockedCard(rec: Rec, opts: { failed?: boolean } = {}): HTMLElement {
  const failed = !!opts.failed;
  if (!failed) seen.add(rec.id);
  else seen.delete(rec.id);
  const pv = previewOf(rec.steps);
  const theme = /^[a-z][a-z-]{0,30}$/.test(pv.theme ?? "") ? pv.theme! : "";
  const band = h("div.result-band.gen-band", {
    class: theme ? `t-${theme}` : "is-pending",
    style: theme === "custom" && pv.accent ? `background: ${derivePalette(pv.accent).light.grad}` : undefined,
  }, h("span.gen-icons", null, (pv.icons ?? []).join("  ")), ring(rec, failed));
  const meta = failed ? "Stopped before it was ready" : rec.followUp?.kind === "revise" ? "Updating your explainer…" : spins(rec) ? "Reading your message…" : "Building… opens when it reaches 100%";
  return h("div.result-card.is-locked", { class: failed ? "is-failed" : undefined, "data-theme": theme || undefined, "aria-busy": failed ? undefined : "true" },
    band,
    h("div.result-body", null,
      pv.title ? h("div.result-title", null, pv.title) : h("div.skel.skel-title", { "aria-hidden": "true" }),
      pv.subtitle ? h("div.result-sub", null, pv.subtitle) : h("div.skel.skel-sub", { "aria-hidden": "true" }),
      h("div.result-meta", null, meta)),
    h("span.result-open", { "aria-disabled": "true" }, "🔒 Open"));
}
