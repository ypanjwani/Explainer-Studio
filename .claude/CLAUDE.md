# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project description

Explainer Studio turns an AI research paper (PDF) or a typed AI concept into an interactive explainer page (sliders, live charts, experiments, cards, quizzes, worked examples, an "Ask about this" chatbot). Free accounts (email + password) keep history on the server.

**AI topics only, and nothing topic-specific:** prompts, components and examples stay topic-neutral, with prompt examples spread across AI areas, because one worked example in a prompt makes the model copy it everywhere.

## Tech stack

- **Frontend:** Vite + TypeScript, no framework (`app/`). Libraries: zod, KaTeX, Plotly, Vega/Vega-Lite, marked, DOMPurify, pdf.js.
- **Backend:** Flask (Python 3.12) + gunicorn (`server/explainer/`), SQLAlchemy + Alembic (SQLite in dev, Postgres in prod), Redis for shared rate-limit counters in prod.
- **AI:** Anthropic API. Haiku writes; "Jev" is a second model that judges/detects (clarity, facts, gates, reviews).
- **Auth:** email + password, JWT + bcrypt. No email/password verification by design. Per account per day: at most 10 explainers (`FREE_DAILY_GENERATIONS`) and $0.25 of AI spend (`FREE_DAILY_USD`).
- **Hosting:** Docker on Render (`render.yaml`).

## Run the local server

From `explainer-studio/`:

1. **First time only:**
   ```
   npm install
   py -3.12 -m venv server/.venv
   server/.venv/Scripts/python -m pip install -e "server[dev]"
   ```
2. **Keys:** create `.env` with `ANTHROPIC_API_KEY=...` and `JEV_API_KEY=...`.
3. **Start:** `npm run dev`. This:
   - builds the viewer
   - brings the dev database up to date (`server/instance/dev.db`)
   - starts Vite on :5173 and the Flask API on :8787 (Vite proxies `/api` to Flask)
4. **Open** `http://localhost:5173` and sign up or sign in.
5. **Stop:** Ctrl+C. On Windows, if port 8787 or 5173 stays busy afterwards, a leftover Flask/Vite process is still running; end it in Task Manager.

## Other commands

- `npm test` (vitest + export/vega checks); one file: `npx vitest run tests/playground.test.ts`
- `npm run test:server` (pytest); one test: `server/.venv/Scripts/python -m pytest server/tests/test_playground.py -k name`
- `npm run typecheck`, `npm run build`
- `npm run parity` (TS vs Python port), `npm run export:server` (regenerates `server/explainer/generated/`)

## Architecture essentials

- Pages render from a JSON spec using prebuilt components. The only AI-written code is Playground's sandboxed-iframe widget (one JSON spec per part, run by `shared/playground/`).
- Generation is a fixed pipeline (summarize, plan, assemble, check), not an agent (`server/explainer/pipeline.py`).
- Validation and math exist twice: TypeScript (`shared/`, `prompts/`) and a Python port (`server/explainer/core/`), kept identical by generated JSON plus parity tests. **Never edit `server/explainer/generated/` by hand**; run `npm run export:server`.
- Always sanitize model output (DOMPurify) and escape user/document text before HTML.
- Per-user rate limits and daily cost caps protect the API budget.
- Per-mode rules live in `.claude/rules/`; read the relevant one before changing a mode.

## AI modes (details in `.claude/rules/`)

We work on two modes only; they are the only ones on the composer menu (`app/src/shell/composer.ts`).

| Mode | id | Summary | Rules file |
|---|---|---|---|
| Quick Read | `lite-plus` | Cheapest: a short, mostly-reading page with charts at fixed values; no quizzes, controls or Ask chatbot; always planned on Low | `quick_read.md` |
| Playground | `playground` | Up to 4 marked sections get a hands-on playground (JSON spec), checked by script and reviewed by Jev (beta) | `playground.md` |

Other modes' code still exists and Playground reuses parts of it (the Code Lab marks/gate/repair machinery in `codelab.py`). Old saved explainers made with other modes must still open. Don't change or delete that code without asking.

## Options

- **Level:** Auto / Beginner / Intermediate / Advanced (Auto is cheapest).
- **How hands on:** Mostly reading (cheapest) / Balanced / Hands on.
- **Effort:** Low / Medium / High (Haiku effort).
- **Thinking (Playground only):** Off (default, cheaper) / On: Haiku thinks before planning and building each playground.
- **Review Rounds (Playground only):** off, 1, 2, 3, 5, until clean (verify, audit, improve loop; more rounds cost more).
- **Page color:** light or dark (affects export).

## Feedback and preferences

- **Feedback button (details in `feedback.md`):** never guesses which part to change.
  - `POST /api/feedback` works in three steps:
    - Code builds a summary card of every section from the JSON (`feedback_pick.section_cards`: blocks, each widget's controls and what it shows, the previous feedback's change).
    - A small Haiku call picks which sections the feedback asks to change, also when the reader describes a kind of part (`feedback_pick.pick_sections`, `FEEDBACK_PICK_SYSTEM` → `feedback.decide_scope`).
    - Haiku (`FEEDBACK_SYSTEM`) gets all the cards plus the full JSON of only the picked sections, and returns a plan: changes (each must quote the reader), locked `keep` sections, a whole-page change, or a question.
    Unsure → the card asks with concrete, multi-select choices (never a silent whole-page change). If the pick is unusable, the plan step picks among all sections. Jev is used only for the after-edit check. A widget add past the 4-part limit becomes a change to the section's own blocks.
  - The dialog shows the plan as a card. The reader confirms it (`POST /api/feedback/<id>/confirm`, no AI call) or answers the question (one more round at most).
  - The confirmed change runs as a revise follow-up carrying `FollowUp.feedback`, and counts as one of the day's explainers. The pipeline skips the target finder and leaves out saved preferences. A change it can't match, or a run that changes nothing, is an error (no new version, not counted), never a whole-page rewrite.
  - `feedback_check.py` then has Jev check each changed section: did it fix the complaint, is nothing worse? Jev sees the section text and each playground's real spec. A failing section gets one retry. After that, text is put back, but a widget is put back only if it got worse; if Jev just doubts the fix, the change is kept with a note.
  - The preference is saved at confirm, only if "Remember" is ticked.
- **Storage:** preferences live in `user_preferences` per user, and messages in `feedback`. Both are deleted with the account. Cost is logged as UsageEvent `feedback`.
- **Limits:** a new preference replaces the user's active one on the same topic, with at most 8 active.
- **Managing:** Account → My preferences turns them on or off and deletes them (`routes/preferences.py`).
- **Use in later pages:** `generate_route` loads active preferences on the server (the client can't send any) as `req["prefs"]`. The pipeline adds `prefs_hint()` to the planner hint. Without preferences nothing changes; modes are untouched. `PREFERENCES=0` turns this off.

## Follow-ups

- **A follow-up does not rewrite the page.** `server/explainer/targets.py` (`find_targets`) names the affected parts; only those text/widget parts change, and the others are restored (`revise.restore_kept_widgets`, `guard_untouched`).
  - Playground: a playground is edited or added by `codelab_edit.py`, through the same checks as a new page.
  - Quick Read: a "change this chart" request becomes a rewrite of that section.
- **Whole-outline replans:** page-wide requests, "Add section" and Review Rounds replan the whole outline. `FOLLOWUP_TARGETS=0` turns the finder off.
- **Mode:** a typed revise follow-up runs in the page's own mode (`quality`, review rounds, Thinking from the base record), not the composer's current pick; "new" follow-ups use the composer's.
- **Live test:** `node scripts/followup-live.mjs --base <.variety page> --quality <mode> "<request>"` (needs the API on :8787; makes real, billed calls).

**Retry (`↻ Retry`, `app/src/shell/app.ts` `retryFresh`/`settleRetry`):**
- Shown only on a conversation's last message. It makes a fresh explainer from the same request as a **new record** with `replaces: <old id>`.
- The old record is hidden while the retry builds, deleted (with its chat, progress and notes) once the retry is done, and comes back if it fails. Unfinished retries are settled again on load and while polling.
- The request carries `avoid` (the old page's section titles plus its theme): `pipeline.avoid_hint` asks for another route, and `other_theme` changes the theme.
- It counts as one of the day's explainers.
