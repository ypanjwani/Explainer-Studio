// App shell, modelled on ChatGPT: a history sidebar, a centred composer on the
// home screen, a conversation view showing generation progress, and the
// finished explainer opening full-width in the main area.
//
// Signed out, every route shows the sign-in screen (account.ts).
//
// Routes: #/                 home
//         #/c/<id>           conversation (request + progress + result card)
//         #/c/<id>/x[/stage] the explainer

import { derivePalette } from "../../../shared/color";
import { buildFollowUp } from "../../../shared/followup";
import { CHECKED_PREFIX, LIMITS, PICK_MODES, formulaKey, isQuality, type ExplainerSpec, type FollowUp, type FollowUpPick, type GenerateRequest, type FollowUpPlan, type PlanOption, type PlanResponse, type ProgressStep, type RouteResponse } from "../../../shared/spec";
import { chat, ConnectionLost, formulaDeeper, generateExplainer, pickFollowUp, planFollowUp, routeFollowUp, sectionTool, type FeedbackConfirmed } from "../api";
import { openFeedbackDialog } from "./feedback";
import { allowance, onAuthChange, refreshAllowance, restore, type User } from "../auth";
import { createChat } from "../render/chatbot";
import { h, mdToHtml, uid } from "../render/dom";
import { inheritState, renderExplainer, storeFor, type DrawerState } from "../render/explainer";
import { exportHtml } from "../render/export";
import { LINK_ICON, openShareDialog } from "./share";
import { closeHistory, deleteRecord, fetchRecord, flushAll, flushRecord, getRecord, importLegacy, legacyRecords, openHistory, putRecord, type ExplainerRecord } from "../store/history";
import { allowanceMeter, authView, legacyOffer, openAccountPanel } from "./account";
import { createComposer, type ComposerChoices, type ComposerEl, type ComposerSubmit } from "./composer";
import { LEVEL_CHOICES } from "../../../shared/menus";
import { createEditPanel, type EditTarget } from "./edit";
import { openSearchPanel, sectionText } from "./section";
import { applyMode, isDark, loadMode, modeToggle, setRootMode } from "./mode";
import { howLong, modeName } from "./modes";
import { extrasQuestion, extrasSummary } from "./extras";
import type { Extra } from "../../../shared/menus";

/** ✏️ Edit chat (per-part Edit buttons + the drawer's Edit tab). Hidden in every mode until version 2; the code stays. */
const EDIT_CHAT = false;
/**
 * Follow-ups v3 (server/explainer/understand.py): a revise follow-up on a Playground or Quick read page asks /api/followup/plan
 * first. The server answers "off" unless FOLLOWUP_PLAN=1, and the app then uses /api/followup/pick as before.
 */
const PLAN_FOLLOWUPS = true;
/** The Feedback button on an open explainer (feedback.ts, /api/feedback). Hidden in every mode for now; the code stays. */
const FEEDBACK = false;
/** "What do you want to add to each section?" (extras.ts). Hidden for v1: new explainers get no extras ("None"); the code stays. */
const EXTRAS_QUESTION = false;
/**
 * The AI cost on the result card ("≈ $0.006"), hidden from readers for v1 (Nov 2026). The data is still saved.
 */
const SHOW_COST = false;

const SUGGESTIONS = [
  { label: "Attention in transformers", text: "Explain self-attention in transformers: queries, keys, values and why it works." },
  { label: "LoRA fine-tuning", text: "Explain LoRA (low-rank adaptation) and why it makes fine-tuning cheap." },
  { label: "Diffusion models", text: "Explain how diffusion models generate images by learning to remove noise." },
  { label: "Temperature & top-p", text: "Explain temperature, top-k and top-p sampling in language models." },
];

const STEP_LABEL: Record<ProgressStep, string> = {
  read: "Reading the paper",
  summarize: "Summarizing sections",
  plan: "Planning the lesson",
  components: "Choosing and building interactive parts",
  interaction: "Designing the interactive parts",
  design: "Choosing the design",
  build: "Building the page",
  examples: "Writing worked examples",
  clarity: "Checking clarity",
  check: "Fact-checking",
  notes: "Writing the section extras",
  improve: "Checking and improving",
};

const ICON = {
  sidebar: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 3v18"/></svg>',
  edit: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
  request: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  download: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>',
  pencil: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
  trash: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
  play: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m10 8 6 4-6 4z"/></svg>',
  plus: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>',
  spark: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/></svg>',
  dots: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>',
  retry: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/></svg>',
  copy: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
  check: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
};

const icon = (svg: string) => h("span", { "aria-hidden": "true", class: "icon", html: svg });

export class App {
  private records: ExplainerRecord[] = [];
  private user: User | null = null;
  /** Polling the server for generations this tab isn't streaming (started before a reload, or the stream broke). */
  private polling = false;
  private running = new Map<string, AbortController>();
  private disposeView: (() => void) | null = null;
  private menuOpen: string | null = null;
  /** Edit chat for the open explainer: its panel and in-memory undo history. */
  private edit: { recId: string; panel: ReturnType<typeof createEditPanel>; undo: ExplainerSpec[] } | null = null;
  /** The Ask/Edit drawer, kept open across the re-render after each edit. */
  private drawer: DrawerState = { open: false, tab: "ask" };
  /** Closes the open section tool panel, if any. */
  private closeSection: (() => void) | null = null;
  /** Follow-ups whose meaning (revise or new?) is still being worked out. */
  private routing = new Set<string>();
  /** The conversation's follow-up box, kept across re-renders so typed text survives progress updates. */
  private dock: { root: string; el: HTMLElement; composer: ComposerEl } | null = null;
  /** The open "Add section" box, under the conversation's latest explainer. */
  private addBox?: { recId: string; el: HTMLElement };
  /** The open Edit box or Retry confirm under a prompt. Kept across re-renders, so typed text survives progress updates. */
  private promptBox?: { recId: string; kind: "edit" | "retry"; el: HTMLElement };

  private sidebar = h("aside.sidebar.shell", { "aria-label": "Explainer history" });
  private topbar = h("header.topbar.shell");
  private view = h("main.view.scroll-host");
  /** Notices above the view (the offer to import pre-account history). */
  private banner = h("div.banner-host.shell");
  private root = h("div.app", null, this.sidebar, h("div.scrim", { onclick: () => this.setSidebar(false) }), h("div.main", null, this.topbar, this.banner, this.view));

  async start(mount: HTMLElement) {
    mount.replaceChildren(this.root);
    applyMode(loadMode());
    if (window.matchMedia("(max-width: 800px)").matches) this.root.classList.add("sb-closed");
    this.view.replaceChildren(h("div.home.shell", null, h("p.home-sub", { role: "status" }, "Loading…")));
    window.addEventListener("hashchange", () => this.route());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.menuOpen) { this.menuOpen = null; this.drawSidebar(); }
    });
    // Pending saves go out before the tab is hidden or closed.
    document.addEventListener("visibilitychange", () => { if (document.hidden) flushAll(); });
    window.addEventListener("pagehide", () => flushAll());
    onAuthChange((u) => this.setUser(u));
    await this.setUser(await restore(), true);
  }

  /** Signed in (load the account's history) or out (forget it). */
  private async setUser(u: User | null, first = false) {
    if (!first && u?.id === this.user?.id) return;
    for (const c of this.running.values()) c.abort();
    this.running.clear();
    this.user = u;
    this.edit = null;
    this.dock = null;
    this.banner.replaceChildren();
    this.records = [];
    if (!u) {
      closeHistory();
      this.route();
      return;
    }
    try {
      this.records = await openHistory(u.id, (msg) => toast(msg));
    } catch (e) {
      toast(e instanceof Error ? `Couldn't load your history: ${e.message}` : "Couldn't load your history.");
    }
    if (this.user?.id !== u.id) return; // signed out (or switched) while loading
    // Old records still waiting for the retired palette question: let them be retried (they run as Quality+).
    for (const r of this.records.filter((r) => (r.status as string) === "choosing")) {
      r.status = "error";
      r.error = "This explainer was waiting for a design choice that no longer exists. Try again.";
      putRecord(r);
    }
    // A follow-up interrupted while its sections were being picked: ask "change it or new?" again (a saved question stays).
    for (const r of this.records.filter((r) => r.status === "picking" && !r.pick && !r.plan)) {
      r.status = "deciding";
      putRecord(r);
    }
    // A retry that finished while no tab watched it: delete what it replaced, or bring that back if it failed.
    for (const r of this.records.filter((r) => r.replaces && (r.status === "done" || r.status === "error"))) await this.settleRetry(r);
    this.route();
    this.pollGenerating();
    this.offerLegacy();
  }

  /** History saved in this browser before accounts: offer to add it to the account. */
  private async offerLegacy() {
    const old = await legacyRecords().catch(() => [] as ExplainerRecord[]);
    if (!old.length || !this.user) return;
    this.banner.replaceChildren(legacyOffer(old.length, async (progress) => {
      const n = await importLegacy(old, progress);
      const added = (await Promise.all(old.map((o) => getRecord(o.id)))).filter((r): r is ExplainerRecord => !!r);
      this.records = [...this.records.filter((r) => !added.some((a) => a.id === r.id)), ...added];
      this.drawSidebar();
      return n;
    }, () => this.banner.replaceChildren()));
  }

  /**
   * Generations running on the server that this tab isn't streaming (it was reloaded, or the connection
   * broke): poll their records until they finish. The server saves the result whether or not anyone watches.
   */
  private async pollGenerating() {
    if (this.polling) return;
    this.polling = true;
    try {
      for (;;) {
        const waiting = this.records.filter((r) => r.status === "generating" && !this.running.has(r.id));
        if (!waiting.length || !this.user) break;
        await new Promise((r) => setTimeout(r, 4000));
        for (const r of waiting) {
          const fresh = await fetchRecord(r.id);
          const i = this.records.findIndex((x) => x.id === r.id);
          if (!fresh || i < 0 || this.running.has(r.id)) continue;
          const changed = fresh.status !== r.status || JSON.stringify(fresh.steps) !== JSON.stringify(r.steps);
          this.records[i] = fresh;
          if (changed) this.refresh(fresh.id);
          if (fresh.status !== "generating") refreshAllowance().then(() => this.drawSidebar());
          if ((fresh.status === "done" || fresh.status === "error") && fresh.replaces) await this.settleRetry(fresh);
        }
      }
    } finally {
      this.polling = false;
    }
  }

  // ---------- routing ----------

  private parts() {
    return location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  }

  private async route() {
    this.disposeView?.();
    this.disposeView = null;
    const [kind, id, x, stage] = this.parts();
    this.root.classList.toggle("in-explainer", kind === "c" && x === "x");
    this.root.classList.toggle("signed-out", !this.user);
    if (window.matchMedia("(max-width: 800px)").matches) this.setSidebar(false);

    if (!this.user) this.view.replaceChildren(authView());
    else if (kind === "c" && id) {
      const rec = this.records.find((r) => r.id === id) ?? (await getRecord(id));
      if (!rec) { location.hash = "#/"; return; }
      const replacing = this.replacerOf(rec);
      if (replacing) { location.hash = `#/c/${replacing.id}`; return; }
      if (x === "x" && rec.spec) this.showExplainer(rec, stage);
      else this.showConversation(rec);
    } else this.showHome();
    this.drawSidebar();
    this.drawTopbar();
  }

  private currentId(): string | undefined {
    const [kind, id] = this.parts();
    return kind === "c" ? id : undefined;
  }

  // ---------- sidebar ----------

  private setSidebar(open: boolean) {
    this.root.classList.toggle("sb-closed", !open);
  }

  private drawSidebar() {
    const current = this.records.find((r) => r.id === this.currentId());
    const active = current ? this.rootOf(current) : undefined;
    const groups = new Map<string, ExplainerRecord[]>();
    const now = new Date();
    const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    // One entry per conversation, ordered by its latest activity.
    const last = (root: string) => Math.max(...this.threadOf(root).map((r) => r.updatedAt));
    const heads = this.records.filter((r) => this.rootOf(r) === r.id && !this.replacerOf(r)).sort((a, b) => last(b.id) - last(a.id));
    for (const r of heads) {
      const t = last(r.id);
      const g = t >= startOfDay ? "Today" : t >= startOfDay - 86_400_000 ? "Yesterday" : t >= startOfDay - 7 * 86_400_000 ? "Previous 7 days" : "Older";
      (groups.get(g) ?? groups.set(g, []).get(g)!).push(r);
    }

    const list = h("nav.sb-list");
    if (!this.user) list.append(h("p.sb-empty", null, "Sign in to make explainers. Your history is saved to your account."));
    else if (!this.records.length) list.append(h("p.sb-empty", null, "Your explainers will appear here."));
    for (const [label, recs] of groups) {
      list.append(h("div.sb-group", null, label));
      for (const r of recs) list.append(this.sidebarItem(r, r.id === active));
    }

    this.sidebar.replaceChildren(
      h("div.sb-top", null,
        h("a.brand-logo", { href: "#/" }, h("span.brand-mark", { "aria-hidden": "true" }, "✦"), "ClariTale"),
        h("button.icon-btn", { type: "button", title: "Close sidebar", "aria-label": "Close sidebar", onclick: () => this.setSidebar(false) }, icon(ICON.sidebar))),
      h("a.sb-new", { href: "#/" }, icon(ICON.plus), "New explainer"),
      list,
      h("div.sb-bottom", null,
        this.user ? allowanceMeter(allowance()) : null,
        modeToggle(),
        this.user
          ? h("button.sb-account", { type: "button", onclick: () => openAccountPanel() },
            h("span.sb-avatar", { "aria-hidden": "true" }, this.user.email.slice(0, 1).toUpperCase()),
            h("span.sb-account-email", null, this.user.email))
          : h("a.sb-account", { href: "#/" }, h("span.sb-avatar", { "aria-hidden": "true" }, "→"), "Sign in")));
  }

  /** r: a conversation's first record. Links to its latest explainer; delete removes the whole conversation. */
  private sidebarItem(r: ExplainerRecord, active: boolean): HTMLElement {
    const thread = this.threadOf(r.id);
    const latest = thread.at(-1)!;
    const lastDone = [...thread].reverse().find((x) => x.spec);
    const href = lastDone && latest.status === "done" ? `#/c/${lastDone.id}/x` : `#/c/${latest.id}`;
    const status = thread.some((x) => x.status === "generating") ? h("span.sb-spinner", { "aria-label": "Generating" })
      : latest.status === "error" ? h("span.sb-err", { title: "Failed" }, "!")
      : thread.some((x) => x.status === "deciding" || x.status === "asking" || (x.status === "picking" && (x.pick || x.plan))) ? h("span.sb-ask", { title: "Waiting for your answer" }) : null;
    const item = h("div.sb-item", { class: active ? "is-active" : "" },
      h("a.sb-item-link", { href, title: r.title }, status, h("span.sb-item-title", null, r.title)),
      h("button.sb-item-menu", { type: "button", "aria-label": `Options for ${r.title}`, "aria-expanded": String(this.menuOpen === r.id), onclick: (e: Event) => { e.stopPropagation(); this.menuOpen = this.menuOpen === r.id ? null : r.id; this.drawSidebar(); } }, icon(ICON.dots)));
    if (this.menuOpen === r.id) {
      let confirm = false;
      const del = h("button.sb-menu-item.is-danger", { type: "button" }, icon(ICON.trash), h("span", null, thread.length > 1 ? "Delete conversation" : "Delete"));
      del.addEventListener("click", async () => {
        if (!confirm) { confirm = true; del.lastElementChild!.textContent = thread.length > 1 ? `Click again to delete all ${thread.length}` : "Click again to delete"; return; }
        const ids = new Set(thread.map((x) => x.id));
        try {
          for (const id of ids) { this.running.get(id)?.abort(); await deleteRecord(id); }
        } catch (e) {
          toast(e instanceof Error ? `Couldn't delete: ${e.message}` : "Couldn't delete.");
        }
        this.records = this.records.filter((x) => !ids.has(x.id));
        this.menuOpen = null;
        if (ids.has(this.currentId() ?? "")) location.hash = "#/";
        else this.drawSidebar();
      });
      item.append(h("div.sb-menu", { role: "menu" },
        h("button.sb-menu-item", { type: "button", onclick: () => this.rename(r, item) }, icon(ICON.pencil), "Rename"),
        lastDone ? h("button.sb-menu-item", { type: "button", onclick: () => { this.menuOpen = null; this.drawSidebar(); this.doExport(lastDone.spec!); } }, icon(ICON.download), thread.length > 1 ? "Export latest" : "Export HTML") : null,
        lastDone ? h("button.sb-menu-item", { type: "button", onclick: () => { this.menuOpen = null; this.drawSidebar(); this.doShare(lastDone.id); } }, icon(LINK_ICON), thread.length > 1 ? "Share latest" : "Share link") : null,
        del));
    }
    return item;
  }

  private rename(r: ExplainerRecord, item: HTMLElement) {
    this.menuOpen = null;
    const input = h("input.sb-rename", { value: r.title, "aria-label": "New title", maxlength: 120 });
    const done = async (save: boolean) => {
      if (save && input.value.trim()) {
        r.title = input.value.trim();
        await putRecord(r);
      }
      this.drawSidebar();
      this.drawTopbar();
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); });
    input.addEventListener("blur", () => done(true));
    item.replaceChildren(input);
    input.focus();
    input.select();
  }

  private drawTopbar() {
    const [kind, id, x] = this.parts();
    const rec = kind === "c" ? this.records.find((r) => r.id === id) : undefined;
    // A conversation is named after its first explainer; an open explainer by its own title.
    const head = rec && x !== "x" ? this.records.find((r) => r.id === this.rootOf(rec)) : undefined;
    const title = rec ?(head ?? rec).title : "ClariTale";
    const actions: HTMLElement[] = [];
    if (rec?.spec && x === "x") {
      actions.push(h("a.tb-btn", { href: `#/c/${rec.id}` }, icon(ICON.request), "Request"));
      actions.push(h("button.tb-btn", { type: "button", onclick: () => this.doExport(rec.spec!) }, icon(ICON.download), "Export"));
      actions.push(h("button.tb-btn", { type: "button", onclick: () => this.doShare(rec.id) }, icon(LINK_ICON), "Share"));
      if (FEEDBACK) actions.push(h("button.tb-btn", { type: "button", onclick: () => this.openFeedback(rec) }, icon(ICON.spark), "Feedback"));
    } else if (rec?.spec) {
      actions.push(h("a.tb-btn.is-primary", { href: `#/c/${rec.id}/x` }, "Open explainer", h("span", { "aria-hidden": "true" }, "→")));
    }
    this.topbar.replaceChildren(
      h("button.icon-btn.tb-toggle", { type: "button", title: "Open sidebar", "aria-label": "Open sidebar", onclick: () => this.setSidebar(true) }, icon(ICON.sidebar)),
      h("a.icon-btn.tb-toggle", { href: "#/", title: "New explainer", "aria-label": "New explainer" }, icon(ICON.edit)),
      h("div.tb-title", null, title),
      h("div.tb-actions", null, ...actions));
  }

  private async doExport(spec: ExplainerSpec) {
    try {
      // A page with no light/dark of its own (older ones, the sample) exports in the mode on screen now.
      const own = spec.design.mode === "light" || spec.design.mode === "dark";
      await exportHtml(own ? spec : { ...spec, design: { ...spec.design, mode: isDark() ? "dark" : "light" } });
    } catch (e) {
      toast(e instanceof Error ? e.message : "Export failed.");
    }
  }

  /** "Share link": a public link to this explainer (shell/share.ts); a page with no light/dark of its own uses the one on screen. */
  private doShare(recordId: string) {
    openShareDialog(recordId, isDark() ? "dark" : "light").catch((e) => toast(e instanceof Error ? e.message : "Sharing failed."));
  }

  // ---------- views ----------

  private showHome() {
    const composer = createComposer({ placeholder: "Describe an AI concept, e.g. “How does LoRA fine-tuning work?”", autofocus: true, onSubmit: (s) => this.submit(s) });
    const fill = (composer as HTMLElement & { fill(t: string): void }).fill;
    this.view.replaceChildren(h("div.home.shell", null,
      h("div.home-eyebrow", null, h("span.brand-mark", { "aria-hidden": "true" }, "✦"), "ClariTale"),
      h("h1.home-title", null, "What do you want to ", h("em", null, "understand"), "?"),
      h("p.home-sub", null, "Describe an AI concept or attach a research paper. You'll get a page with sliders, live charts, quizzes and a tutor to ask."),
      composer,
      h("div.home-suggest", null, h("span.home-suggest-label", null, "Try one:"), ...SUGGESTIONS.map((s) => h("button.suggest", { type: "button", onclick: () => fill(s.text) }, icon(ICON.spark), s.label))),
      allowanceMeter(allowance())));
  }

  /** A conversation: every record in the thread, oldest first, then the follow-up box. */
  private showConversation(rec: ExplainerRecord) {
    const root = this.rootOf(rec);
    const thread = this.threadOf(root);
    const latest = thread.at(-1);
    // Keep the box (and anything typed in it) across the re-renders during generation.
    if (this.dock?.root !== root) {
      const composer = createComposer({ placeholder: "Follow up…", onSubmit: (s) => this.submit(s, root) });
      this.dock = { root, composer, el: h("div.convo-dock", null, composer, h("p.dock-note", null, "Follow-ups continue this conversation: change the explainer above (a new version) or explain a related topic.")) };
    }
    const list = h("div.thread", null, ...thread.flatMap((r) => this.turn(r, thread)));
    this.view.replaceChildren(h("div.convo.shell", null, list, this.dock.el));
    // Opening an older message of the conversation scrolls to it; otherwise follow the latest.
    if (rec !== latest) list.querySelector(`[data-rec="${CSS.escape(rec.id)}"]`)?.scrollIntoView({ block: "start" });
    else this.view.scrollTo({ top: this.view.scrollHeight });
  }

  /** One request and its reply. */
  private turn(rec: ExplainerRecord, thread: ExplainerRecord[]): HTMLElement[] {
    const isPaper = rec.request.mode === "paper";
    const order: ProgressStep[] = isPaper ? ["read", "summarize", "plan", "design", "build"] : ["plan", "design", "build"];
    // Optional steps appear only once they have happened (lab components, worked examples).
    if (rec.steps.components) order.splice(order.indexOf("plan") + 1, 0, "components");
    if (rec.steps.interaction) order.splice(order.indexOf("plan") + 1, 0, "interaction");
    if (rec.steps.examples) order.push("examples");
    if (rec.steps.clarity) order.push("clarity");
    if (rec.steps.check) order.push("check");
    if (rec.steps.improve) order.push("improve");
    if (rec.steps.notes) order.push("notes");

    // Under the prompt: ↻ Retry, Copy, ✏️ Edit. Retry and Edit only on the conversation's last message, once it has
    // finished or failed, so no later version is built on a page that goes away.
    const canRedo = thread.at(-1) === rec && ((rec.status === "done" && !!rec.spec) || rec.status === "error");
    if (this.promptBox?.recId === rec.id && !canRedo) this.promptBox = undefined;
    const open = this.promptBox?.recId === rec.id ? this.promptBox : undefined;
    const userMsg = h("div.msg.is-user", { "data-rec": rec.id, class: open?.kind === "edit" ? "is-editing" : undefined },
      rec.prompt.fileName ? h("div.msg-file", null, h("div.cmp-file-icon", null, "PDF"), h("div", null, h("div.cmp-file-name", null, rec.prompt.fileName), h("div.cmp-file-sub", null, `${rec.prompt.pages ?? "?"} pages`))) : null,
      open?.kind === "edit" ? open.el : rec.prompt.text ? h("div.msg-bubble", null, rec.prompt.text) : null,
      open?.kind === "edit" ? null : this.promptActions(rec, canRedo),
      open?.kind === "retry" ? open.el : null);

    const steps = h("ol.steps", null, ...order.map((s) => {
      const st = rec.steps[s];
      const state = st?.status === "done" ? "done" : st?.status === "running" ? "running" : rec.status === "error" ? "skipped" : "pending";
      return h("li.step", { class: `is-${state}` },
        h("span.step-mark", { "aria-hidden": "true" }, state === "done" ? "✓" : ""),
        h("span.step-label", null, STEP_LABEL[s]),
        st?.detail ? h("span.step-detail", null, plain(st.detail)) : null);
    }));

    const body: HTMLElement[] = [];
    const base = rec.followUp ? thread.find((r) => r.id === rec.followUp!.baseId) : undefined;
    if (rec.status === "deciding") {
      if (this.routing.has(rec.id)) body.push(h("p.msg-text.is-muted", null, "Reading your message…"));
      else {
        const target = this.followUpBase(this.rootOf(rec), rec);
        body.push(
          h("p.msg-text", null, target ? `Should I change “${target.spec!.title}”, or make a new explainer about this?` : "Should I make a new explainer about this?"),
          h("div.msg-choices", null,
            target ? h("button.btn-pill", { type: "button", onclick: () => this.startFollowUp(rec, "revise") }, `✏️ Change it (version ${(target.version ?? 1) + 1})`) : null,
            h("button.btn-pill", { type: "button", onclick: () => this.startFollowUp(rec, "new") }, "✨ New explainer about this")));
      }
    }
    if (rec.status === "picking" && rec.plan) body.push(...this.planView(rec));
    else if (rec.status === "picking") {
      const q = rec.pick?.question;
      if (!q) body.push(h("p.msg-text.is-muted", null, PLAN_FOLLOWUPS ? "Working out what to change…" : "Finding the part of the page to change…"));
      else {
        body.push(h("p.msg-text", null, q.text),
          h("div.msg-choices", null,
            ...q.options.map((o) => h("button.btn-pill", { type: "button", onclick: () => this.answerPick(rec, { targets: o.targets, pageWide: o.pageWide }) }, o.label)),
            h("button.btn-pill", { type: "button", onclick: () => this.cancelPick(rec) }, "Cancel")));
      }
    }
    if (rec.status === "asking") {
      body.push(h("p.msg-text", null, "What do you want to add to each section?"), extrasQuestion((ex) => this.answerExtras(rec, ex)));
    } else if (EXTRAS_QUESTION && isQuality(rec.request.quality) && rec.request.quality && rec.request.extras && rec.status !== "deciding" && rec.status !== "picking") {
      body.push(h("p.msg-choice", null, extrasSummary(rec.request.extras)));
    }
    if (rec.followUp && rec.status !== "deciding" && !rec.answer) {
      const what = base?.spec ? `“${base.spec.title}”` : "the explainer above";
      body.push(h("p.msg-choice", null, rec.followUp.add
        ? `➕ Version ${rec.version ?? 2}: adding a section to ${what}`
        : rec.followUp.kind === "revise"
        ? `✏️ Version ${rec.version ?? 2}: revising ${what} (${modeName(rec.request.quality ?? "quality-plus") ?? "Interactive"})`
        : "✨ A new explainer that builds on this conversation"));
    }
    if (rec.replaces && rec.status === "generating") {
      const old = this.records.find((r) => r.id === rec.replaces);
      body.push(h("p.msg-choice", null, `↻ A fresh explainer, replacing “${old?.spec?.title ?? old?.title ?? "the old one"}”`));
    }
    if (rec.status === "generating") body.push(h("p.msg-text", null, rec.replaces ? "Building a fresh explainer on the same prompt. The old one is replaced once this is ready." : isPaper ? "Reading your paper and designing an explainer. This usually takes 1–3 minutes." : rec.followUp?.add ? "Adding a section. Everything already on the page stays as it is." : rec.followUp?.kind === "revise" ? "Revising the explainer. This usually takes about a minute." : `Designing your explainer. This usually takes ${howLong(rec.request.quality) ?? "about a minute"}.`), steps);
    if (rec.status === "error") {
      body.push(steps, h("div.msg-error", { role: "alert" }, h("strong", null, "Something went wrong. "), rec.error ?? ""),
        h("button.btn-pill", { type: "button", onclick: () => this.retryFailed(rec) }, "↻ Try again"));
    }
    if (rec.status === "done" && !rec.spec && rec.answer) {
      body.push(h("div.msg-text.msg-answer", { html: mdToHtml(rec.answer) }), h("p.msg-choice", null, "Nothing on the page was changed. Ask for a change if you'd like one."));
    }
    if (rec.status === "done" && rec.spec && rec.reverted) body.push(h("p.msg-choice", null, `↶ Back to version ${rec.reverted}: no AI call, nothing counted`));
    if (rec.status === "done" && rec.spec) {
      const s = rec.spec;
      body.push(
        h("p.msg-text", null, rec.version ? `Version ${rec.version} is ready: ${s.stages.length} interactive steps. Earlier versions still open from their cards above.` : `Your explainer is ready: ${s.stages.length} interactive steps.`),
        h("a.result-card", { href: `#/c/${rec.id}/x`, "data-theme": s.design.theme },
          h("div.result-band", { class: `t-${s.design.theme}`, style: s.design.theme === "custom" && s.design.accent ? `background: ${derivePalette(s.design.accent).light.grad}` : undefined }, h("span", null, s.stages.map((st) => st.icon).join("  "))),
          h("div.result-body", null,
            h("div.result-title", null, s.title, rec.version ? h("span.result-version", null, ` v${rec.version}`) : null),
            h("div.result-sub", null, s.subtitle),
            h("div.result-meta", null, `${s.layout === "pipeline" ? "Step-by-step" : "Article"} · ${s.level} · ${s.design.theme === "custom" ? "custom palette" : `${s.design.theme} theme`}${s.design.mode && s.design.mode !== "auto" ? ` · ${s.design.mode}` : ""}`),
            rec.usage ? h("div.result-cost", null, resultMeta(rec)) : null,
            s.smart ? h("div.result-check", null, smartText(s.smart)) : null,
            s.clarity ? h("div.result-check", null, s.clarity.added + s.clarity.edited
              ? `✓ Clarity-checked: ${[s.clarity.added ? `${s.clarity.added} explanation${s.clarity.added > 1 ? "s" : ""} added` : "", s.clarity.edited ? `${s.clarity.edited} passage${s.clarity.edited > 1 ? "s" : ""} clarified` : ""].filter(Boolean).join(", ")} (see below)`
              : "✓ Clarity-checked: no changes needed") : null,
            s.revision ? h("div.result-check", null, revisionText(s.revision)) : null,
            s.addition ? h("div.result-check", null, additionText(s)) : null,            s.factCheck ? h("div.result-check", null, s.factCheck.fixed ? `✓ Fact-checked: ${s.factCheck.fixed} correction${s.factCheck.fixed > 1 ? "s" : ""} (see below)` : "✓ Fact-checked: no issues found") : null),
          h("span.result-open", null, "Open →")));
      const reports = s.revision?.steps;
      if (reports?.length) {
        const mark = { done: "✓", kept: "○", undone: "↺", skipped: "–" } as const;
        body.push(h("details.msg-warn.msg-steps", null, h("summary", null, `What changed: ${reports.filter((x) => x.status === "done").length} of ${reports.length} steps done`),
          h("ul", null, ...reports.map((x) => h("li", null, `${mark[x.status]} ${plain(x.label)}${x.why ? ` (${plain(x.why)})` : ""}`)))));
      }
      if (s.warnings.length) body.push(h("details.msg-warn", null, h("summary", null, `${s.warnings.length} part(s) were fixed or skipped automatically`), h("ul", null, ...s.warnings.map((w) => h("li", null, plain(w))))));
      // Undo: under the conversation's latest version made by a follow-up, once nothing else is running.
      if (PLAN_FOLLOWUPS && rec.followUp?.kind === "revise" && thread.at(-1) === rec && !thread.some((r) => r.status !== "done" && r.status !== "error")) {
        body.push(h("div.msg-choices", null, h("button.btn-pill", { type: "button", onclick: () => this.undoVersion(rec) }, "↶ Undo this change")));
      }
      // "Add section": only under the conversation's latest explainer, and only for Quality pages.
      if (isQuality(rec.request.quality) && this.followUpBase(this.rootOf(rec)) === rec && !thread.some((r) => r.status !== "done" && r.status !== "error")) body.push(this.addSectionBox(rec));
    }

    return [userMsg, h("div.msg.is-assistant", null, h("div.msg-avatar", { "aria-hidden": "true" }, "✦"), h("div.msg-body", null, ...body))];
  }

  private showExplainer(rec: ExplainerRecord, stage?: string) {
    const spec = rec.spec!;
    if (this.edit?.recId !== rec.id) {
      this.edit = { recId: rec.id, panel: createEditPanel(this.editHost(rec)), undo: [] };
      this.drawer = { open: false, tab: "ask" };
    }
    const panel = this.edit.panel;
    // Lite+ pages have no Ask chatbot; the drawer then holds only Edit chat.
    const chatEl = spec.noAsk ? undefined : createChat({
      spec,
      messages: rec.chat,
      onMessages: (m) => { rec.chat = m; rec.updatedAt = Date.now(); putRecord(rec); },
      send: (req, onText) => chat(req, onText),
    });
    const dispose = renderExplainer(this.view, {
      spec,
      progress: rec.progress,
      stageId: stage,
      href: (sid) => (sid ? `#/c/${rec.id}/x/${encodeURIComponent(sid)}` : `#/c/${rec.id}/x`),
      onProgress: (p) => { rec.progress = p; putRecord(rec); },
      chat: chatEl,
      // Edit chat is hidden for now (planned for version 2); set EDIT_CHAT to true to bring it back.
      ...(EDIT_CHAT ? { edit: panel.el, editing: panel.target() ?? undefined, onEditBlock: (s: number, b: number) => this.openEdit(s, b) } : {}),
      onSectionTool: (i, _kind, button) => this.openSearch(rec, i, button),
      loadFormulaNote: async (i, b, signal) => {
        const st = spec.stages[i];
        const r = await formulaDeeper({ title: spec.title, level: spec.level, stage: st.label, latex: b.latex, caption: b.caption,
          terms: b.terms.map((t) => ({ symbol: t.symbol, meaning: t.meaning })), text: sectionText(spec, i) }, signal);
        // Saved on the page itself, so reopening is free and an export made later includes it.
        spec.formulaNotes = { ...spec.formulaNotes, [formulaKey(b.latex)]: r.note };
        rec.updatedAt = Date.now();
        putRecord(rec);
        return r.note;
      },
      drawer: this.drawer,
      onDrawer: (d) => { this.drawer = d; },
    });
    // Custom mode: show the explainer in the light/dark chosen at creation, then restore the app setting.
    const own = spec.design.mode;
    if (own === "light" || own === "dark") setRootMode(own);
    this.disposeView = () => { this.closeSection?.(); this.closeSection = null; dispose(); if (own === "light" || own === "dark") setRootMode(loadMode()); };
    this.view.scrollTo({ top: 0 });
  }

  // ---------- section tools ----------

  /** A section's "Search the web" button: the side panel, with answers saved on the record. */
  private openSearch(rec: ExplainerRecord, i: number, button: HTMLElement) {
    this.closeSection?.();
    const spec = rec.spec!;
    const st = spec.stages[i];
    const key = `${st.id}:search`;
    this.closeSection = openSearchPanel({
      host: this.view.querySelector<HTMLElement>(".explainer") ?? document.body,
      trigger: button,
      stageLabel: st.label,
      notes: () => rec.sectionNotes?.[key] ?? [],
      run: (previous, signal) => sectionTool({ kind: "search", title: spec.title, level: spec.level, stage: st.label, text: sectionText(spec, i), previous }, signal),
      save: (n) => {
        rec.sectionNotes = { ...rec.sectionNotes, [key]: [...(rec.sectionNotes?.[key] ?? []), n] };
        rec.updatedAt = Date.now();
        putRecord(rec);
      },
    });
  }

  // ---------- Edit chat ----------

  private openEdit(stage: number, block: number) {
    this.edit?.panel.open({ stage, block });
    this.view.querySelector(".explainer")?.dispatchEvent(new CustomEvent("x-drawer", { detail: { open: true, tab: "edit" } }));
  }

  /** Re-render the open explainer after an edit, keeping the scroll position and the drawer. */
  private rerender(rec: ExplainerRecord) {
    const top = this.view.scrollTop;
    this.disposeView?.();
    this.showExplainer(rec, this.parts()[3]);
    this.view.scrollTop = top;
  }

  /** Swap in a new spec: keep slider positions, reset saved answers that no longer line up, save. */
  private setSpec(rec: ExplainerRecord, next: ExplainerSpec, resetStages: string[]) {
    inheritState(rec.spec!, next);
    if (resetStages.length) {
      const answers = Object.fromEntries(Object.entries(rec.progress.answers).filter(([k]) => !resetStages.some((id) => k.startsWith(`${id}:`))));
      rec.progress = { ...rec.progress, answers };
    }
    rec.spec = next;
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.rerender(rec);
  }

  private editHost(rec: ExplainerRecord) {
    const undo = () => this.edit?.recId === rec.id ? this.edit.undo : [];
    const highlight = (t: EditTarget | null) => {
      this.view.querySelectorAll(".x-block.is-editing").forEach((e) => e.classList.remove("is-editing"));
      if (t) this.view.querySelector(`[data-edit="${t.stage}:${t.block}"]`)?.classList.add("is-editing");
    };
    return {
      spec: () => rec.spec!,
      apply: (next: ExplainerSpec, focus: EditTarget | null, structural: boolean) => {
        const prev = rec.spec!;
        undo().push(prev);
        rec.originalSpec ??= prev;
        // Only the edited stage is a new object (checkEdit keeps the others as they were).
        const changed = next.stages.filter((s, i) => s !== prev.stages[i]).map((s) => s.id);
        this.setSpec(rec, next, structural ? changed : []);
        highlight(focus);
      },
      undo: () => {
        const prev = undo().pop();
        if (!prev) return false;
        this.setSpec(rec, prev, prev.stages.map((s) => s.id).filter((id, i) => prev.stages[i] !== rec.spec!.stages[i]));
        return true;
      },
      canUndo: () => undo().length > 0,
      restoreOriginal: () => {
        if (!rec.originalSpec) return false;
        undo().push(rec.spec!);
        const original = rec.originalSpec;
        rec.originalSpec = undefined;
        this.setSpec(rec, original, original.stages.map((s) => s.id));
        return true;
      },
      hasOriginal: () => !!rec.originalSpec,
      value: (name: string) => storeFor(rec.spec!)?.get(name) ?? NaN,
      highlight,
    };
  }

  // ---------- generation ----------

  /** root: the conversation this message continues (absent = the home box: a new conversation). */
  private async submit(s: ComposerSubmit, root?: string) {
    let request: GenerateRequest;
    let title: string;
    if (s.paper) {
      request = { mode: "paper", title: s.paper.title, note: s.text, sections: s.paper.sections, ...optionsOf(s) };
      title = s.paper.title || s.paper.fileName;
    } else {
      request = { mode: "concept", text: s.text, ...optionsOf(s) };
      title = clip(s.text);
    }
    if (!EXTRAS_QUESTION && isQuality(s.quality) && s.quality) request = { ...request, extras: [] };
    // A follow-up continues from the conversation's latest finished explainer.
    const base = root ? this.followUpBase(root) : undefined;
    const now = Date.now();
    const rec: ExplainerRecord = {
      id: uid(), title, createdAt: now, updatedAt: now,
      prompt: { text: s.text, fileName: s.paper?.fileName, pages: s.paper?.pages },
      request, status: base ? "deciding" : isQuality(s.quality) && s.quality && !request.extras ? "asking" : "generating", steps: {}, progress: { visited: [], answers: {} }, chat: [],
      ...(root ? { thread: root } : {}),
    };
    this.records.unshift(rec);
    await putRecord(rec);
    location.hash = `#/c/${rec.id}`;
    if (!base) { if (rec.status === "generating") this.run(rec); return; }
    // A new paper can only be a new explainer; anything else: revise the one above, or a new one?
    if (s.paper) { this.startFollowUp(rec, "new"); return; }
    this.routing.add(rec.id);
    this.refresh(rec.id);
    let kind: RouteResponse["kind"] = "unclear";
    try {
      kind = (await routeFollowUp({ text: s.text, title: base.spec!.title, stages: base.spec!.stages.map((st) => st.label) })).kind;
    } catch (e) {
      console.warn("follow-up routing failed; asking the reader", e);
    }
    this.routing.delete(rec.id);
    if (rec.status !== "deciding") return; // the reader already chose
    if (kind === "unclear") this.refresh(rec.id);
    else this.startFollowUp(rec, kind);
  }

  // ---------- conversations (threads of records) ----------

  /** The id of the conversation a record belongs to (its first record). */
  private rootOf(r: ExplainerRecord): string {
    return r.thread && this.records.some((x) => x.id === r.thread) ? r.thread : r.id;
  }

  /** A conversation's records, oldest first. */
  private threadOf(root: string): ExplainerRecord[] {
    return this.records.filter((r) => this.rootOf(r) === root && !this.replacerOf(r)).sort((a, b) => a.createdAt - b.createdAt);
  }

  /** The retry building a fresh explainer in place of `r` (r is then hidden until the retry ends). */
  private replacerOf(r: ExplainerRecord): ExplainerRecord | undefined {
    return this.records.find((x) => x.replaces === r.id);
  }

  /** The conversation's latest finished explainer (before `before`, if given): what a follow-up builds on. */
  private followUpBase(root: string, before?: ExplainerRecord): ExplainerRecord | undefined {
    return this.threadOf(root).filter((r) => r.status === "done" && r.spec && r !== before && (!before || r.createdAt < before.createdAt)).at(-1);
  }

  /** Start a follow-up as a new version of the explainer above, or as a related new explainer. */
  private startFollowUp(rec: ExplainerRecord, kind: "revise" | "new") {
    const root = this.rootOf(rec);
    const base = this.followUpBase(root, rec);
    if (base?.spec) {
      const earlier = this.threadOf(root).filter((r) => r.status === "done" && r.spec && r.createdAt < rec.createdAt).map((r) => r.spec!);
      let f = buildFollowUp(kind, base.spec, earlier);
      if (!f && kind === "revise") {
        toast("That explainer is too large to revise, so I'll make a new one that builds on it.");
        kind = "new";
        f = buildFollowUp(kind, base.spec, earlier);
      }
      if (f) {
        rec.request = { ...rec.request, followUp: f };
        rec.followUp = { kind, baseId: base.id };
        if (kind === "revise") rec.version = (base.version ?? 1) + 1;
      }
      // A revision keeps the answer the explainer above was made with; a new Quality explainer asks again.
      if (kind === "revise" && base.request.extras) rec.request = { ...rec.request, extras: base.request.extras };
      // A revision is made in the page's own mode (with its review rounds and Thinking), not the one picked in the box now:
      // a Playground page revised with Quick read selected would otherwise lose its playgrounds.
      if (kind === "revise") {
        const r: GenerateRequest = { ...rec.request };
        const b = base.request;
        delete r.reviewRounds;
        delete r.planThinking;
        r.quality = b.quality;
        if (b.reviewRounds) r.reviewRounds = b.reviewRounds;
        if (b.planThinking !== undefined) r.planThinking = b.planThinking;
        rec.request = r;
      }
    }
    if (isQuality(rec.request.quality) && rec.request.quality && !rec.request.extras && kind === "new") {
      rec.status = "asking";
      rec.updatedAt = Date.now();
      putRecord(rec);
      this.refresh(rec.id);
      return;
    }
    const f = rec.request.followUp;
    if (kind === "revise" && f?.kind === "revise" && !f.pick && !f.plan && rec.request.quality && PICK_MODES.includes(rec.request.quality)) {
      this.matchFirst(rec);
      return;
    }
    rec.status = "generating";
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
    this.run(rec);
  }

  /**
   * The "➕ Add section" button under an explainer, opening a small box for what to add. Kept across re-renders
   * (like the dock), so typed text survives progress updates.
   */
  private addSectionBox(base: ExplainerRecord): HTMLElement {
    if (this.addBox?.recId === base.id) return this.addBox.el;
    const input = h("textarea.add-input", { rows: 2, maxlength: LIMITS.conceptChars, placeholder: "What should the new section explain? e.g. “Also explain continuous distributions”" }) as HTMLTextAreaElement;
    const go = h("button.btn-pill.is-primary", { type: "button" }, "Add section") as HTMLButtonElement;
    const form = h("div.add-form", { hidden: true }, input,
      h("p.dock-note", null, "We'll place it where it fits best; everything already on the page stays exactly as it is."),
      h("div.msg-choices", null, go, h("button.btn-pill", { type: "button", onclick: () => { form.hidden = true; open.hidden = false; } }, "Cancel")));
    const open = h("button.btn-pill", { type: "button", onclick: () => { form.hidden = false; open.hidden = true; input.focus(); } }, icon(ICON.plus), "Add section") as HTMLButtonElement;
    const send = () => {
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      this.addBox = undefined;
      this.addSection(base, text);
    };
    go.addEventListener("click", send);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
    const el = h("div.add-section", null, open, form);
    this.addBox = { recId: base.id, el };
    return el;
  }

  /** A new version of `base` with one section added: no routing, no extras question (the base's answer is reused). */
  private async addSection(base: ExplainerRecord, text: string) {
    const f = buildFollowUp("revise", base.spec!, []);
    if (!f || f.kind !== "revise") { toast("That explainer is too large to add to. Start a new conversation instead."); return; }
    const b = base.request;
    const now = Date.now();
    const rec: ExplainerRecord = {
      id: uid(), title: text.length > 60 ? text.slice(0, 57) + "…" : text, createdAt: now, updatedAt: now,
      prompt: { text },
      request: { mode: "concept", text, quality: "quality-plus", effort: b.effort, interactivity: b.interactivity, ...(b.extras ? { extras: b.extras } : {}), followUp: { ...f, add: true } },
      status: "generating", steps: {}, progress: { visited: [], answers: {} }, chat: [],
      thread: this.rootOf(base), followUp: { kind: "revise", baseId: base.id, add: true }, version: (base.version ?? 1) + 1,
    };
    this.records.unshift(rec);
    await putRecord(rec);
    location.hash = `#/c/${rec.id}`;
    this.run(rec);
  }

  /** The Feedback dialog (feedback.ts): the server reads the page from the same revise follow-up the change will run with. */
  private openFeedback(base: ExplainerRecord) {
    const f = base.spec ? buildFollowUp("revise", base.spec, []) : null;
    if (!f || f.kind !== "revise") { toast("That explainer is too large to change from feedback. Start a new conversation instead."); return; }
    // The change the feedback that made this version asked for (none for a first version or an ordinary follow-up).
    const prev = base.request.followUp?.kind === "revise" ? base.request.followUp.feedback : undefined;
    const lastChange = prev && prev.targets.length
      ? { stages: prev.targets.map((t) => t.stage).filter(Boolean), what: prev.targets.map((t) => t.what).join("; ").slice(0, 400) }
      : undefined;
    openFeedbackDialog({ recordId: base.id, spec: base.spec!, quality: base.request.quality, followUp: f, ...(lastChange && lastChange.stages.length ? { lastChange } : {}) },
      (r, text) => this.onFeedback(base, f, r, text));
  }

  /** What the reader confirmed: say what was saved, and run the confirmed change (if any) as a new version. */
  private onFeedback(base: ExplainerRecord, f: Extract<FollowUp, { kind: "revise" }>, r: FeedbackConfirmed, text: string) {
    const saved = r.preference ? `Saved for your future pages: “${r.preference.text}”` : "";
    if (r.feedback && base.spec) {
      toast(saved ? `${saved} Updating this page…` : "Updating this page…");
      this.feedbackFollowUp(base, { ...f, feedback: r.feedback }, text);
    } else {
      toast(saved || "Thanks for the feedback.");
    }
  }

  /**
   * Feedback's confirmed change to `base`, as a revise follow-up carrying FollowUp.feedback: a new version in the same
   * conversation, made with the same options as `base`. Only the confirmed parts change (server/explainer/pipeline.py).
   */
  private async feedbackFollowUp(base: ExplainerRecord, f: FollowUp, text: string) {
    const b = base.request;
    const now = Date.now();
    const title = `Feedback: ${text}`;
    const rec: ExplainerRecord = {
      id: uid(), title: title.length > 60 ? title.slice(0, 57) + "…" : title, createdAt: now, updatedAt: now,
      prompt: { text },
      request: {
        mode: "concept", text, quality: b.quality, effort: b.effort, interactivity: b.interactivity, level: b.level,
        ...(b.planThinking !== undefined ? { planThinking: b.planThinking } : {}), ...(b.colorMode ? { colorMode: b.colorMode } : {}),
        ...(b.extras ? { extras: b.extras } : {}), followUp: f,
      },
      status: "generating", steps: {}, progress: { visited: [], answers: {} }, chat: [],
      thread: this.rootOf(base), followUp: { kind: "revise", baseId: base.id }, version: (base.version ?? 1) + 1,
    };
    this.records.unshift(rec);
    await putRecord(rec);
    location.hash = `#/c/${rec.id}`;
    this.run(rec);
  }

  /**
   * A revise follow-up on a Playground or Quick read page: match the request to the page's sections first
   * (server/explainer/followup_pick.py). Sure -> build with those parts; unclear -> ask the reader (rec.pick); can't
   * tell -> an error, nothing built. If the call itself fails, the page is built anyway and the server's finder decides.
   */
  private async pickFirst(rec: ExplainerRecord) {
    const f = rec.request.followUp;
    if (f?.kind !== "revise" || !rec.request.quality) return;
    rec.status = "picking";
    rec.pick = undefined;
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
    let r: Awaited<ReturnType<typeof pickFollowUp>> | null = null;
    try {
      r = await pickFollowUp({ text: rec.prompt.text, quality: rec.request.quality, followUp: f });
    } catch (e) {
      console.warn("follow-up pick failed; the server's finder decides", e);
    }
    if (rec.status !== "picking") return; // cancelled meanwhile
    if (r && "question" in r) {
      rec.pick = { question: r.question };
      rec.updatedAt = Date.now();
      putRecord(rec);
      this.refresh(rec.id);
      return;
    }
    if (r && "error" in r) {
      rec.status = "error";
      rec.error = r.error;
      rec.updatedAt = Date.now();
      putRecord(rec);
      this.refresh(rec.id);
      return;
    }
    this.answerPick(rec, r ? { targets: r.targets, pageWide: r.pageWide } : null);
  }

  /** A revise follow-up on a Playground or Quick read page: the plan first (follow-ups v2), else the section pick. */
  private matchFirst(rec: ExplainerRecord) {
    if (PLAN_FOLLOWUPS) this.planFirst(rec);
    else this.pickFirst(rec);
  }

  /**
   * Follow-ups v3 (server/explainer/understand.py): what the follow-up asks (sections + intents, structural steps). Small plans build at once; a question, a
   * plan to confirm, or a request the page can't do waits for the reader (rec.plan). A question gets an answer in the
   * thread (no new version). "off" (FOLLOWUP_PLAN not set) or a failed call falls back to the section pick.
   * option (+ extra): the reader chose an option of a question or the alternative to a refusal (and added words to it).
   */
  private async planFirst(rec: ExplainerRecord, option?: PlanOption, extra?: string) {
    const f = rec.request.followUp;
    if (f?.kind !== "revise" || !rec.request.quality) return;
    rec.status = "picking";
    rec.pick = undefined;
    rec.plan = undefined;
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
    let r: PlanResponse | null = null;
    try {
      const history = this.historyOf(rec);
      r = await planFollowUp({ text: rec.prompt.text, quality: rec.request.quality, followUp: f, ...(option ? { option } : {}), ...(extra ? { extra } : {}), ...(history.length ? { history } : {}) });
    } catch (e) {
      // No silent fallback to the old section pick: the reader sees why, and "Try again" asks again (retry → matchFirst).
      if (rec.status !== "picking") return; // cancelled meanwhile
      this.failFollowUp(rec, `Couldn't work out the change (${e instanceof Error ? e.message : String(e)}). Try again.`);
      return;
    }
    if (rec.status !== "picking") return; // cancelled meanwhile
    // Legacy: only when the server has v3 turned off (FOLLOWUP_PLAN=0) does the old section pick decide.
    if (!r || ("kind" in r && r.kind === "off")) { this.pickFirst(rec); return; }
    if ("error" in r) { this.failFollowUp(rec, r.error); return; }
    if (r.kind === "revert") { this.revert(rec); return; }
    if (r.kind === "answer") {
      rec.status = "done";
      rec.answer = r.answer;
    } else if (r.kind === "ask") {
      rec.plan = { kind: "ask", question: r.question };
    } else if (r.kind === "cannot") {
      rec.plan = { kind: "cannot", reason: r.reason, alternative: r.alternative };
    } else if (r.kind === "change") {
      const plan: FollowUpPlan = { sections: r.sections, steps: r.steps };
      if (!r.confirm) { this.runSteps(rec, plan); return; }
      rec.plan = { kind: "confirm", plan, summary: r.summary };
    }
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
  }

  /** What the reader is asked before a plan runs: a question, a plan to confirm, or what the page can't do. */
  private planView(rec: ExplainerRecord): HTMLElement[] {
    const p = rec.plan!;
    const cancel = h("button.btn-pill", { type: "button", onclick: () => this.cancelPick(rec) }, "Cancel");
    if (p.kind === "confirm") {
      // Summary lines follow the plan: steps, then sections. A section part the server wasn't sure the reader asked for
      // (unsure) gets a ticked box: untick it to leave it out. An unsure step only shows its note (knock-on fixes depend on it).
      const ticks = new Map<number, HTMLInputElement>();
      const lines = p.summary.map((line, i) => {
        const part = i < p.plan.steps.length ? null : p.plan.sections[i - p.plan.steps.length];
        if (!part?.unsure) return h("li", null, plain(line));
        const box = h("input", { type: "checkbox", checked: "", "aria-label": `Include: ${line}` }) as HTMLInputElement;
        box.checked = true;
        ticks.set(i - p.plan.steps.length, box);
        return h("li.is-unsure", null, h("label", null, box, " ", plain(line)));
      });
      const go = () => {
        const sections = p.plan.sections.filter((_, k) => ticks.get(k)?.checked !== false);
        if (!sections.length && !p.plan.steps.length) { toast("Tick at least one change, or press Cancel."); return; }
        this.runSteps(rec, { ...p.plan, sections });
      };
      return [h("p.msg-text", null, ticks.size ? "Here's what I'll change. Untick anything you didn't ask for, then go ahead." : "Here's what I'll change. Go ahead?"),
        h("ul.msg-plan", null, ...lines),
        h("div.msg-choices", null, h("button.btn-pill", { type: "button", onclick: go }, "✓ Make these changes"), cancel)];
    }
    if (p.kind === "cannot") {
      const alt = p.alternative;
      return [h("p.msg-text", null, p.reason),
        h("div.msg-choices", null, alt ? h("button.btn-pill", { type: "button", onclick: () => this.planFirst(rec, alt) }, alt.label) : null, cancel)];
    }
    // A question: each option runs as it is; with words in "Add detail", the chosen option is planned again with them.
    const detail = h("input.plan-detail", { type: "text", placeholder: "Add detail to your choice (optional)", maxlength: "300", "aria-label": "Add detail to your choice" });
    const choose = (o: PlanOption) => this.planFirst(rec, o, detail.value.trim() || undefined);
    return [h("p.msg-text", null, p.question.text),
      h("div.msg-choices", null, ...p.question.options.map((o) => h("button.btn-pill", { type: "button", onclick: () => choose(o) }, o.label)), cancel),
      detail];
  }

  /** Build the new version from this plan (FollowUp.plan). */
  private runSteps(rec: ExplainerRecord, plan: FollowUpPlan) {
    const f = rec.request.followUp;
    if (rec.status !== "picking" || f?.kind !== "revise") return;
    rec.request = { ...rec.request, followUp: { ...f, plan } };
    rec.plan = undefined;
    rec.status = "generating";
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
    this.run(rec);
  }

  /** The conversation's earlier prompts with what each did, oldest first (the last 5), for /api/followup/plan. */
  private historyOf(rec: ExplainerRecord): string[] {
    const earlier = this.threadOf(this.rootOf(rec)).filter((r) => r.id !== rec.id && r.createdAt < rec.createdAt);
    return earlier.slice(-5).map((r) => {
      const what = r.answer ? "answered in the chat, nothing changed"
        : r.status === "error" ? "failed, nothing changed"
        : r.reverted ? `undone: back to version ${r.reverted}`
        : r.status === "done" && r.spec ? (r.spec.revision ? `made version ${r.version ?? 1}: ${(r.spec.revision.steps ?? []).filter((x) => x.status === "done").map((x) => x.label).join("; ") || revisionText(r.spec.revision)}` : `made version ${r.version ?? 1}`)
        : "not finished";
      return `"${r.prompt.text.slice(0, 160)}": ${what}`.slice(0, 300);
    });
  }

  private failFollowUp(rec: ExplainerRecord, error: string) {
    rec.status = "error";
    rec.error = error;
    rec.plan = undefined;
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
  }

  /** "Undo": this record becomes a copy of the version before the one it follows. No AI call, not counted. */
  private revert(rec: ExplainerRecord) {
    const base = rec.followUp ? this.records.find((r) => r.id === rec.followUp!.baseId) : undefined;
    const prevId = base?.followUp?.kind === "revise" ? base.followUp.baseId : undefined;
    const prev = prevId ? this.records.find((r) => r.id === prevId) : undefined;
    if (!prev?.spec) { this.failFollowUp(rec, "There's no earlier version of this page to go back to."); return; }
    const { revision: _revision, addition: _addition, ...spec } = structuredClone(prev.spec);
    rec.spec = { ...spec, warnings: [] };
    rec.reverted = prev.version ?? 1;
    rec.title = spec.title;
    rec.plan = undefined;
    rec.steps = {};
    rec.status = "done";
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
  }

  /** The "Undo this change" button: a new version that goes back to the one before `latest`. */
  private undoVersion(latest: ExplainerRecord) {
    const f = latest.spec ? buildFollowUp("revise", latest.spec, []) : null;
    if (!f || f.kind !== "revise") { toast("This version can't be undone."); return; }
    const text = "Undo the last change";
    const now = Date.now();
    const rec: ExplainerRecord = {
      id: uid(), title: text, createdAt: now, updatedAt: now, prompt: { text },
      request: { ...latest.request, mode: "concept", text, followUp: f } as GenerateRequest,
      status: "picking", steps: {}, progress: { visited: [], answers: {} }, chat: [],
      thread: this.rootOf(latest), followUp: { kind: "revise", baseId: latest.id }, version: (latest.version ?? 1) + 1,
    };
    this.records.unshift(rec);
    this.revert(rec);
    location.hash = `#/c/${rec.id}`;
  }

  /** Build the revision with the parts picked (or chosen by the reader); null = let the server's finder decide. */
  private answerPick(rec: ExplainerRecord, pick: FollowUpPick | null) {
    const f = rec.request.followUp;
    if (rec.status !== "picking" || f?.kind !== "revise") return;
    if (pick) rec.request = { ...rec.request, followUp: { ...f, pick } };
    rec.pick = undefined;
    rec.status = "generating";
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
    this.run(rec);
  }

  private cancelPick(rec: ExplainerRecord) {
    if (rec.status !== "picking") return;
    rec.pick = undefined;
    rec.plan = undefined;
    rec.status = "error";
    rec.error = "Cancelled: nothing on the page was changed. Type what you'd like to change, or press Try again.";
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
  }

  /** The answer to "What do you want to add to each section?": plan the explainer with it. */
  private answerExtras(rec: ExplainerRecord, extras: Extra[]) {
    if (rec.status !== "asking") return;
    rec.request = { ...rec.request, extras };
    rec.status = "generating";
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
    this.run(rec);
  }

  /** The icon row under a prompt: ↻ Retry, Copy, ✏️ Edit (Retry and Edit only when `canRedo`). */
  private promptActions(rec: ExplainerRecord, canRedo: boolean): HTMLElement | null {
    const text = rec.prompt.text;
    const act = (svg: string, label: string, onclick: (b: HTMLButtonElement) => void) => {
      const b = h("button.msg-act", { type: "button", title: label, "aria-label": label }, icon(svg)) as HTMLButtonElement;
      b.addEventListener("click", () => onclick(b));
      return b;
    };
    const items: HTMLElement[] = [];
    if (canRedo) {
      items.push(act(ICON.retry, rec.status === "error" ? "Try again" : "Retry: a fresh explainer on the same prompt", () => {
        if (rec.status === "error") { this.retryFailed(rec); return; } // nothing to lose: run it again in place
        this.openPromptBox(rec, "retry");
      }));
    }
    if (text) {
      items.push(act(ICON.copy, "Copy prompt", (b) => {
        const done = () => {
          b.replaceChildren(icon(ICON.check));
          b.title = "Copied";
          setTimeout(() => { b.replaceChildren(icon(ICON.copy)); b.title = "Copy prompt"; }, 1500);
        };
        if (copyNow(text)) done();
        else if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => toast("Couldn't copy the prompt."));
        else toast("Couldn't copy the prompt.");
      }));
    }
    if (canRedo) items.push(act(ICON.pencil, "Edit prompt", () => this.openPromptBox(rec, "edit")));
    return items.length ? h("div.msg-actions", null, ...items) : null;
  }

  /** Opens the Edit box or the Retry confirm under a prompt (one at a time). */
  private openPromptBox(rec: ExplainerRecord, kind: "edit" | "retry") {
    const close = () => { this.promptBox = undefined; this.refresh(rec.id); };
    let el: HTMLElement;
    if (kind === "retry") {
      const go = h("button.btn-pill.is-primary", { type: "button" }, "Retry") as HTMLButtonElement;
      go.addEventListener("click", () => { go.disabled = true; this.promptBox = undefined; this.retryFresh(rec); });
      el = h("div.retry-confirm", null,
        h("p.dock-note", null, `This builds a fresh explainer on the same prompt ${this.retryWith(rec)} and replaces this one. The old one can't be recovered.`),
        h("div.msg-choices", null, go, h("button.btn-pill", { type: "button", onclick: close }, "Cancel")));
    } else {
      const paper = rec.request.mode === "paper";
      const input = h("textarea.add-input", { rows: 3, maxlength: LIMITS.conceptChars, placeholder: paper ? "Anything to focus on? (optional)" : "Describe an AI concept" }) as HTMLTextAreaElement;
      input.value = rec.prompt.text;
      const send = h("button.btn-pill.is-primary", { type: "button" }, "Send") as HTMLButtonElement;
      const go = () => {
        const t = input.value.trim();
        if (!t && !paper) { input.focus(); return; }
        send.disabled = true;
        this.promptBox = undefined;
        this.editPrompt(rec, t);
      };
      send.addEventListener("click", go);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); go(); }
        else if (e.key === "Escape") close();
      });
      el = h("div.add-form.prompt-edit", null, input,
        h("p.dock-note", null, (rec.status === "error" ? "Runs again with your edited prompt" : "Builds a fresh explainer from your edited prompt and replaces this one (the old one can't be recovered)")
          + (rec.request.followUp?.kind === "revise" ? ", in this page's own mode." : ", using the mode and options selected below.")),
        h("div.msg-choices", null, send, h("button.btn-pill", { type: "button", onclick: close }, "Cancel")));
      queueMicrotask(() => { input.focus(); input.setSelectionRange(input.value.length, input.value.length); });
    }
    this.promptBox = { recId: rec.id, kind, el };
    this.refresh(rec.id);
  }

  /** What a Retry of `rec` will use, for its confirm: "with Explorer · medium effort · thinking on" or "in this page's own mode". */
  private retryWith(rec: ExplainerRecord): string {
    const { request: r } = this.withCurrentOptions(rec.request);
    if (r.followUp?.kind === "revise") return "in this page's own mode";
    const parts = [modeName(r.quality) ?? r.quality ?? "the same mode"];
    if (r.effort) parts.push(`${r.effort} effort`);
    if (r.quality === "playground") parts.push(`thinking ${r.planThinking ? "on" : "off"}`);
    return `with ${parts.join(" · ")}`;
  }

  /** ✏️ Edit: the same request with the edited prompt. A failed message runs again in place; a finished one is replaced like Retry. */
  private editPrompt(rec: ExplainerRecord, text: string) {
    if (rec.status === "error") {
      const { request, ask } = this.editedRequest(rec.request, text);
      rec.prompt = { ...rec.prompt, text };
      rec.request = request;
      if (!rec.thread && rec.request.mode === "concept") rec.title = clip(text);
      this.rerun(rec, ask);
      return;
    }
    this.retryFresh(rec, text);
  }

  /** ↻ Retry / Try again on a failed message: runs again in place, with the composer's current mode and options. */
  private retryFailed(rec: ExplainerRecord) {
    const { request, ask } = this.withCurrentOptions(rec.request);
    rec.request = request;
    this.rerun(rec, ask);
  }

  /** A failed message with its new request: asks what to add to each section first if the mode needs it, else runs again. */
  private rerun(rec: ExplainerRecord, ask: boolean) {
    if (ask) { // the mode changed to one that asks what to add to each section, like a new prompt
      rec.status = "asking";
      rec.error = undefined;
      rec.steps = {};
      rec.updatedAt = Date.now();
      putRecord(rec);
      this.refresh(rec.id);
      return;
    }
    this.retry(rec); // a revise follow-up on Explorer / Reader matches the text to sections first
  }

  /**
   * An edited prompt behaves like a newly sent one: it uses the composer's current mode and options (v1 defaults for the
   * hidden ones), not the old explainer's. A revise follow-up keeps its page's own mode, as typed follow-ups do. The answer
   * to "what to add to each section" is kept when the mode is the same; a mode that asks it, newly chosen, asks again.
   */
  private editedRequest(req: GenerateRequest, text: string): { request: GenerateRequest; ask: boolean } {
    return this.withCurrentOptions(withPromptText(req, text));
  }

  /**
   * `req` with the composer's current mode and options (v1 defaults for the hidden ones), as a newly sent prompt would
   * have. A revise follow-up keeps its page's own mode (and its plan). Used by ✏️ Edit and ↻ Retry.
   */
  private withCurrentOptions(req: GenerateRequest): { request: GenerateRequest; ask: boolean } {
    const choices = this.dock?.composer.choices?.();
    if (!choices || req.followUp?.kind === "revise") return { request: req, ask: false };
    const { quality, reviewRounds: _r, effort: _e, interactivity: _i, level: _l, planThinking: _t, colorMode: _c, extras, ...rest } = req;
    const opts = optionsOf(choices);
    const keep = !!extras && quality === opts.quality;
    const none = !keep && !EXTRAS_QUESTION && isQuality(opts.quality); // question hidden (v1): no extras
    return { request: { ...rest, ...opts, ...(keep ? { extras } : none ? { extras: [] } : {}) } as GenerateRequest, ask: isQuality(opts.quality) && !keep && !none };
  }

  /**
   * A fresh run of `old`'s prompt, from scratch: nothing about `old` is sent, and it uses the composer's current mode and
   * options (a revise follow-up keeps its page's mode and plan). (Sending its section titles to avoid made each retry
   * shorter, as the list grew with every retry.)
   * It is a new record; `old` stays hidden until it ends (settleRetry). `text`: an edited prompt (✏️ Edit).
   */
  private async retryFresh(old: ExplainerRecord, text?: string) {
    if (!old.spec || this.replacerOf(old)) return;
    const { avoid: _old, ...b } = old.request; // also drops an `avoid` saved by an earlier version of Retry
    let request: GenerateRequest = { ...b };
    // A version made from Feedback is redone as an ordinary revision of the same request (its confirmed run was used up).
    if (request.followUp?.kind === "revise" && request.followUp.feedback) {
      const { feedback: _used, ...f } = request.followUp;
      request.followUp = f;
    }
    // An edited prompt also gets the new text; a revise follow-up's section pick is then dropped, as it was made for the
    // old text, so the server's finder decides again. A plain Retry keeps the prompt (and a revise follow-up's plan).
    let ask = false;
    ({ request, ask } = text !== undefined ? this.editedRequest(request, text) : this.withCurrentOptions(request));
    const now = Date.now();
    const rec: ExplainerRecord = {
      id: uid(), title: text !== undefined && !old.thread && request.mode === "concept" ? clip(text) : old.title, createdAt: now, updatedAt: now,
      prompt: { ...old.prompt, ...(text !== undefined ? { text } : {}) }, request,
      status: ask ? "asking" : "generating", steps: {}, progress: { visited: [], answers: {} }, chat: [],
      ...(old.thread ? { thread: old.thread } : {}), ...(old.followUp ? { followUp: old.followUp } : {}), ...(old.version ? { version: old.version } : {}),
      replaces: old.id,
    };
    if (this.addBox?.recId === old.id) this.addBox = undefined;
    this.records.unshift(rec);
    await putRecord(rec);
    location.hash = `#/c/${rec.id}`;
    if (!ask) this.run(rec); // asking: runs once the reader answers (answerExtras)
  }

  /** A retry ended: done deletes the explainer it replaced; failed deletes the retry, and the old explainer comes back. */
  private async settleRetry(rec: ExplainerRecord) {
    const oldId = rec.replaces;
    if (!oldId || (rec.status !== "done" && rec.status !== "error")) return; // still building, or waiting on a question
    if (rec.status === "done") {
      try {
        await deleteRecord(oldId);
      } catch (e) {
        // Still hidden; the next load tries again.
        toast(e instanceof Error ? `Couldn't remove the old explainer: ${e.message}` : "Couldn't remove the old explainer.");
        return;
      }
      this.records = this.records.filter((r) => r.id !== oldId);
      delete rec.replaces;
      rec.updatedAt = Date.now();
      putRecord(rec);
      this.refresh(rec.id);
      return;
    }
    const why = rec.error;
    try { await deleteRecord(rec.id); } catch { /* a leftover is settled again on the next load */ }
    this.records = this.records.filter((r) => r.id !== rec.id);
    toast(`Retry failed${why ? `: ${why}` : ""}. Your explainer is unchanged.`);
    if (this.currentId() === rec.id) location.hash = this.records.some((r) => r.id === oldId) ? `#/c/${oldId}` : "#/";
    else this.drawSidebar();
  }

  private retry(rec: ExplainerRecord) {
    const f = rec.request.followUp;
    if (f?.kind === "revise" && !f.pick && !f.plan && !f.feedback && !f.add && rec.request.quality && PICK_MODES.includes(rec.request.quality)) {
      rec.error = undefined;
      rec.steps = {};
      this.matchFirst(rec); // match the request to the page again (it may ask the reader)
      return;
    }
    rec.status = "generating";
    rec.error = undefined;
    rec.steps = {};
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
    this.run(rec);
  }

  private async run(rec: ExplainerRecord) {
    const ctrl = new AbortController();
    this.running.set(rec.id, ctrl);
    try {
      // The server saves this generation into the record, so the record must be on the server first.
      await flushRecord(rec.id).catch(() => undefined);
      await generateExplainer(rec.request, (e) => {
        if (e.type === "step") rec.steps[e.step] = { status: e.status, detail: e.detail };
        else if (e.type === "done") {
          rec.spec = e.spec;
          rec.usage = e.usage;
          rec.status = "done";
          rec.title = e.spec.title;
        } else if (e.type === "error" && e.message.startsWith(CHECKED_PREFIX)) {
          // Follow-ups v3: the sections were checked and found right. A reply, not an error or a new version.
          rec.status = "done";
          rec.answer = e.message.slice(CHECKED_PREFIX.length);
        } else if (e.type === "error") {
          rec.status = "error";
          rec.error = e.message;
        }
        rec.updatedAt = Date.now();
        // Progress stays local (the server records it itself); the result is saved to the account.
        putRecord(rec, { remote: e.type !== "step" });
        this.refresh(rec.id);
      }, ctrl.signal, rec.id);
      this.running.delete(rec.id);
      if (rec.replaces) await this.settleRetry(rec);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      if (e instanceof ConnectionLost) {
        // The server carries on; follow the record there instead.
        this.running.delete(rec.id);
        this.pollGenerating();
        return;
      }
      rec.status = "error";
      rec.error = e instanceof Error ? e.message : String(e);
      putRecord(rec);
      this.refresh(rec.id);
      this.running.delete(rec.id);
      if (rec.replaces) await this.settleRetry(rec);
    } finally {
      this.running.delete(rec.id);
      refreshAllowance().then(() => this.drawSidebar());
    }
    // When done, the conversation shows the result card; the reader opens the explainer from it.
  }

  /** Re-render the conversation if it's on screen, and the sidebar. */
  private refresh(id: string) {
    const [kind, cid, x] = this.parts();
    const shown = kind === "c" && x !== "x" ? this.records.find((r) => r.id === cid) : undefined;
    const rec = this.records.find((r) => r.id === id);
    if (shown && rec && this.rootOf(shown) === this.rootOf(rec)) {
      // Progress updates redraw the conversation without moving the reader's scroll position.
      const top = this.view.scrollTop;
      this.showConversation(shown);
      this.view.scrollTop = top;
      this.drawTopbar();
    }
    this.drawSidebar();
  }
}

/** A revision made as changes: "✏️ 2 sections added · 7 kept as they were (only the changed ones were checked)". */
function revisionText(r: NonNullable<ExplainerSpec["revision"]>): string {
  const n = (k: number, what: string) => (k ? `${k} section${k === 1 ? "" : "s"} ${what}` : "");
  const charts = r.charts ? `${r.charts} interactive part${r.charts === 1 ? "" : "s"} changed` : "";
  const changes = [n(r.added, "added"), n(r.replaced, "rewritten"), n(r.removed, "removed"), charts].filter(Boolean).join(" · ") || "Page details updated";
  return `✏️ ${changes} · ${r.kept} kept as they were${r.added + r.replaced ? " (only the changed ones were checked)" : ""}`;
}

function additionText(s: ExplainerSpec): string {
  const a = s.addition!;
  const where = a.placed === "jev" ? `➕ Placed ${a.after} (${Math.round(a.p * 100)}% sure)` : `➕ Put ${a.after}: no clear best place for it`;
  const covered = a.covered ? s.stages.find((st) => st.id === a.covered) : undefined;
  return covered ? `${where} · “${covered.label}” may already cover part of it` : where;
}

/** Smart mode's picks: "✦ Smart: 2 quick reads · 3 interactive · 1 custom simulation". */
function smartText(m: NonNullable<ExplainerSpec["smart"]>): string {
  const parts = [
    m.quick ? `${m.quick} quick read${m.quick > 1 ? "s" : ""}` : "",
    m.interactive ? `${m.interactive} interactive` : "",
    m.creative ? `${m.creative} creative` : "",
    m.custom ? `${m.custom} custom simulation${m.custom > 1 ? "s" : ""}` : "",
  ].filter(Boolean);
  return `✦ Smart: ${parts.join(" · ") || "written as Interactive"}`;
}

/** Review rounds (server/explainer/deepen.py): Interactive with rounds goes as quality-deep; other modes keep their own mode. */
/** The request options for the composer's choices (mode, review rounds, effort, level, thinking, page colours). */
function optionsOf(s: ComposerChoices): Pick<GenerateRequest, "quality" | "reviewRounds" | "effort" | "interactivity" | "level" | "planThinking" | "colorMode"> {
  // Code Lab 2.0 to 7.0 and Explorer always say whether to think before planning (the Options' Thinking switch).
  const thinking = s.quality === "code-lab-2" || s.quality === "code-lab-3" || s.quality === "code-lab-7" || s.quality === "playground" || s.quality === "playground-2" ? { planThinking: s.planThinking } : {};
  return { ...modeOf(s), effort: s.effort, interactivity: s.interactivity, level: s.level, ...thinking, colorMode: s.colorMode };
}

function modeOf(s: ComposerChoices): Pick<GenerateRequest, "quality" | "reviewRounds"> {
  if (s.reviewRounds <= 0) return { quality: s.quality };
  return { quality: s.quality === "quality-plus" ? "quality-deep" : s.quality, reviewRounds: s.reviewRounds };
}

/** Copies `text` right away, inside the click (navigator.clipboard can wait on a permission prompt). False if the browser refused. */
function copyNow(text: string): boolean {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  return ok;
}

/** A typed prompt as a conversation title. */
function clip(text: string): string {
  return text.length > 60 ? text.slice(0, 57) + "…" : text;
}

/** `req` with an edited prompt (a concept's text, a paper's note). A revise follow-up drops its section pick, made for the old text. */
function withPromptText(req: GenerateRequest, text: string): GenerateRequest {
  const out: GenerateRequest = req.mode === "concept" ? { ...req, text } : { ...req, note: text };
  if (out.followUp?.kind === "revise" && (out.followUp.pick || out.followUp.plan)) {
    const { pick: _old, plan: _oldPlan, ...f } = out.followUp;
    out.followUp = f;
  }
  return out;
}

/** The result card's settings line: "Explorer · low effort · hands-on · 47s" (plus "≈ $0.006" when SHOW_COST). */
function resultMeta(rec: ExplainerRecord): string {
  const u = rec.usage!;
  const parts = [modeName(u.quality) ?? u.quality];
  if (u.quality === "quality-deep" || rec.request.reviewRounds) {
    const n = rec.request.reviewRounds;
    parts.push(!n ? "review rounds" : n >= 10 ? "review until clean" : `up to ${n} review round${n === 1 ? "" : "s"}`);
  }
  if (u.effort === "low" || u.effort === "high") parts.push(`${u.effort} effort`);
  if (rec.request.interactivity && rec.request.interactivity !== "balanced") parts.push(rec.request.interactivity === "reading" ? "mostly reading" : "hands-on");
  if (rec.request.level && rec.request.level !== "auto") parts.push(LEVEL_CHOICES[rec.request.level].split(":")[0].toLowerCase());
  if (SHOW_COST) parts.push(`≈ $${u.totalUsd.toFixed(3)}`);
  parts.push(`${u.seconds}s`);
  return parts.join(" · ");
}

/** Server progress notes and warnings name Jev, the internal checker; readers see plain words instead. */
function plain(text: string): string {
  return text
    .replace(/^Quality 2\.0: /, "Review: ")
    .replace(/\s*\(Jev\)/g, "")
    .replace(/\bJev:\s*/g, "")
    .replace(/\bJev is checking\b/g, "Checking")
    .replace(/\bJev skipped\b/g, "Skipped")
    .replace(/\bJev flagged (\d+)/g, "$1 flagged")
    .replace(/\bJev thinks\b/g, "It looks like")
    .replace(/\bJev wasn't sure\b/g, "We weren't sure")
    .replace(/\bno Jev key\b/g, "the checker isn't set up")
    .replace(/\bJev\b/g, "the checker");
}

function toast(msg: string) {
  const t = h("div.toast.shell", { role: "status" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 4000);
}
