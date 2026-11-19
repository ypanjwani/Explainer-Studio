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
import { ANSWER_PREFIX, ASKED_PREFIX, CHECKED_PREFIX, LIMITS, NEW_TOPIC_BRIEF_PREFIX, NEW_TOPIC_PREFIX, OFFER_PREFIX, PLAN_MODES, formulaKey, isQuality, type ExplainerSpec, type GenerateRequest, type FollowUpPlan, type PlanOption, type PlanResponse, type ProgressStep } from "../../../shared/spec";
import { chat, ConnectionLost, formulaDeeper, generateExplainer, planFollowUp, revertVersion, sectionTool } from "../api";
import { allowance, onAuthChange, refreshAllowance, restore, type User } from "../auth";
import { createChat } from "../render/chatbot";
import { h, mdToHtml, uid } from "../render/dom";
import { renderExplainer, type DrawerState } from "../render/explainer";
import { exportHtml } from "../render/export";
import { LINK_ICON, openShareDialog } from "./share";
import { closeHistory, deleteRecord, fetchRecord, flushAll, flushRecord, getRecord, importLegacy, legacyRecords, openHistory, putRecord, type ExplainerRecord } from "../store/history";
import { allowanceMeter, authView, legacyOffer, openAccountPanel } from "./account";
import { createComposer, type ComposerChoices, type ComposerEl, type ComposerSubmit } from "./composer";
import { LEVEL_CHOICES } from "../../../shared/menus";
import { openSearchPanel, sectionText } from "./section";
import { applyMode, isDark, loadMode, modeToggle, setRootMode } from "./mode";
import { howLong, modeName } from "./modes";
import { currentStep, justUnlocked, lockedCard, reading } from "./progress";
import { extrasQuestion, extrasSummary } from "./extras";
import type { Extra } from "../../../shared/menus";

/** "What do you want to add to each section?" (extras.ts). Hidden for v1: new explainers get no extras ("None"); the code stays. */
const EXTRAS_QUESTION = false;
/**
 * The AI cost on the result card ("≈ $0.006"), hidden from readers for v1 (Nov 2026). The data is still saved.
 */
const SHOW_COST = false;
/** Records whose "Show steps" fold the reader opened; kept across the redraws every progress event makes. */
const STEPS_OPEN = new Set<string>();
/** Earlier turns sent with a follow-up's plan call; keep equal to MAX_HISTORY in server/explainer/modes/followup/shared.py. */
const HISTORY_TURNS = 12;

const SUGGESTIONS = [
  { label: "Attention in transformers", text: "Explain self-attention in transformers: queries, keys, values and why it works." },
  { label: "LoRA fine-tuning", text: "Explain LoRA (low-rank adaptation) and why it makes fine-tuning cheap." },
  { label: "Diffusion models", text: "Explain how diffusion models generate images by learning to remove noise." },
  { label: "Temperature & top-p", text: "Explain temperature, top-k and top-p sampling in language models." },
];

const STEP_LABEL: Record<ProgressStep, string> = {
  intake: "Reading your message",
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
  /** The explainer the drawer state belongs to (a different explainer starts with the drawer closed). */
  private drawerRec: string | null = null;
  /** The Ask drawer, kept open across re-renders of the same explainer. */
  private drawer: DrawerState = { open: false };
  /** Closes the open section tool panel, if any. */
  private closeSection: (() => void) | null = null;
  /** The conversation's follow-up box, kept across re-renders so typed text survives progress updates. */
  private dock: { root: string; el: HTMLElement; composer: ComposerEl } | null = null;
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
    this.drawerRec = null;
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
    // A follow-up left waiting by the old router (status "deciding", removed Nov 13 2026), or interrupted while the plan step
    // read it: a failed message whose Try again runs it through the plan step (rerun). Nothing is called by itself on load;
    // a saved question or plan card stays.
    for (const r of this.records.filter((r) => r.status === "deciding" || (r.status === "picking" && !r.plan))) {
      r.error = r.status === "deciding" ? "This message wasn't sent. Press Try again." : "This message was interrupted. Press Try again.";
      r.status = "error";
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
      : thread.some((x) => x.status === "asking" || (x.status === "picking" && x.plan)) ? h("span.sb-ask", { title: "Waiting for your answer" }) : null;
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
    if (rec.status === "picking" && rec.plan) body.push(...this.planView(rec));
    else if (rec.status === "picking") body.push(h("p.msg-text.is-muted", null, "Reading your message…"));
    if (rec.status === "asking") {
      body.push(h("p.msg-text", null, "What do you want to add to each section?"), extrasQuestion((ex) => this.answerExtras(rec, ex)));
    } else if (EXTRAS_QUESTION && isQuality(rec.request.quality) && rec.request.quality && rec.request.extras && rec.status !== "picking") {
      body.push(h("p.msg-choice", null, extrasSummary(rec.request.extras)));
    }
    if (rec.followUp && !rec.answer && !(rec.status === "picking" && rec.plan?.kind === "cannot" && rec.plan.newTopic)) {
      const what = base?.spec ? `“${base.spec.title}”` : "the explainer above";
      body.push(h("p.msg-choice", null, rec.followUp.kind === "revise"
        ? `✏️ ${rec.version ? `Version ${rec.version}: revising ${what}` : `Revising version ${base?.version ?? 1}: ${what}`} (${modeName(rec.request.quality ?? "quality-plus") ?? "Interactive"})`
        : "✨ A new explainer that builds on this conversation"));
    }
    if (rec.replaces && rec.status === "generating") {
      const old = this.records.find((r) => r.id === rec.replaces);
      body.push(h("p.msg-choice", null, `↻ A fresh explainer, replacing “${old?.spec?.title ?? old?.title ?? "the old one"}”`));
    }
    // Building: the card appears at once, locked, with a progress ring; the current step is named above it and the
    // full list of steps is folded under it (progress.ts).
    const stepsFold = h("details.gen-steps", {
      open: STEPS_OPEN.has(rec.id),
      ontoggle: (e: Event) => (e.currentTarget as HTMLDetailsElement).open ? STEPS_OPEN.add(rec.id) : STEPS_OPEN.delete(rec.id),
    }, h("summary", null, "Show steps"), steps);
    if (rec.status === "generating") {
      const cur = currentStep(rec);
      body.push(
        h("p.gen-now", { "aria-live": "polite" }, h("span.gen-dot", { "aria-hidden": "true" }),
          h("strong", null, cur ? `${STEP_LABEL[cur[0]] ?? "Working"}…` : "Starting…"),
          cur?.[1].detail ? h("span.step-detail", null, plain(cur[1].detail)) : null),
        h("p.gen-hint", null, reading(rec) ? "Reading your message first: it's answered here, or built into an explainer." : rec.replaces ? "Building a fresh explainer on the same prompt. The old one is replaced once this is ready." : isPaper ? "Reading your paper and designing an explainer. This usually takes 1–3 minutes." : rec.followUp?.kind === "revise" ? "Revising the explainer. This usually takes about a minute." : `Designing your explainer. This usually takes ${howLong(rec.request.quality) ?? "about a minute"}.`),
        lockedCard(rec), stepsFold);
    }
    if (rec.status === "error") {
      body.push(...(Object.keys(rec.steps).length ? [lockedCard(rec, { failed: true }), stepsFold] : []),
        h("div.msg-error", { role: "alert" }, h("strong", null, "Something went wrong. "), rec.error ?? ""),
        h("button.btn-pill", { type: "button", onclick: () => this.retryFailed(rec) }, "↻ Try again"));
    }
    if (rec.status === "done" && !rec.spec && rec.answer) {
      body.push(h("div.msg-text.msg-answer", { html: mdToHtml(rec.answer) }));
      // A follow-up's answer leaves its page as it was; the first-message step's answer (FIRST_TALK) has no page to change.
      if (rec.followUp?.kind === "revise") body.push(h("p.msg-choice", null, "Nothing on the page was changed. Ask for a change if you'd like one."));
      // The first-message step offered a page (a brief): one click builds it. Only under the conversation's last message.
      if (rec.offer && thread.at(-1) === rec && !thread.some((r) => r.status !== "done" && r.status !== "error")) {
        body.push(h("div.msg-choices", null, h("button.btn-pill", { type: "button", onclick: () => this.buildOffer(rec) }, "✨ Make an explainer about this")));
      }
      // Follow-ups v5: the answer lists fixes. One click says yes to all of them (sent as a picked option, so it is always
      // read as a change; typed "fix them" could be read as a question again). Only under the conversation's last message.
      if (rec.offersFix && thread.at(-1) === rec && !thread.some((r) => r.status !== "done" && r.status !== "error")) {
        body.push(h("div.msg-choices", null, h("button.btn-pill", { type: "button", onclick: () => this.acceptFixes(rec) }, "✓ Yes, make these fixes")));
      }
    }
    if (rec.status === "done" && rec.spec && rec.reverted) body.push(h("p.msg-choice", null, `↶ Back to version ${rec.reverted}: no AI call, nothing counted`));
    if (rec.status === "done" && rec.spec && rec.madeFrom && !rec.reverted) body.push(h("p.msg-choice", null, `Made from version ${rec.madeFrom}`));
    if (rec.status === "done" && rec.spec) {
      const s = rec.spec;
      // Follow-ups v4: the friendly reply, written from what really changed.
      if (s.revision?.reply) body.push(h("div.msg-text.msg-answer", { html: mdToHtml(s.revision.reply) }));
      body.push(
        h("p.msg-text", null, rec.version ? `Version ${rec.version} is ready: ${s.stages.length} interactive steps. Earlier versions still open from their cards above.` : `Your explainer is ready: ${s.stages.length} interactive steps.`),
        h("a.result-card", { href: `#/c/${rec.id}/x`, "data-theme": s.design.theme, class: justUnlocked(rec.id) ? "is-unlocked" : undefined },
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
      // Follow-ups with a reply (v4, v5) show no change list: the reply says what changed. The steps stay in the record
      // (one per tool call, so the same section repeated). The chips were removed Nov 19 2026: they looked noisy.
      const reports = s.revision?.steps;
      if (reports?.length && !s.revision?.reply) {
        const mark = { done: "✓", kept: "○", undone: "↺", skipped: "–" } as const;
        body.push(h("details.msg-warn.msg-steps", null, h("summary", null, `What changed: ${reports.filter((x) => x.status === "done").length} of ${reports.length} steps done`),
          h("ul", null, ...reports.map((x) => h("li", null, `${mark[x.status]} ${plain(x.label)}${x.why ? ` (${plain(x.why)})` : ""}`)))));
      }
      if (s.warnings.length) body.push(h("details.msg-warn", null, h("summary", null, `${s.warnings.length} part(s) were fixed or skipped automatically`), h("ul", null, ...s.warnings.map((w) => h("li", null, plain(w))))));
      // Undo: under the conversation's latest version made by a follow-up, once nothing else is running.
      if (rec.followUp?.kind === "revise" && thread.at(-1) === rec && !thread.some((r) => r.status !== "done" && r.status !== "error")) {
        body.push(h("div.msg-choices", null, h("button.btn-pill", { type: "button", onclick: () => this.undoVersion(rec) }, "↶ Undo this change")));
      }
    }

    return [userMsg, h("div.msg.is-assistant", null, h("div.msg-avatar", { "aria-hidden": "true" }, "✦"), h("div.msg-body", null, ...body))];
  }

  private showExplainer(rec: ExplainerRecord, stage?: string) {
    const spec = rec.spec!;
    if (this.drawerRec !== rec.id) {
      this.drawerRec = rec.id;
      this.drawer = { open: false };
    }
    // Reader pages have no Ask chatbot, so no drawer.
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

  // ---------- generation ----------

  /** root: the conversation this message continues (absent = the home box: a new conversation). */
  /** "✓ Yes, make these fixes" under a v5 answer that listed fixes: the next message, sent as a picked option, so the plan
   * step reads it as a change without asking Jev, and the agent gets the answer's fix list as the turn before. */
  private acceptFixes(rec: ExplainerRecord) {
    const choices = this.dock?.composer.choices?.();
    if (!choices) return;
    const text = "Yes, make the fixes you listed.";
    this.submit({ text, ...choices }, this.rootOf(rec), { label: text, sections: [], steps: [] });
  }

  /** "✨ Make an explainer about this" under a first-message answer (FIRST_TALK): builds the offered brief at once (direct:
   * no first-message step), with the composer's current mode and options; a paper conversation keeps its paper. */
  private async buildOffer(old: ExplainerRecord) {
    const offer = old.offer;
    if (!offer) return;
    const { history: _h, direct: _d, avoid: _a, ...r } = old.request;
    const { request, ask } = this.withCurrentOptions(withPromptText(r as GenerateRequest, offer));
    const now = Date.now();
    const rec: ExplainerRecord = {
      id: uid(), title: clip(offer), createdAt: now, updatedAt: now,
      prompt: { text: offer, ...(request.mode === "paper" ? { fileName: old.prompt.fileName, pages: old.prompt.pages } : {}) },
      request: { ...request, direct: true }, status: ask ? "asking" : "generating", steps: {}, progress: { visited: [], answers: {} }, chat: [],
      thread: this.rootOf(old), ...(old.followUp?.kind === "new" ? { followUp: old.followUp } : {}),
    };
    this.records.unshift(rec);
    await putRecord(rec);
    location.hash = `#/c/${rec.id}`;
    if (!ask) this.run(rec);
  }

  /** option: the message is a picked option (acceptFixes), passed on to the plan step. */
  private async submit(s: ComposerSubmit, root?: string, option?: PlanOption) {
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
    // A conversation with no page yet (only answers so far): a typed message keeps talking about the paper uploaded earlier.
    const paper = root && !base && !s.paper ? this.threadOf(root).filter((r) => r.request.mode === "paper").at(-1)?.request : undefined;
    if (paper?.mode === "paper") request = { mode: "paper", title: paper.title, note: s.text, sections: paper.sections, ...optionsOf(s), ...(request.extras ? { extras: request.extras } : {}) };
    const now = Date.now();
    const rec: ExplainerRecord = {
      id: uid(), title, createdAt: now, updatedAt: now,
      prompt: { text: s.text, fileName: s.paper?.fileName, pages: s.paper?.pages },
      request, status: base ? "picking" : isQuality(s.quality) && s.quality && !request.extras ? "asking" : "generating", steps: {}, progress: { visited: [], answers: {} }, chat: [],
      ...(root ? { thread: root } : {}),
    };
    this.records.unshift(rec);
    // No page yet: the first-message step (FIRST_TALK) reads the conversation so far, so "ok, make it" builds what was discussed.
    if (root && !base) {
      const history = this.historyOf(rec);
      if (history.length) rec.request = { ...rec.request, history };
    }
    await putRecord(rec);
    location.hash = `#/c/${rec.id}`;
    if (!base) { if (rec.status === "generating") this.run(rec); return; }
    this.beginFollowUp(rec, base, option);
  }

  /**
   * A message under a finished explainer (`base`). There is no router any more (removed Nov 13 2026): the plan step
   * (/api/followup/plan) reads every typed follow-up and answers, asks, refuses, undoes, changes, or says it is a new topic
   * (then the reader gets a "New explainer about this" button, newFromPick). A new paper, or a page made with a retired
   * mode (it can't be changed), can only be a new explainer.
   */
  private beginFollowUp(rec: ExplainerRecord, base: ExplainerRecord, option?: PlanOption) {
    const changeable = !!base.request.quality && PLAN_MODES.includes(base.request.quality);
    this.startFollowUp(rec, rec.request.mode === "paper" || !changeable ? "new" : "revise", option);
  }

  /** "✨ New explainer about this" under a reply that said the message is about a different topic: a new explainer that
   * builds on the conversation, made with the composer's current mode and options (not the page's, which the plan step used). */
  private newFromPick(rec: ExplainerRecord) {
    if (rec.status !== "picking") return;
    const { followUp: _f, ...r } = rec.request;
    // The reader chose to build (direct: no first-message step); with v5's brief, the brief is what to build.
    const brief = rec.plan?.kind === "cannot" ? rec.plan.brief : undefined;
    rec.followUp = undefined;
    rec.version = undefined;
    const request = this.withCurrentOptions(r).request;
    rec.request = { ...(brief ? withPromptText(request, brief) : request), direct: true };
    rec.plan = undefined;
    this.startFollowUp(rec, "new");
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

  /** Start a follow-up as a new version of the explainer above, or as a related new explainer. option: a picked option
   * sent with a revise's plan step (acceptFixes). */
  private startFollowUp(rec: ExplainerRecord, kind: "revise" | "new", option?: PlanOption) {
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
        rec.version = undefined; // the server numbers the version when it's saved (done event)
      }
      // A revision keeps the answer the explainer above was made with; a new Quality explainer asks again.
      if (kind === "revise" && base.request.extras) rec.request = { ...rec.request, extras: base.request.extras };
      // A revision is made in the page's own mode (with its Thinking), not the one picked in the box now:
      // a Playground page revised with Quick read selected would otherwise lose its playgrounds.
      if (kind === "revise") {
        const r: GenerateRequest = { ...rec.request };
        const b = base.request;
        delete r.planThinking;
        r.quality = b.quality;
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
    if (kind === "revise" && f?.kind === "revise" && !f.plan && rec.request.quality && PLAN_MODES.includes(rec.request.quality)) {
      this.matchFirst(rec, option);
      return;
    }
    rec.status = "generating";
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
    this.run(rec);
  }

  /** A revise follow-up on a Reader or Explorer page: what it asks first (follow-ups v3, /api/followup/plan). */
  private matchFirst(rec: ExplainerRecord, option?: PlanOption) {
    this.planFirst(rec, option);
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
    rec.plan = undefined;
    rec.updatedAt = Date.now();
    putRecord(rec);
    this.refresh(rec.id);
    let r: PlanResponse | null = null;
    try {
      const history = this.historyOf(rec);
      r = await planFollowUp({ text: rec.prompt.text, quality: rec.request.quality, followUp: f, ...(option ? { option } : {}), ...(extra ? { extra } : {}), ...(history.length ? { history } : {}), ...(rec.followUp?.baseId ? { baseId: rec.followUp.baseId } : {}) });
    } catch (e) {
      // No silent fallback to the old section pick: the reader sees why, and "Try again" asks again (retry → matchFirst).
      if (rec.status !== "picking") return; // cancelled meanwhile
      this.failFollowUp(rec, `Couldn't work out the change (${e instanceof Error ? e.message : String(e)}). Try again.`);
      return;
    }
    if (rec.status !== "picking") return; // cancelled meanwhile
    if ("error" in r) { this.failFollowUp(rec, r.error); return; }
    if (r.kind === "revert") { this.revert(rec, r.to); return; }
    if (r.kind === "answer") {
      rec.status = "done";
      rec.answer = r.answer;
    } else if (r.kind === "ask") {
      rec.plan = { kind: "ask", question: r.question };
    } else if (r.kind === "cannot") {
      rec.plan = { kind: "cannot", reason: r.reason, alternative: r.alternative, ...(r.newTopic ? { newTopic: true } : {}) };
    } else if (r.kind === "change") {
      const plan: FollowUpPlan = { sections: r.sections, steps: r.steps, ...(r.v4 ? { v4: r.v4 } : {}), ...(r.v5 ? { v5: r.v5 } : {}) };
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
      // A different topic: offer a new explainer (a click, so a wrong guess never spends one of the day's explainers).
      const fresh = p.newTopic ? h("button.btn-pill", { type: "button", onclick: () => this.newFromPick(rec) }, "✨ New explainer about this") : null;
      return [h("p.msg-text", null, p.reason),
        h("div.msg-choices", null, fresh, alt ? h("button.btn-pill", { type: "button", onclick: () => this.planFirst(rec, alt) }, alt.label) : null, cancel)];
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

  /**
   * The conversation's earlier prompts with what each did, oldest first (the last HISTORY_TURNS), for /api/followup/plan.
   * Sent in full (Nov 13 2026): the prompt, a whole answer, a change's whole reply plus every step (kept or undone, with why).
   * Cutting answers to 240 characters lost what "fix that" pointed at. The server trims older turns and caps the total.
   */
  private historyOf(rec: ExplainerRecord): string[] {
    const earlier = this.threadOf(this.rootOf(rec)).filter((r) => r.id !== rec.id && r.createdAt < rec.createdAt);
    return earlier.slice(-HISTORY_TURNS).map((r) => {
      const rev = r.status === "done" ? r.spec?.revision : undefined;
      const steps = (rev?.steps ?? []).map((x) => `${x.label} (${x.status}${x.why ? `: ${x.why}` : ""})`).join("; ");
      const what = r.answer ? `answered in the chat, nothing changed: "${r.answer.replace(/\s+/g, " ").trim()}"${r.offer ? ` Offered an explainer: "${r.offer}"` : ""}`
        : r.status === "error" ? "failed, nothing changed"
        : r.reverted ? `undone: back to version ${r.reverted}`
        : rev ? `made version ${r.version ?? 1}. Reply: "${(rev.reply ?? revisionText(rev)).replace(/\s+/g, " ").trim()}"${steps ? ` Steps: ${steps}` : ""}`
        : r.status === "done" && r.spec ? `made version ${r.version ?? 1}`
        : "not finished";
      return `"${r.prompt.text}": ${what}`;
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

  /** "Undo": this record becomes a copy of the version before the one it follows, saved by the server as a new
   *  version (versions.py). No AI call, not counted. */
  private async revert(rec: ExplainerRecord, to?: number) {
    const baseId = rec.followUp?.baseId;
    if (!baseId) { this.failFollowUp(rec, "There's no earlier version of this page to go back to."); return; }
    let r: Awaited<ReturnType<typeof revertVersion>>;
    try {
      await flushRecord(baseId).catch(() => undefined);
      r = await revertVersion(rec.id, baseId, to);
    } catch (e) {
      this.failFollowUp(rec, e instanceof Error ? e.message : "There's no earlier version of this page to go back to.");
      return;
    }
    rec.spec = r.spec;
    rec.version = r.version;
    rec.reverted = r.reverted;
    rec.title = r.spec.title;
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
      thread: this.rootOf(latest), followUp: { kind: "revise", baseId: latest.id },
    };
    this.records.unshift(rec);
    this.revert(rec);
    location.hash = `#/c/${rec.id}`;
  }

  private cancelPick(rec: ExplainerRecord) {
    if (rec.status !== "picking") return;
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
    // A message under an explainer that never got its follow-up started (an old router's "deciding" one): through the plan
    // step now, like a newly sent follow-up.
    const base = rec.thread && !rec.followUp ? this.followUpBase(this.rootOf(rec), rec) : undefined;
    if (base) {
      rec.error = undefined;
      rec.steps = {};
      rec.status = "picking";
      this.beginFollowUp(rec, base);
      return;
    }
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
    const { quality, effort: _e, interactivity: _i, level: _l, planThinking: _t, colorMode: _c, extras, ...rest } = req;
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
    // An edited prompt also gets the new text; a revise follow-up's section pick is then dropped, as it was made for the
    // old text, so the server's finder decides again. A plain Retry keeps the prompt (and a revise follow-up's plan).
    let { request, ask } = text !== undefined ? this.editedRequest(b, text) : this.withCurrentOptions(b);
    if (text === undefined && request.followUp?.kind !== "revise") {
      // A plain Retry of a page builds again without the first-message step (FIRST_TALK), from the brief the page was built
      // from (the step's `detail`), not from words like "ok, make it". An edited prompt is read again, like a new one.
      const brief = old.steps.intake?.detail;
      request = { ...(brief ? withPromptText(request, brief) : request), direct: true };
    }
    const now = Date.now();
    const rec: ExplainerRecord = {
      id: uid(), title: text !== undefined && !old.thread && request.mode === "concept" ? clip(text) : old.title, createdAt: now, updatedAt: now,
      prompt: { ...old.prompt, ...(text !== undefined ? { text } : {}) }, request,
      status: ask ? "asking" : "generating", steps: {}, progress: { visited: [], answers: {} }, chat: [],
      ...(old.thread ? { thread: old.thread } : {}), ...(old.followUp ? { followUp: old.followUp } : {}), ...(old.version ? { version: old.version } : {}),
      replaces: old.id,
    };
    this.records.unshift(rec);
    await putRecord(rec);
    location.hash = `#/c/${rec.id}`;
    if (!ask) this.run(rec); // asking: runs once the reader answers (answerExtras)
  }

  /** A retry ended: done deletes the explainer it replaced; failed deletes the retry, and the old explainer comes back. */
  private async settleRetry(rec: ExplainerRecord) {
    const oldId = rec.replaces;
    if (!oldId || (rec.status !== "done" && rec.status !== "error")) return; // still building, or waiting on a question
    if (rec.status === "done" && !rec.spec) {
      // An edited prompt that was answered, not built (FIRST_TALK): nothing replaces the page, so it comes back and the
      // answer stays after it.
      delete rec.replaces;
      rec.updatedAt = Date.now();
      putRecord(rec);
      this.refresh(rec.id);
      return;
    }
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
    if (f?.kind === "revise" && !f.plan && rec.request.quality && PLAN_MODES.includes(rec.request.quality)) {
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
        if (e.type === "step") {
          // at: when the step started, for the progress ring (the server stores its own; this keeps a live tab's).
          const old = rec.steps[e.step];
          rec.steps[e.step] = { status: e.status, detail: e.detail, at: old?.at ?? Date.now(), preview: e.preview ?? old?.preview };
        }
        else if (e.type === "done") {
          rec.spec = e.spec;
          rec.usage = e.usage;
          if (e.version && e.version > 1) rec.version = e.version; // a page's first version shows no number
          if (e.madeFrom) rec.madeFrom = e.madeFrom;
          rec.status = "done";
          rec.title = e.spec.title;
        } else if (e.type === "error" && e.message.startsWith(ASKED_PREFIX) && askedOf(e.message)) {
          // Follow-ups v4: the editor asks the reader before changing anything. Shown like the plan step's question.
          rec.status = "picking";
          rec.plan = { kind: "ask", question: askedOf(e.message)! };
        } else if (e.type === "error" && jsonAfter(e.message, ANSWER_PREFIX)) {
          // The first-message step (FIRST_TALK) answered: a reply, no page, not counted; offer: a brief to build with one click.
          const a = jsonAfter(e.message, ANSWER_PREFIX)!;
          rec.status = "done";
          rec.answer = a.reply;
          if (typeof a.offer === "string" && a.offer) rec.offer = a.offer;
        } else if (e.type === "error" && jsonAfter(e.message, NEW_TOPIC_BRIEF_PREFIX)) {
          // Follow-ups v5's new topic with a brief: the button builds from the brief.
          const a = jsonAfter(e.message, NEW_TOPIC_BRIEF_PREFIX)!;
          rec.status = "picking";
          rec.plan = { kind: "cannot", reason: a.reply, alternative: null, newTopic: true, ...(typeof a.brief === "string" && a.brief ? { brief: a.brief } : {}) };
        } else if (e.type === "error" && e.message.startsWith(NEW_TOPIC_PREFIX)) {
          // Follow-ups v5: the agent found a different topic. Shown like the plan step's new-topic reply (a button, never automatic).
          rec.status = "picking";
          rec.plan = { kind: "cannot", reason: e.message.slice(NEW_TOPIC_PREFIX.length), alternative: null, newTopic: true };
        } else if (e.type === "error" && e.message.startsWith(OFFER_PREFIX)) {
          // Follow-ups v5: an answer that lists fixes, shown with a "Yes, make these fixes" button.
          rec.status = "done";
          rec.answer = e.message.slice(OFFER_PREFIX.length);
          rec.offersFix = true;
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
      }, ctrl.signal, rec.id, { baseId: rec.followUp?.kind === "revise" ? rec.followUp.baseId : undefined, replaces: rec.replaces });
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

/** The request options for the composer's choices (mode, effort, level, thinking, page colours). */
function optionsOf(s: ComposerChoices): Pick<GenerateRequest, "quality" | "effort" | "interactivity" | "level" | "planThinking" | "colorMode"> {
  // Explorer always says whether to think before planning (the Options' Thinking switch).
  const thinking = s.quality === "playground" ? { planThinking: s.planThinking } : {};
  return { quality: s.quality, effort: s.effort, interactivity: s.interactivity, level: s.level, ...thinking, colorMode: s.colorMode };
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

/** The JSON object after `prefix` in a generation's end message (ANSWER_PREFIX, NEW_TOPIC_BRIEF_PREFIX), with a reply. */
function jsonAfter(message: string, prefix: string): { reply: string; offer?: unknown; brief?: unknown } | null {
  if (!message.startsWith(prefix)) return null;
  try {
    const o = JSON.parse(message.slice(prefix.length));
    return o && typeof o === "object" && typeof o.reply === "string" ? o : null;
  } catch {
    return null;
  }
}

/** A typed prompt as a conversation title. */
function clip(text: string): string {
  return text.length > 60 ? text.slice(0, 57) + "…" : text;
}

/** `req` with an edited prompt (a concept's text, a paper's note). A revise follow-up drops its section pick, made for the old text. */
function withPromptText(req: GenerateRequest, text: string): GenerateRequest {
  const out: GenerateRequest = req.mode === "concept" ? { ...req, text } : { ...req, note: text };
  if (out.followUp?.kind === "revise" && out.followUp.plan) {
    const { plan: _oldPlan, ...f } = out.followUp;
    out.followUp = f;
  }
  return out;
}

/** The result card's settings line: "Explorer · low effort · hands-on · 47s" (plus "≈ $0.006" when SHOW_COST). */
function resultMeta(rec: ExplainerRecord): string {
  const u = rec.usage!;
  const parts = [modeName(u.quality) ?? u.quality];
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

/** Follow-ups v4: the question in an Asked message (ASKED_PREFIX + {text, question: {text, options: [label]}}), as the plan
 * step's ask; undefined when it can't be read. Keep in step with history._asked on the server. */
function askedOf(message: string): { text: string; options: PlanOption[] } | undefined {
  try {
    const q = JSON.parse(message.slice(ASKED_PREFIX.length)) as { question?: { text?: unknown; options?: unknown } };
    const options = Array.isArray(q.question?.options) ? q.question!.options.filter((o): o is string => typeof o === "string" && !!o.trim()) : [];
    if (typeof q.question?.text !== "string" || options.length < 2) return undefined;
    return { text: q.question.text, options: options.map((label) => ({ label, sections: [], steps: [] })) };
  } catch {
    return undefined;
  }
}
