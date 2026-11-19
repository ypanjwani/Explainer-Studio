# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project description

**ClariTale** (product name since Nov 5 2026; the repo and code still say Explainer Studio) turns an AI research paper (PDF) or a typed AI concept into an interactive explainer page (sliders, live charts, experiments, cards, quizzes, worked examples, an "Ask about this" chatbot). Free accounts ("Continue with Google") keep history on the server.

**AI topics only, and nothing topic-specific:** prompts, components and examples stay topic-neutral, with prompt examples spread across AI areas, because one worked example in a prompt makes the model copy it everywhere.

## Tech stack

- **Frontend:** Vite + TypeScript, no framework (`app/`). Libraries: zod, KaTeX, Plotly, Vega/Vega-Lite, marked, DOMPurify, pdf.js.
- **Backend:** Flask (Python 3.12) + gunicorn (`server/explainer/`), SQLAlchemy + Alembic (SQLite in dev, Postgres in prod), Redis for shared rate-limit counters in prod.
- **AI:** Anthropic API. Haiku writes; "Jev" is a second model that judges/detects (clarity, facts, gates, reviews).
- **Auth:** "Continue with Google" only for new accounts (Nov 5 2026; `server/explainer/google_auth.py`, routes in `routes/auth.py`): Google verifies the email, a Google sign-in with an existing account's email opens that account, and new accounts count toward the per-network sign-up cap. Sessions are our own JWT + refresh cookie either way. **No password option anywhere in the UI** (removed Nov 5 2026): old password accounts sign in with Google under the same email. The password routes (register, login, change password) stay in the code but answer 403 `password_off` unless `PASSWORD_AUTH=1` (tests use it to make accounts). Google-only accounts have `password_hash = NULL`; deleting any account is confirmed by typing its email. Privacy policy: `app/public/privacy.html`. Per account per day: at most 10 explainers (`FREE_DAILY_GENERATIONS`) and $0.25 of AI spend (`FREE_DAILY_USD`).
- **Hosting:** Docker on Render (`render.yaml`).

## Run the local server

From `explainer-studio/`:

1. **First time only:**
   ```
   npm install
   py -3.12 -m venv server/.venv
   server/.venv/Scripts/python -m pip install -e "server[dev]"
   ```
2. **Keys:** create `.env` with `ANTHROPIC_API_KEY=...`, `JEV_API_KEY=...`, `GOOGLE_CLIENT_ID=...` and `GOOGLE_CLIENT_SECRET=...` (see `.env.example`).
3. **Start:** `npm run dev`. This:
   - builds the viewer
   - brings the dev database up to date (`server/instance/dev.db`)
   - starts Vite on :5173 and the Flask API on :8787 (Vite proxies `/api` to Flask)
4. **Open** `http://localhost:5173` (not `127.0.0.1`: Google only accepts the registered redirect URI) and Continue with Google. Needs `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` in `.env`; while the Google app is in Testing, only its listed test users can sign in.
5. **Stop:** Ctrl+C. On Windows, if port 8787 or 5173 stays busy afterwards, a leftover Flask/Vite process is still running; end it in Task Manager.

## Other commands

- `npm test` (vitest + export/vega checks); one file: `npx vitest run tests/playground.test.ts`
- `npm run test:server` (pytest); one test: `server/.venv/Scripts/python -m pytest server/tests/test_playground.py -k name`
- `npm run typecheck`, `npm run build`
- `npm run parity` (TS vs Python port), `npm run export:server` (regenerates `server/explainer/generated/`)

## Old modes removed (Nov 11 2026)

Only Reader and Explorer exist. Every other mode (Interactive, Creative, Smart, Lab, Code Lab 1.0-7.0, Explorer 2.0, review rounds), Add section, the Feedback button, saved preferences and the old follow-up section pick were deleted. `/api/generate` refuses any other quality, and any revise follow-up without a v3 plan, with 400 `mode_off` (not counted). Old saved explainers made with retired modes still open (the viewer in `shared/` and `LEGACY_MODE_NAMES` in `app/src/shell/modes.ts` stay) but can't be changed or re-run. The `feedback` and `user_preferences` tables are kept (old data; account delete still clears them).

## Architecture essentials

- Pages render from a JSON spec using prebuilt components. The only AI-written code is Playground's sandboxed-iframe widget (one JSON spec per part, run by `shared/playground/`).
- Generation is a fixed pipeline (summarize, plan, assemble, check), not an agent.
  - **Reader and Explorer each have their own pipeline:** `server/explainer/modes/` (`reader.py`, `explorer/`, shared steps in `common.py`), chosen in `routes/ai.py` (`modes.handles`).
  - **Their v3 follow-ups:** `server/explainer/modes/followup/`. Playground edits use Explorer's own builder (`modes/explorer/`).
  - **Rule:** nothing under `modes/` imports mode logic from outside it; only shared tools: `llm`, `jev`, `data`, `core/`, `brief`, `design`, `clarity`, `factcheck`, `notes`, `filter`, `paper_cache`, `db`, …. `server/tests/test_modes_boundary.py` enforces this.
  - The composer's modes are in `app/src/shell/modes.ts`.
- Validation and math exist twice: TypeScript (`shared/`, `prompts/`) and a Python port (`server/explainer/core/`), kept identical by generated JSON plus parity tests. **Never edit `server/explainer/generated/` by hand**; run `npm run export:server`.
- Always sanitize model output (DOMPurify) and escape user/document text before HTML.
- Per-user rate limits and daily cost caps protect the API budget.
- **Building = a locked card with a progress ring** (Nov 18 2026, `app/src/shell/progress.ts`): the result card shows at once, locked, with the current step above it and the step list folded under "Show steps". The percent is an estimate: expected seconds per step and mode, the running step timed from its `at` (stored by `history.generation_event`, so a reload resumes), never backwards, held at 95% until done. A revise follow-up spins with no number. Step events carry an optional `preview` (`modes/common.py` `plan_preview` / `design_preview`): title/subtitle after the plan, theme/icons after the design. Tests: `tests/progress.test.ts`.
- Per-mode rules live in `.claude/rules/`; read the relevant one before changing a mode.

## AI modes (details in `.claude/rules/`)

There are two modes; they are the only ones on the composer menu (`app/src/shell/composer.ts`).

| Mode | id | Summary | Rules file |
|---|---|---|---|
| Reader (was Quick read) | `lite-plus` | Cheapest: a short, mostly-reading page with charts at fixed values; no quizzes, controls or Ask chatbot; always planned on Low | `quick_read.md` |
| Explorer (was Playground) | `playground` | Up to 4 marked sections get a hands-on playground (JSON spec), checked by script and reviewed by Jev (beta) | `playground.md` |

Old saved explainers made with retired modes must still open: don't remove the viewer code in `shared/` or `LEGACY_MODE_NAMES` without asking.

## Options

**The "What do you want to add to each section?" question is hidden for v1** (`EXTRAS_QUESTION = false` in `app/src/shell/app.ts`, Nov 5 2026): new prompts, new-topic follow-ups and edits send `extras: []` (None) and start at once; records already waiting on the question still show it. `extras.ts` stays.

**v1 shows only Effort, Thinking and Page color** (`MORE_OPTIONS = false` in `app/src/shell/composer.ts`, Nov 5 2026). Level and How hands on are hidden and send their defaults (Level Auto, Balanced), not a remembered choice; their code stays.

- **Level:** Auto / Beginner / Intermediate / Advanced (Auto is cheapest).
- **How hands on:** Mostly reading (cheapest) / Balanced / Hands on.
- **Effort:** Low / Medium / High (Haiku effort).
- **Thinking (Explorer only):** Off (default, cheaper) / On: Haiku thinks before planning and building each playground.
- **Page color:** light or dark (affects export).

## Feedback and preferences: removed (Nov 11 2026)

The Feedback button, its routes and saved preferences ("My preferences" in Account) were deleted. The `feedback` and `user_preferences` tables stay so old rows survive; account delete still clears them (FK cascade). Dropping them needs an Alembic migration.

## Follow-ups (file map in `.claude/rules/followup.md`)

**To explore or change follow-ups, start with `.claude/followup-v3-plan.md`**, then `.claude/rules/followup.md` for the file map. (`.claude/followup-planning.md` describes the removed section pick; history only.)

**v3 and v4 are kept apart (Nov 13 2026) so v3 can be deleted without touching v4:**
- Code: `modes/followup/v3/` and `modes/followup/v4/`, plus shared files (`shared.py`, `page_index.py`, `check.py`, `playground_edit.py`, `revise.py`, `cards.py`).
- Only `entry.py` (`/api/followup/plan`) and `run.py` (`/api/generate`) pick a version, each importing v3 only inside its v3 branch.
- v4 never imports v3; anything both need goes in a shared file. `test_modes_boundary.py` enforces this and runs the v4 suite with v3 unimportable.
- Deleting v3: steps in `.claude/rules/followup.md`.

**Version numbers come from the server (Nov 16 2026, follow-ups v5 phase 1):** every finished page is saved as a version (`server/explainer/versions.py`), numbered per page line by the server and sent back in the `done` event; Undo is `POST /api/explainers/<id>/revert`. Details in `followup.md` ("Version store").

**Follow-ups v5 (Nov 16 2026; the default follow-up since Nov 19 2026, `FOLLOWUP_V5=0` falls back to v4/v3):** `modes/followup/v5/`, standing alone (never imports v3 or v4). **One agent (phase 4):** `/api/followup/plan` is code only (undo, or a plan carrying the conversation); in `/api/generate` one Haiku agent (`v5/agent.py`) reads the base version's main file, the version log, the page's full text (`FOLLOWUP_V5_FULLPAGE`), the whole paper a paper page was made from (Nov 18 2026: the page's own source is only a summary) and the conversation, then answers, asks, says it's a new topic, or changes the page with tools: any version readable, `start_from` an older version, restore sections and playgrounds from others. Budget by Effort; Haiku runs at the reader's Effort from the first call (Nov 19 2026; was Low until the first change, so whole-page checks were read at Low). The checks (Jev, playground readings, the after-edit review) come back to it as feedback at its first finish; a final guard puts back only what's still broken; it writes its own reply. Past the day's explainers it only answers. With `FOLLOWUP_V5_LOOK` (Nov 18 2026, on by default since Nov 19, `0` turns it off; Explorer only) it can also look at playgrounds: screenshots in the page's own colours (`v5/look.py`), capped by the reader's Effort, and a look check at its first finish (feedback, never a put-back). Details in `followup.md`.

**First message (Nov 19 2026; on by default, `FIRST_TALK=0` turns it off):** every message that would start a new explainer (a conversation's first prompt, any message while a conversation has no page yet, a paper) is read first by one forced Haiku tool call (`server/explainer/modes/intake.py`, prompt `INTAKE_SYSTEM`), run first inside `/api/generate` (`modes.generate`). Two tools, shared with v5 in `modes/topic.py`:
- `answer {reply, offer?}`: a question, "what would a page on X cover?", chit-chat, a question about the app, a non-AI topic or paper, something too vague (asked back in the text). No page, not counted (it ends as `ANSWER_PREFIX` + JSON, like v5's answers); `offer` is a brief shown as a "✨ Make an explainer about this" button (`buildOffer`, `direct: true`).
- `new_topic {brief}`: the brief (topic + what the conversation settled) replaces the request's text (a paper's note) and the build goes on in the same request; the `intake` step's detail holds it, so Retry of that page builds the brief again (`direct: true`).
- When unsure it builds; a failed call builds as asked; past the day's 10 it only answers. Skipped for revise follow-ups and `direct` requests (offer and new-topic buttons, Retry of a page). The app sends `history` (`historyOf`) while a conversation has no page, and a typed message there keeps an earlier uploaded paper. The ring spins ("Reading your message…") until the first pipeline step. An Edit of a page that ends as an answer keeps the page (`settleRetry`).
- v5's `new_topic` now carries a `brief` too (`NEW_TOPIC_BRIEF_PREFIX`), and its button builds from it; v5's prompt says AI topics only.
- Tests: `server/tests/test_intake.py`, `tests/progress.test.ts`. Eval: `server/evals/intake_eval.py` (36 labelled rows, ~$0.005 a run; Nov 19 2026: 36/36 twice, no plain topic wrongly answered).

**No follow-up router (removed Nov 13 2026):** the old `POST /api/route` asked "revise / new / unclear" first and showed "Change it or New explainer?" on unclear, even for plain questions. Now every message under a Reader or Explorer page goes straight to the plan step (v3 or v4), which answers, asks, refuses, undoes or changes. A different topic comes back as `cannot` + `newTopic` and the app shows a "✨ New explainer about this" button (composer's mode; a click, never automatic). A new paper, or a page made with a retired mode, is always a new explainer. Old records stuck in `deciding` load as failed messages whose Try again goes through the plan step.

**Follow-ups v4 (Nov 13 2026, behind `FOLLOWUP_V4=1`, used only when `FOLLOWUP_V5=0`):** the same two calls, but `/api/followup/plan` becomes a talk step (`modes/followup/v4/talk.py`). It answers questions (Reader pages too), asks with 2–4 options, refuses kindly with an alternative, or writes one brief. Then a Haiku editor with tools (`v4/editor.py`, `v4/tools.py`) changes anything the brief asks:
- page and section titles, any text or block, the structure
- Explorer playgrounds: patched directly, rebuilt or added through Explorer's builder, or removed

Each tool call is applied and checked by code. Jev checks each changed section and puts back any that fails. **Playground readings:** the talk step and the editor see what each playground really shows, not just its code. Every playground change answers with its new numbers and Jev's text-vs-numbers check, and a change that makes the text contradict its playground is put back. Details are in `followup.md`. The editor may ask a question before writing (`Asked`, not counted). A friendly reply (`spec.revision.reply`) is written from what really happened. Design: `.claude/followup-v4-plan.md`. Playgrounds also gained a `table` mark. Phase 3: when no built-in view fits, the editor can ask the builder for a playground's own picture (`custom: true`), drawn with the whitelisted `drawing` mark (follow-ups only).

**Follow-ups v3 (Nov 9 2026; the default from Nov 11 to Nov 19 2026, now used only when `FOLLOWUP_V5=0`; code in `server/explainer/modes/followup/v3/`):** on Reader and Explorer pages a first call (`v3/understand.py`, `POST /api/followup/plan`) reads the follow-up against a cached page map and the conversation: it names the sections and what the reader wants in each (never text vs playground), plus typed structural steps (add/remove/move/split/merge a section, level, tone, length, title, undo), or asks (options + "Add detail"), refuses with the nearest alternative, or answers. Then a section editor (`v3/section_editor.py`, Haiku + Thinking) looks at each section's text, playground spec, checker problems and text-playground agreement and decides what to change (text, playground, both, or nothing with a reason). Code does the structure and knock-on fixes, Jev checks each changed section (undone alone if it fails), and "checked, nothing needed changing" is a reply, not an error. If the plan call fails, the app shows the error with "Try again" (no fallback).

- **A follow-up does not rewrite the page:** only the sections the plan names change; the others are restored (`modes/followup/revise.py` `restore_kept_widgets`; in v3 also `v3/targets.py` `guard_untouched`).
- **Mode:** a typed revise follow-up runs in the page's own mode (Thinking from the base record), not the composer's current pick; "new" follow-ups use the composer's.
- **Live test:** `node scripts/followup-live.mjs --base <.variety page> --quality <lite-plus|playground> "<request>"` (needs the API on :8787; makes real, billed calls).
- **Edit chat was removed** (Nov 2026): the drawer's "Edit" tab, the ✏️ buttons on page parts, `app/src/shell/edit.ts`, `/api/edit` and its prompt. The bottom-right drawer holds only "Ask about this"; Reader pages, which have no Ask chatbot, show no drawer. `shared/edit.ts` / `core/edit.py` keep only what the fact check uses (`leaves`, `getAt`, `applyChanges`). The ✏️ Edit icon under a prompt is a different feature and stays.

**Prompt icons under each prompt bubble (`promptActions`, Nov 5 2026): ↻ Retry, Copy, ✏️ Edit.**
- Copy is on every prompt (copies right away in the click, `copyNow`; `navigator.clipboard` only as a fallback, since it can hang on a permission prompt).
- Retry and Edit only on the conversation's last message, once it is done or failed. On a failed message both run again in place (`retryFailed` / `editPrompt`, with the composer's current options); on a finished one Retry asks to confirm, and Edit opens a box in place of the bubble (Enter sends, Esc cancels).
- Edit = a newly sent prompt replacing the old one (`editPrompt` → `retryFresh(old, text)`, `editedRequest`): the edited text (a concept's `text` or a paper's `note`) with the **composer's current mode and options** (`ComposerEl.choices`, v1 defaults for hidden ones), not the old explainer's. A revise follow-up keeps its page's own mode and drops its plan, so the new text is understood again. A newly chosen Explorer mode would ask "what to add to each section" first (status `asking`; hidden in v1); retries settle only once `done`/`error`.

**Retry (`↻ Retry`, `app/src/shell/app.ts` `retryFresh`/`settleRetry`):**
- Shown only on a conversation's last message, as an icon under the prompt. It makes a fresh explainer from the same request as a **new record** with `replaces: <old id>`.
- The old record is hidden while the retry builds, deleted (with its chat, progress and notes) once the retry is done, and comes back if it fails. Unfinished retries are settled again on load and while polling.
- It runs the same prompt **from scratch** with the **composer's current mode and options** (Nov 13 2026; `withCurrentOptions`, the same as ✏️ Edit; the confirm names them): nothing about the old page is sent (any saved `avoid` is dropped). A revise follow-up keeps its page's own mode and its plan. Retry / Try again on a failed message also runs in place with the current options (`retryFailed`). The old `avoid` titles list grew with every retry and made pages shorter, so it was removed (Nov 4 2026); the server's `avoid_hint`/`other_theme` (`modes/common.py`) stay but are dormant.
- It counts as one of the day's explainers.
