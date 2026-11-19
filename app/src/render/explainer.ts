// Renders an ExplainerSpec into a full interactive page. Generic version of
// ../rag-explainer's shell: overview flowchart + step pages with progress dots,
// Prev/Next and unlocking ("pipeline" layout), or one scrolling page with a
// table of contents ("article" layout).

import { derivePalette } from "../../../shared/color";
import type { SectionTool } from "../../../shared/menus";
import { formulaKey, type Block, type ExplainerSpec, type FormulaNote } from "../../../shared/spec";
import { questionKeys, renderBlock, type Ctx } from "./components";
import { h, md, mdInline } from "./dom";
import { Store } from "./engine";
import { linkedControls } from "./linked";
import { openFormulaPanel } from "./formulapanel";
import { openSidePanel } from "./sidepanel";

const CONTROLS = new Set(["slider", "choice", "presets", "toggle"]);
const LIVE_VISUALS = new Set(["plot", "chart", "bars", "readout", "calculator", "table"]);

/**
 * A run of controls becomes one compact panel (several sliders per row), and
 * the live visuals right after it that read those variables join the same card.
 */
function groupControls(els: HTMLElement[]): HTMLElement[] {
  const out: HTMLElement[] = [];
  for (let i = 0; i < els.length; ) {
    if (!CONTROLS.has(els[i].dataset.block ?? "")) { out.push(els[i++]); continue; }
    const controls: HTMLElement[] = [];
    while (i < els.length && CONTROLS.has(els[i].dataset.block ?? "")) controls.push(els[i++]);
    const vars = new Set(controls.flatMap((c) => (c.dataset.var ?? "").split(" ").filter(Boolean)));
    const drives = (el: HTMLElement | undefined) => !!el && LIVE_VISUALS.has(el.dataset.block ?? "") && (el.dataset.uses ?? "").split(" ").some((v) => vars.has(v));
    const visuals: HTMLElement[] = [];
    while (i < els.length && drives(els[i])) visuals.push(els[i++]);
    // Visuals placed just before their controls join too (controls still go on top).
    if (!visuals.length) while (drives(out[out.length - 1])) visuals.unshift(out.pop()!);
    out.push(h("div.x-lab", null, h("div.x-lab-controls", null, ...controls), ...visuals));
  }
  return out;
}

export interface Progress {
  visited: string[];
  answers: Record<string, number>;
}

export interface ExplainerOptions {
  spec: ExplainerSpec;
  progress: Progress;
  onProgress(p: Progress): void;
  /** Link for a stage (or the overview when undefined). */
  href(stageId?: string): string;
  /** Current stage id from the route, or undefined for the overview. */
  stageId?: string;
  /** Extra elements for the top bar (back button, export...). */
  toolbar?: HTMLElement[];
  /** Optional chat panel; shown in a drawer toggled by a floating button. */
  chat?: HTMLElement | null;
  /** Section tools that call the server (spec.sectionTools; app only, never in exported pages): the app opens their panel. */
  onSectionTool?(stageIndex: number, kind: SectionTool, button: HTMLElement): void;
  /** "Go deeper" on a formula with no saved note (app only): writes one (POST /api/formula) and saves it on the page
   * (spec.formulaNotes). Without it, only formulas with a saved note get the button (exports). */
  loadFormulaNote?(stageIndex: number, block: Extract<Block, { type: "formula" }>, signal: AbortSignal): Promise<FormulaNote>;
  /** Drawer state to start in (kept by the app across re-renders of the same explainer). */
  drawer?: DrawerState;
  onDrawer?(d: DrawerState): void;
}

export interface DrawerState { open: boolean }

const SECTION_TOOL_LABEL: Record<SectionTool, string> = { search: "🔎 Search the web" };
const SECTION_TOOL_HINT: Record<SectionTool, string> = { search: "Search the web about this section with AI, with sources" };

const LEVEL_LABEL = { beginner: "Beginner friendly", student: "Student level", practitioner: "For practitioners" } as const;

/** Store is per spec object, so slider values survive page changes. */
const stores = new WeakMap<ExplainerSpec, Store>();

export function renderExplainer(root: HTMLElement, o: ExplainerOptions): () => void {
  const { spec } = o;
  const store = stores.get(spec) ?? new Store(spec);
  stores.set(spec, store);
  const disposers: (() => void)[] = [];
  const navUpdaters: (() => void)[] = [];

  const visited = new Set(o.progress.visited);
  const answers = { ...o.progress.answers };
  const save = () => o.onProgress({ visited: [...visited], answers: { ...answers } });

  const isComplete = (i: number) => {
    const st = spec.stages[i];
    return visited.has(st.id) && questionKeys(st.blocks, st.id).every((k) => k in answers);
  };
  const isUnlocked = (i: number) => spec.layout === "article" || spec.stages.slice(0, i).every((_, j) => isComplete(j));
  const status = (i: number) => (!isUnlocked(i) ? "locked" : isComplete(i) ? "done" : "ready");
  const refreshNav = () => navUpdaters.forEach((f) => f());

  // "Go deeper" on a formula block: a saved note opens at once; otherwise the app writes one. Not on Quick read pages
  // (noControls), and not in an export for a formula nobody opened.
  let closeDeeper: (() => void) | null = null;
  disposers.push(() => closeDeeper?.());
  function deeperFor(stageIndex: number, b: Extract<Block, { type: "formula" }>): ((button: HTMLElement) => void) | undefined {
    const key = formulaKey(b.latex);
    if (spec.noControls || (!spec.formulaNotes?.[key] && !o.loadFormulaNote)) return undefined;
    return (button) => {
      closeDeeper?.();
      const load = o.loadFormulaNote;
      closeDeeper = openFormulaPanel({
        host: button.closest<HTMLElement>(".explainer") ?? document.body,
        trigger: button,
        latex: b.latex,
        caption: b.caption,
        note: spec.formulaNotes?.[key],
        load: load ? (signal) => load(stageIndex, b, signal) : undefined,
      });
    };
  }

  function blocksFor(stageIndex: number): HTMLElement[] {
    const st = spec.stages[stageIndex];
    // Parts that read a control from another section get a synced copy of it right above them.
    const links = spec.noControls ? new Map() : linkedControls(spec, stageIndex);
    const ctxFor = (key: string): Ctx => ({
      store,
      key,
      answer: (k) => answers[k],
      setAnswer: (k, c) => {
        answers[k] = c;
        save();
        refreshNav();
      },
      onDispose: (fn) => disposers.push(fn),
      href: (sid) => o.href(sid),
      fromMemory: spec.fromMemory === true,
    });
    // A static (Lite+) page renders no controls; the store already holds their defaults, so visuals still draw.
    return groupControls(st.blocks.flatMap((b, i) => {
      if (spec.noControls && CONTROLS.has(b.type)) return [];
      const copies = (links.get(i) ?? []).map((l: { block: Parameters<typeof renderBlock>[0]; stage: string }) => {
        const el = renderBlock(l.block, ctxFor(`${st.id}:link:${i}`));
        el.classList.add("x-linked");
        el.append(h("span.x-linked-note", null, `Same control as in “${l.stage}”: moving either one moves both.`));
        return el;
      });
      const ctx: Ctx = {
        store,
        key: `${st.id}:${i}`,
        answer: (k) => answers[k],
        setAnswer: (k, c) => {
          answers[k] = c;
          save();
          refreshNav();
        },
        onDispose: (fn) => disposers.push(fn),
        href: (sid) => o.href(sid),
        fromMemory: spec.fromMemory === true,
        deeper: b.type === "formula" ? deeperFor(stageIndex, b) : undefined,
      };
      const el = renderBlock(b, ctx);
      return [...copies, el];
    }));
  }

  function stageHeader(i: number, asLink = false) {
    const st = spec.stages[i];
    const title = asLink ? h("a", { href: o.href(st.id) }, st.label) : st.label;
    return h("header.x-stage-head", null,
      h("div.x-stage-badge", { "aria-hidden": "true" }, h("span.x-stage-icon", null, st.icon), h("span.x-stage-num", null, String(i + 1))),
      h("div", null,
        h("div.x-stage-kicker", null, `Step ${i + 1} of ${spec.stages.length}`),
        h("h2.x-stage-title", null, title),
        h("p.x-stage-blurb", null, st.blurb),
        asLink ? null : sectionToolbar(i)));
  }

  // "Add to each section": notes written with the page open in a side panel (also in exports); web search
  // calls the server, so it is app only.
  function sectionToolbar(i: number) {
    const st = spec.stages[i];
    const notes = spec.sectionNotes?.[st.id] ?? {};
    const btn = (label: string, title: string, onclick: (b: HTMLElement) => void): HTMLElement => {
      const b = h("button.x-btn.x-btn-soft.x-btn-sm", { type: "button", "aria-haspopup": "dialog", title }, label);
      b.addEventListener("click", () => onclick(b));
      return b;
    };
    const note = (text: string, title: string) => (b: HTMLElement) => {
      const host = b.closest<HTMLElement>(".explainer") ?? document.body;
      const p = openSidePanel({ host, trigger: b, title, sub: st.label, content: [h("section.x-ex-item", null, md(text, "md x-sec-body"))] });
      disposers.push(() => p.close(false));
    };
    const buttons = [
      notes.deeper ? btn("📘 Deeper explanation", "A longer explanation of this section", note(notes.deeper, "Deeper explanation")) : null,
      notes.another ? btn("🔄 In other words", "The same explanation in different wording", note(notes.another, "In other words")) : null,
      ...(o.onSectionTool ? (spec.sectionTools ?? []) : []).map((t) => btn(SECTION_TOOL_LABEL[t], SECTION_TOOL_HINT[t], (b) => o.onSectionTool!(i, t, b))),
    ].filter((b): b is HTMLElement => !!b);
    return buttons.length ? h("div.x-stage-tools", null, ...buttons) : null;
  }

  function hero() {
    return h("header.x-hero", null,
      h("div.x-hero-meta", null, h("span.x-pill", null, LEVEL_LABEL[spec.level]), h("span.x-pill", null, `${spec.stages.length} steps`)),
      h("h1.x-hero-title", null, spec.title),
      spec.subtitle ? h("p.x-hero-sub", null, spec.subtitle) : null,
      spec.keyIntuition ? h("div.x-intuition", null, h("span.x-badge", null, "Big idea"), mdInline(spec.keyIntuition)) : null);
  }

  function glossary() {
    if (!spec.glossary.length) return null;
    return h("details.x-glossary", null,
      h("summary", null, `📖 Glossary (${spec.glossary.length} terms)`),
      h("dl", null, ...spec.glossary.flatMap((g) => [h("dt", null, g.term), h("dd", null, mdInline(g.definition))])));
  }

  // ---------- pipeline layout ----------

  function overview(): HTMLElement {
    const firstOpen = spec.stages.findIndex((_, i) => status(i) !== "done");
    const start = firstOpen < 0 ? 0 : firstOpen;
    const phases = spec.phases.map((p, pi) => {
      const items = spec.stages.map((s, i) => ({ s, i })).filter(({ s }) => s.phase === p.id);
      return h("section.x-phase", null,
        h("h3.x-phase-title", null, h("span.x-phase-num", null, String(pi + 1)), p.label),
        h("div.x-flow", null, ...items.flatMap(({ s, i }, k) => {
          const st = status(i);
          const card = st === "locked"
            ? h("div.x-flow-card.is-locked", { "aria-disabled": "true", title: "Finish the earlier steps first" })
            : h("a.x-flow-card", { href: o.href(s.id), class: st === "done" ? "is-done" : "" });
          card.append(
            h("div.x-flow-top", null, h("span.x-flow-icon", null, s.icon), h("span.x-flow-num", null, `Step ${i + 1}`),
              h("span.x-flow-status", null, st === "done" ? "✓ Done" : st === "locked" ? "🔒 Locked" : "Ready")),
            h("div.x-flow-label", null, s.label),
            h("div.x-flow-blurb", null, s.blurb));
          return k ? [h("div.x-flow-arrow", { "aria-hidden": "true" }, "↓"), card] : [card];
        })));
    });
    return h("div.x-overview", null,
      hero(),
      h("div.x-hero-cta", null, h("a.x-btn.x-btn-primary.x-btn-lg", { href: o.href(spec.stages[start].id) }, firstOpen === 0 && !visited.size ? "Start learning →" : firstOpen < 0 ? "Review from step 1 →" : `Continue: step ${start + 1} →`)),
      ...phases,
      glossary());
  }

  function stagePage(i: number): HTMLElement {
    const st = spec.stages[i];
    if (!isUnlocked(i)) {
      const first = spec.stages.findIndex((_, j) => !isComplete(j));
      return h("div.x-locked", null, h("div.x-locked-icon", null, "🔒"), h("h2", null, "This step is locked"),
        h("p", null, "Each step builds on the one before it. Finish the earlier steps first."),
        h("a.x-btn.x-btn-primary", { href: o.href(spec.stages[first].id) }, `Go to step ${first + 1}: ${spec.stages[first].label}`));
    }
    if (!visited.has(st.id)) { visited.add(st.id); save(); }

    const dots = h("nav.x-dots", { "aria-label": "Steps" });
    const prev = i > 0 ? h("a.x-btn.x-btn-ghost", { href: o.href(spec.stages[i - 1].id) }, "← Previous") : h("a.x-btn.x-btn-ghost", { href: o.href() }, "← Overview");
    const next = h("a.x-btn.x-btn-primary");
    const hint = h("span.x-nav-hint");
    const updateNav = () => {
      dots.replaceChildren(...spec.stages.map((s, j) => {
        const stt = status(j);
        const cls = `x-dot is-${stt}${j === i ? " is-current" : ""}`;
        return stt === "locked"
          ? h("span", { class: cls, title: `${s.label} (locked)` }, String(j + 1))
          : h("a", { class: cls, href: o.href(s.id), title: s.label, "aria-current": j === i ? "step" : null }, String(j + 1));
      }));
      const done = isComplete(i);
      const last = i === spec.stages.length - 1;
      next.textContent = last ? "Finish ✓" : "Next →";
      next.setAttribute("href", last ? o.href() : o.href(spec.stages[i + 1].id));
      next.classList.toggle("is-disabled", !done);
      next.setAttribute("aria-disabled", String(!done));
      hint.textContent = done ? "" : "Answer the questions above to continue.";
    };
    next.addEventListener("click", (e) => { if (!isComplete(i)) e.preventDefault(); });
    navUpdaters.push(updateNav);
    updateNav();

    return h("article.x-stage", null,
      h("div.x-stage-top", null, h("a.x-back", { href: o.href() }, "← Overview"), dots),
      stageHeader(i),
      h("div.x-blocks", null, ...blocksFor(i)),
      h("footer.x-stage-nav", null, prev, h("div.x-nav-right", null, hint, next)));
  }

  // ---------- article layout ----------

  // One scrolling page with a sticky stage nav. Nav links scroll in place
  // (see the click handler below) so widget state survives.
  function article(): HTMLElement {
    const links = spec.stages.map((s, i) =>
      h("a.x-snav-link", { href: o.href(s.id), "data-stage": s.id }, h("span.x-snav-num", null, String(i + 1)), h("span.x-snav-label", null, s.label)));
    const updateNav = () => links.forEach((a, i) => a.classList.toggle("is-done", isComplete(i)));
    navUpdaters.push(updateNav);
    spec.stages.forEach((s) => visited.add(s.id));
    updateNav();
    const sections = spec.stages.map((_, i) =>
      h("section.x-article-section", { id: `x-sec-${spec.stages[i].id}`, "data-stage": spec.stages[i].id }, stageHeader(i), h("div.x-blocks", null, ...blocksFor(i))));

    // Scroll-spy: highlight the stage in the middle of the screen.
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) {
        const id = (e.target as HTMLElement).dataset.stage;
        links.forEach((a) => a.classList.toggle("is-current", a.dataset.stage === id));
        links.find((a) => a.dataset.stage === id)?.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
    }, { rootMargin: "-40% 0px -55% 0px" });
    requestAnimationFrame(() => sections.forEach((s) => io.observe(s)));
    disposers.push(() => io.disconnect());

    return h("div.x-article", null,
      hero(),
      h("nav.x-snav", { "aria-label": "Stages" }, h("div.x-snav-inner", null, ...links)),
      ...sections,
      glossary());
  }

  // ---------- frame ----------

  const idx = o.stageId ? spec.stages.findIndex((s) => s.id === o.stageId) : -1;
  const page = spec.layout === "article" ? article() : idx >= 0 ? stagePage(idx) : overview();

  const frame = h("div.explainer", {
    "data-theme": spec.design.theme,
    "data-fonts": spec.design.fontPairing,
    "data-heading": spec.design.headingStyle,
    "data-layout": spec.layout,
    // Page brief look-and-feel (absent on older records = the normal look).
    "data-size": spec.design.textSize,
    "data-density": spec.design.density,
    "data-emphasis": spec.design.emphasis,
  });
  // Custom palette: themes.css maps these onto the theme variables per mode.
  if (spec.design.theme === "custom" && spec.design.accent) {
    const pal = derivePalette(spec.design.accent);
    for (const [k, v] of Object.entries(pal.light)) frame.style.setProperty(`--c-l-${k}`, v);
    for (const [k, v] of Object.entries(pal.dark)) frame.style.setProperty(`--c-d-${k}`, v);
  }
  if (o.toolbar?.length) frame.append(h("div.x-toolbar", null, ...o.toolbar));

  // Hovering a formula symbol highlights its slider, and the reverse.
  let linked = "";
  const setLinked = (v: string) => {
    if (v === linked) return;
    frame.querySelectorAll(".is-linked").forEach((el) => el.classList.remove("is-linked"));
    linked = v;
    if (v) frame.querySelectorAll(`[data-var="${CSS.escape(v)}"], [data-link-var="${CSS.escape(v)}"]`).forEach((el) => el.classList.add("is-linked"));
  };
  frame.addEventListener("pointerover", (e) => {
    const el = (e.target as Element).closest("[data-var], [data-link-var]");
    setLinked(el?.getAttribute("data-var") ?? el?.getAttribute("data-link-var") ?? "");
  });
  // Article layout: in-page stage links scroll instead of re-routing.
  if (spec.layout === "article") {
    frame.addEventListener("click", (e) => {
      const a = (e.target as Element).closest<HTMLAnchorElement>("a[data-stage]");
      if (!a || e.ctrlKey || e.metaKey || e.shiftKey) return;
      const sec = frame.querySelector(`#x-sec-${CSS.escape(a.dataset.stage!)}`);
      if (!sec) return;
      e.preventDefault();
      sec.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }
  frame.append(h("div.x-page", null, page));

  // The drawer: "Ask about this" (tutor bot).
  if (o.chat) {
    const state: DrawerState = { open: false, ...o.drawer };
    const pane = h("div.x-drawer-pane", { "data-pane": "ask" }, o.chat);
    const drawer = h("aside.x-chat-drawer", { "aria-label": "Ask about this explainer" }, pane);
    const fab = h("button.x-chat-fab", { type: "button", "aria-expanded": "false" }, h("span", { "aria-hidden": "true" }, "💬"), " Ask about this");
    function set(d: Partial<DrawerState>, notify = true) {
      Object.assign(state, d);
      frame.classList.toggle("chat-open", state.open);
      fab.setAttribute("aria-expanded", String(state.open));
      if (notify) o.onDrawer?.({ ...state });
      if (state.open && d.open) pane.querySelector("textarea")?.focus();
    }
    fab.addEventListener("click", () => set({ open: !state.open }));
    drawer.addEventListener("x-close", () => set({ open: false }));
    frame.append(fab, drawer);
    set({}, false);
  }

  root.replaceChildren(frame);
  if (spec.layout === "article" && o.stageId) document.getElementById(`x-sec-${o.stageId}`)?.scrollIntoView({ block: "start" });
  else root.closest(".scroll-host")?.scrollTo?.({ top: 0 });

  return () => disposers.forEach((d) => d());
}

