// "Share link": makes (or updates) a public, read-only link to one explainer (server: routes/share.py) and shows
// it in a dialog with Copy, Open and Stop sharing. Anyone with the link can view the page without signing in;
// sharing the same explainer again updates the page behind the same link.

import { apiJSON } from "../auth";
import { h } from "../render/dom";
import { flushRecord } from "../store/history";
import "../styles/share.css";

interface ShareInfo { id: string; path: string; createdAt: number; updatedAt: number }

export const LINK_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg>';

/** Copies right away, inside the click (navigator.clipboard can wait on a permission prompt). */
function copyText(input: HTMLInputElement): boolean {
  input.focus();
  input.select();
  try { if (document.execCommand("copy")) return true; } catch { /* fall through */ }
  navigator.clipboard?.writeText(input.value).catch(() => undefined);
  return !!navigator.clipboard;
}

/** mode: the light/dark mode on screen, used only when the page has none of its own (as Export does). */
export async function openShareDialog(recordId: string, mode: "light" | "dark"): Promise<void> {
  const dialog = h("dialog.account.share.shell", { "aria-labelledby": "share-title" }) as HTMLDialogElement;
  const close = () => { dialog.close(); dialog.remove(); };
  const status = h("p.share-status", { role: "status" }, "Making your link…");
  const body = h("div.share-body");
  dialog.append(
    h("div.account-head", null,
      h("h2", { id: "share-title" }, "Share link"),
      h("button.icon-btn", { type: "button", "aria-label": "Close", onclick: close }, "✕")),
    status,
    body);
  dialog.addEventListener("close", () => dialog.remove());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) close(); });
  document.body.append(dialog);
  dialog.showModal();

  let info: ShareInfo;
  try {
    await flushRecord(recordId).catch(() => undefined); // the server shares the saved copy, so save the latest first
    info = await apiJSON<ShareInfo>("/api/share", "POST", { recordId, mode });
  } catch (e) {
    status.textContent = e instanceof Error ? e.message : "Couldn't make the link.";
    status.classList.add("is-error");
    return;
  }

  const url = new URL(info.path, location.origin).href;
  const input = h("input.auth-input.share-url", { type: "text", readonly: true, value: url, "aria-label": "Share link" }) as HTMLInputElement;
  input.addEventListener("focus", () => input.select());
  const copyBtn = h("button.btn-pill.is-primary", { type: "button" }, "Copy") as HTMLButtonElement;
  copyBtn.addEventListener("click", () => {
    copyBtn.textContent = copyText(input) ? "Copied ✓" : "Select and copy";
    setTimeout(() => { copyBtn.textContent = "Copy"; }, 1800);
  });
  const stopBtn = h("button.btn-pill.is-danger", { type: "button" }, "Stop sharing") as HTMLButtonElement;
  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    try {
      await apiJSON(`/api/share/${encodeURIComponent(info.id)}`, "DELETE");
      status.textContent = "Sharing stopped. The link doesn't work anymore.";
      body.replaceChildren();
    } catch (e) {
      stopBtn.disabled = false;
      status.textContent = e instanceof Error ? e.message : "Couldn't stop sharing.";
      status.classList.add("is-error");
    }
  });

  status.textContent = info.createdAt === info.updatedAt ? "Your link is ready." : "Link updated to this version.";
  body.replaceChildren(
    h("div.share-row", null, input, copyBtn),
    h("p.account-warn", null, "Anyone with this link can view this explainer, without signing in. After you change it, share again to update the page behind the same link."),
    h("div.account-actions", null,
      h("a.btn-pill", { href: url, target: "_blank", rel: "noopener" }, "Open"),
      stopBtn));
  input.focus();
}
