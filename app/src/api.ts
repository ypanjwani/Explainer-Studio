// Calls to our server (never to Anthropic directly; the key lives server-side). Every call is signed in:
// authFetch adds the access token and refreshes it when it runs out.

import type { ChatRequest, ExplainerSpec, FormulaRequest, FormulaResponse, GenerateEvent, GenerateRequest, PlanRequest, PlanResponse, SectionRequest, SectionResponse } from "../../shared/spec";
import { apiJSON, authFetch, errorOf } from "./auth";

/** POST JSON and read a text/event-stream response, calling onData per event. */
async function postSSE(path: string, body: unknown, onData: (data: any) => void, signal?: AbortSignal) {
  const res = await authFetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!res.ok || !res.body) throw await errorOf(res);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      for (const line of chunk.split("\n")) if (line.startsWith("data: ")) onData(JSON.parse(line.slice(6)));
    }
  }
}

/** recordId: the history record to save progress and the result to (on the server, so it lands even if the tab closes). */
/** The stream broke after the server accepted the generation: it carries on there, so the record can be polled. */
export class ConnectionLost extends Error {
  constructor() { super("The connection closed before the explainer was finished."); }
}

/** recordId: the history record to save progress and the result to (on the server, so it lands even if the tab closes).
 *  ids.baseId: the record a revise follow-up changes; ids.replaces: the record a Retry or Edit replaces. The server
 *  uses them to number the version it saves (the number comes back in the done event). */
export async function generateExplainer(req: GenerateRequest, onEvent: (e: GenerateEvent) => void, signal?: AbortSignal, recordId?: string,
  ids: { baseId?: string; replaces?: string } = {}) {
  let started = false;
  let finished = false;
  const extra = Object.fromEntries(Object.entries(ids).filter(([, v]) => v));
  try {
    await postSSE("/api/generate", recordId ? { ...req, ...extra, recordId } : req, (e: GenerateEvent) => {
      started = true;
      if (e.type === "done" || e.type === "error") finished = true;
      onEvent(e);
    }, signal);
  } catch (e) {
    if ((e as Error).name === "AbortError" || !started) throw e;
    throw new ConnectionLost();
  }
  if (!finished) {
    if (recordId) throw new ConnectionLost();
    onEvent({ type: "error", message: "The connection closed before the explainer was finished. Please try again." });
  }
}

export async function chat(req: ChatRequest, onText: (t: string) => void, signal?: AbortSignal) {
  let error: string | null = null;
  await postSSE("/api/chat", req, (d: { t?: string; error?: string }) => {
    if (d.t) onText(d.t);
    if (d.error) error = d.error;
  }, signal);
  if (error) throw new Error(error);
}

const postJSON = <T>(path: string, body: unknown, signal?: AbortSignal) => apiJSON<T>(path, "POST", body, signal);

/** Undo / "go back to version N": the server saves version `to` (default: the one before baseId's) as a new version of
 *  baseId's page for record `recordId`. No AI call, not counted. */
export const revertVersion = (recordId: string, baseId: string, to?: number) =>
  postJSON<{ spec: ExplainerSpec; version: number; reverted: number }>(`/api/explainers/${encodeURIComponent(recordId)}/revert`, to ? { baseId, to } : { baseId });

/** A follow-up in a conversation: does it revise the explainer above, or ask for a new one? */
/** Follow-ups v3: what a typed follow-up on a Reader or Explorer page asks: sections with intents and structural steps, a question, a refusal or an answer. */
export const planFollowUp = (req: PlanRequest, signal?: AbortSignal) => postJSON<PlanResponse>("/api/followup/plan", req, signal);

/** A section tool the reader clicked ("Another way", "Search the web"): one Haiku call, run only on the click. */
export const sectionTool = (req: SectionRequest, signal?: AbortSignal) => postJSON<SectionResponse>("/api/section", req, signal);

/** "Go deeper" on a formula: one Haiku call, run only on the first click (the note is then saved on the page). */
export const formulaDeeper = (req: FormulaRequest, signal?: AbortSignal) => postJSON<FormulaResponse>("/api/formula", req, signal);
