// Accounts in the app shell: the sign-in screen ("Continue with Google", the only way in), the account panel
// (today's free allowance, sign out, delete account) and the offer to import pre-account history.

import { allowance, currentUser, deleteAccount, logout, logoutEverywhere, refreshAllowance, type Allowance } from "../auth";
import { deletePreference, listPreferences, setPreferenceStatus, type Preference } from "../api";
import { h } from "../render/dom";
import { temperatureDemo } from "./authDemo";
import { modeButton } from "./mode";

function field(label: string, input: HTMLInputElement, control: HTMLElement = input): { el: HTMLElement; error: (msg: string | null) => void } {
  const id = `f-${Math.random().toString(36).slice(2, 8)}`;
  input.id = id;
  const err = h("div.auth-field-error", { id: `${id}-err`, role: "alert" });
  input.setAttribute("aria-describedby", `${id}-err`);
  return {
    el: h("div.auth-field", null, h("label", { for: id }, label), control, err),
    error: (msg) => { err.textContent = msg ?? ""; input.setAttribute("aria-invalid", String(!!msg)); },
  };
}

const CHECK = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';

const GOOGLE_G = '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>';

/** What the server's /?auth_error=<code> means (routes/auth.py google_callback). */
const AUTH_ERRORS: Record<string, string> = {
  cancelled: "Google sign-in was cancelled. Try again when you're ready.",
  state: "That sign-in link expired or came from another tab. Please try again.",
  not_verified: "Google hasn't verified this account's email address, so we can't use it. Try another Google account.",
  network_limit: "Too many new accounts were made from this network today. Please try again tomorrow.",
  rate_limited: "Too many sign-in attempts from this network. Please wait a while and try again.",
  inactive: "This Google account can't be used to sign in here.",
  unavailable: "Google sign-in isn't available right now. Please try again later.",
  google: "Google sign-in didn't work. Please try again.",
};

/** The error the Google sign-in sent back in the address, once: it is removed from the address at once. */
function takeAuthError(): string | null {
  const params = new URLSearchParams(location.search);
  const code = params.get("auth_error");
  if (code === null) return null;
  params.delete("auth_error");
  const rest = params.toString();
  history.replaceState(history.state, "", location.pathname + (rest ? `?${rest}` : "") + location.hash);
  return AUTH_ERRORS[code] ?? AUTH_ERRORS.google;
}

/**
 * The signed-out screen: a brand panel with a live example, and "Continue with Google", the only way to sign in
 * or make an account (password sign-in was removed Nov 5 2026; old password accounts sign in with Google under
 * the same email). The trip to Google and back reloads the page, which then restores the session.
 */
export function authView(): HTMLElement {
  const formError = h("div.auth-error", { role: "alert" });
  const googleError = takeAuthError();
  if (googleError) formError.textContent = googleError;

  const google = h("a.auth-submit.auth-google", { href: "/api/auth/google/start" },
    h("span.auth-google-g", { html: GOOGLE_G }), h("span", null, "Continue with Google")) as HTMLAnchorElement;
  // A full-page trip to Google and back; the button shows it is working meanwhile.
  google.addEventListener("click", () => {
    google.classList.add("is-busy");
    google.replaceChildren(h("span.auth-spinner", { "aria-hidden": "true" }), h("span", null, "Opening Google…"));
  });

  queueMicrotask(() => google.focus());

  const head = h("div.auth-head.is-in", null,
    h("h1.auth-title", null, "Welcome to ClariTale"),
    h("p.auth-sub", null, "Sign in or create your free account to make explainers and keep them in your history."));
  const logo = () => h("div.brand-logo", null, h("span.brand-mark", { "aria-hidden": "true" }, "✦"), "ClariTale");
  const perk = (t: string) => h("span", null, h("span.icon", { "aria-hidden": "true", html: CHECK }), t);
  return h("div.auth-page.shell", null,
    h("aside.auth-brand", null,
      logo(),
      h("div.auth-pitch", null,
        h("h2", null, "Don't just read about AI.", h("br"), h("em", null, "Play"), " with it."),
        h("p", null, "Type a concept or drop in a research paper. Get an interactive page with sliders, live charts and quizzes that make the idea click.")),
      temperatureDemo(),
      h("div.auth-perks", null, perk("Free: 10 explainers a day"), perk("No card needed"), perk("History saved to your account"))),
    h("main.auth-side", null,
      h("div.auth-top", null, modeButton()),
      h("div.auth-center", null,
        h("div.auth-box", null,
          h("div.auth-mobile-logo", null, logo()),
          head,
          h("div.auth-form", null, formError, google,
            h("p.auth-hint.auth-google-note", null, "New here? Continuing with Google creates your account.")))),
      h("p.auth-legal", null, "AI topics only · Pages are made by AI and checked by code, but can still be wrong. · ",
        h("a", { href: "/privacy.html", target: "_blank", rel: "noopener" }, "Privacy"))));
}

/** "3 of 10 explainers today" with a small bar. */
export function allowanceMeter(a: Allowance | null): HTMLElement | null {
  if (!a) return null;
  const pct = a.generationsLimit ? Math.min(100, (100 * a.generationsUsed) / a.generationsLimit) : 100;
  const out = a.generationsUsed >= a.generationsLimit || a.usdUsed >= a.usdLimit;
  return h("div.allow", { class: out ? "is-out" : "" },
    h("div.allow-label", null, out ? "Today's free allowance is used up" : `${a.generationsUsed} of ${a.generationsLimit} free explainers today`),
    h("div.allow-bar", { role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(a.generationsLimit), "aria-valuenow": String(a.generationsUsed), "aria-label": "Explainers made today" },
      h("span", { style: `width: ${pct}%` })));
}

/**
 * "My preferences": what the reader saved through Feedback. Active ones shape their future pages; each can be
 * turned off (kept for later) or deleted. Loaded when the section is opened.
 */
function preferencesSection(): HTMLElement {
  const list = h("div.pref-list", null, h("p.account-warn", null, "Loading…"));
  const draw = (prefs: Preference[]) => {
    if (!prefs.length) {
      list.replaceChildren(h("p.account-warn", null, "No saved preferences yet. Use Feedback on a page and tick “Remember this”."));
      return;
    }
    list.replaceChildren(...prefs.map((p) => {
      const row = h("div.pref-row", { class: p.status === "off" ? "is-off" : "" },
        h("span.pref-text", null, p.text),
        h("button.btn-pill", { type: "button", onclick: () => act(() => setPreferenceStatus(p.id, p.status === "active" ? "off" : "active")) }, p.status === "active" ? "Turn off" : "Turn on"),
        h("button.btn-pill.is-danger", { type: "button", "aria-label": `Delete preference: ${p.text}`, onclick: () => act(() => deletePreference(p.id)) }, "Delete"));
      return row;
    }));
  };
  const load = () => listPreferences().then(draw).catch((err) => {
    list.replaceChildren(h("p.account-warn", null, err instanceof Error ? err.message : String(err)));
  });
  const act = async (run: () => Promise<unknown>) => {
    try { await run(); } catch (err) { alertIn(list, err); return; }
    await load();
  };
  const section = h("details.account-section", null,
    h("summary", null, "My preferences"),
    h("p.account-warn", null, "Saved from your feedback. The ones that are on are used for your future pages only."),
    list) as HTMLDetailsElement;
  section.addEventListener("toggle", () => { if (section.open) load(); });
  return section;
}

function alertIn(list: HTMLElement, err: unknown) {
  list.prepend(h("p.account-warn.pref-error", { role: "alert" }, err instanceof Error ? err.message : String(err)));
}

/** The account panel: allowance, preferences, sign out (here or everywhere), delete the account. */
export async function openAccountPanel(): Promise<void> {
  const user = currentUser();
  if (!user) return;
  const dialog = h("dialog.account.shell", { "aria-labelledby": "account-title" }) as HTMLDialogElement;
  const close = () => { dialog.close(); dialog.remove(); };
  const status = h("p.account-status", { role: "status" });
  const meter = h("div", null, allowanceMeter(allowance()));
  refreshAllowance().then((a) => meter.replaceChildren(allowanceMeter(a) ?? ""));

  // Delete account: confirmed by typing the account's email (the server checks it).
  const delEmail = h("input.auth-input", { type: "email", autocomplete: "off", maxlength: 320, placeholder: user.email }) as HTMLInputElement;
  const delF = field("Type your email to confirm", delEmail);
  const delForm = h("form.account-form.is-danger", { novalidate: true }, delF.el,
    h("button.btn-pill.is-danger", { type: "submit" }, "Delete my account and history")) as HTMLFormElement;
  delForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    delF.error(null);
    if (delEmail.value.trim().toLowerCase() !== user.email) { delF.error("Type this account's email."); return; }
    try {
      await deleteAccount(delEmail.value);
      close();
    } catch (err) {
      delF.error(err instanceof Error ? err.message : String(err));
    }
  });

  dialog.append(
    h("div.account-head", null,
      h("h2", { id: "account-title" }, "Account"),
      h("button.icon-btn", { type: "button", "aria-label": "Close", onclick: close }, "✕")),
    h("p.account-email", null, user.email, h("span.account-plan", null, "Free plan")),
    meter,
    status,
    preferencesSection(),
    h("div.account-actions", null,
      h("button.btn-pill", { type: "button", onclick: async () => { await logout(); close(); } }, "Sign out"),
      h("button.btn-pill", { type: "button", onclick: async () => { await logoutEverywhere().catch(() => undefined); close(); } }, "Sign out on all devices")),
    h("details.account-section", null, h("summary", null, "Delete account"),
      h("p.account-warn", null, "This permanently deletes your account and every explainer in it. It can't be undone."), delForm));
  dialog.addEventListener("close", () => dialog.remove());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) close(); });
  document.body.append(dialog);
  dialog.showModal();
}

/** Offer to move history saved in this browser (before accounts) into the account. */
export function legacyOffer(count: number, onImport: (progress: (n: number) => void) => Promise<number>, onDismiss: () => void): HTMLElement {
  const text = h("span", null, `Found ${count} explainer${count > 1 ? "s" : ""} saved in this browser before accounts. Add ${count > 1 ? "them" : "it"} to your account?`);
  const add = h("button.btn-pill", { type: "button" }, "Add to my account") as HTMLButtonElement;
  const no = h("button.btn-pill", { type: "button" }, "Not now") as HTMLButtonElement;
  const el = h("div.legacy-offer.shell", { role: "region", "aria-label": "Import earlier explainers" }, text, h("div.legacy-actions", null, add, no));
  add.addEventListener("click", async () => {
    add.disabled = no.disabled = true;
    const n = await onImport((k) => { text.textContent = `Adding… ${k} of ${count}`; });
    text.textContent = n === count ? `Added ${n} explainer${n > 1 ? "s" : ""} to your account.` : `Added ${n} of ${count}. The rest will be offered again next time.`;
    no.disabled = false;
    no.textContent = "Close";
    add.remove();
  });
  no.addEventListener("click", () => { el.remove(); onDismiss(); });
  return el;
}
