---
description: Rules for follow-ups (revise / new / Add section / Feedback / Retry) - a follow-up edits only the targeted parts, never rewrites the page
paths:
  - "server/explainer/followup.py"
  - "server/explainer/modes/followup/**"
  - "server/tests/test_modes_followup_same.py"
  - "server/tests/test_steps.py"
  - "server/tests/test_understand.py"
  - "server/tests/test_section_editor.py"
  - "server/tests/test_page_index.py"
  - "server/explainer/targets.py"
  - "server/explainer/revise.py"
  - "server/explainer/codelab_edit.py"
  - "server/explainer/core/followup.py"
  - "server/explainer/addsection.py"
  - "server/explainer/routes/ai.py"
  - "server/explainer/feedback.py"
  - "server/explainer/feedback_pick.py"
  - "server/explainer/feedback_check.py"
  - "shared/followup.ts"
  - "app/src/shell/app.ts"
  - "app/src/shell/feedback.ts"
  - "app/src/api.ts"
  - "scripts/followup-live.mjs"
  - "server/tests/test_targets.py"
  - "server/tests/test_revise.py"
  - "server/tests/test_codelab_edit.py"
  - "server/tests/test_feedback.py"
  - "server/tests/test_feedback_pick.py"
  - "server/tests/test_feedback_check.py"
  - "server/tests/test_retry.py"
  - "tests/followup.test.ts"
---

# Follow-ups

## Follow-ups v3: understand → section editor (Nov 9 2026; the default since Nov 11 2026, `FOLLOWUP_PLAN=0` turns it off)

**Where it lives (Nov 11 2026):** `server/explainer/modes/followup/`, Reader and Explorer's own follow-up path; nothing there imports another mode's code (`server/tests/test_modes_boundary.py`).
- `understand.py`, `page_index.py`, `steps.py`, `section_editor.py`: moved here.
- `run.py`: the run once the plan is chosen, `/api/generate` via `modes.handles`, copied from `pipeline.generate`'s planned branch.
- `playground_edit.py`: `codelab_edit` for playgrounds, built on `modes/explorer/`.
- `targets.py`, `revise.py`, `check.py` (`feedback_check`), `cards.py` (`feedback_pick.block_line`, `followup_pick.parse_pick_request`): copies.

The app shows a failed plan call as an error with "Try again". The section pick below now runs only with `FOLLOWUP_PLAN=0` (legacy), and a revise follow-up without a plan still goes through `pipeline.generate` (legacy). `test_modes_followup_same.py` checks the new run against `pipeline.generate`'s v3 branch, and the playground edit/add/agree against `codelab_edit`.

Design: `.claude/followup-v3-plan.md`. With the flag on, a typed revise follow-up on a `playground` or `lite-plus` page goes to `POST /api/followup/plan` instead of the pick below; flag off → `{"kind": "off"}` and the app uses the pick.
- **Understand** (`understand.py`, `UNDERSTAND_SYSTEM`, format `followupUnderstand`, Haiku Low; reads the page map `page_index.map_text` and the conversation's last 5 prompts with what each did): `change` / `ask` / `cannot` / `answer` / `new_page` / `revert`. A change is **sections with the reader's intent** (`{section, intent, quote}`, never "edit the text/playground") plus **typed structural steps** (`OPS` = `PLAN_OPS` in `shared/spec.ts`, a test checks): add / remove / move / split / merge a section, set_level / set_tone / set_length, edit_page_meta, revert. Shortcuts with no AI call: undo, "remove section N".
- **Section names and quotes (Nov 10 2026):** the map names sections by **handle** (`S1`, `S2` …, never ids); `resolve_ref` turns what Haiku wrote into the id by code (handle, a number in any form, `after: start/end`, exact or partial label). Only how a section was written is fixed, never meaning: two possible sections ("the last one" = last section or last playground, a shared label) → `which` asks the reader with one option per section (no AI call); none → "Skip that part, do the rest", or an error naming it. Fixes are logged and returned as `fixes`. **Nothing is dropped for its quote:** `flag` marks a part `unsure` when its quote isn't in what the reader said (`quote_ok`: any script, rewording, numbers, earlier messages) or names another section; an unsure part forces the plan card, where an unsure section part has a box to untick. `steps.run_plan` strips the note.
- **Knock-ons** (`understand.knock_ons`, from the page map, `derived` + `because`): mentions of a removed / moved / merged section, ideas only a removed section explained. **Budget** (`estimate_usd` vs `FOLLOWUP_MAX_USD`, default 0.15): over it → ask with cheaper options (text only, or the first sections). **Confirm** (`needs_confirm`): remove/merge, level, > 4 sections, > 2 knock-ons, any part marked unsure.
- **Page map** (`page_index.py`): code part (cards, references found by code) + one Haiku Low call (`INDEX_SYSTEM`); cached in the `page_index` table by page hash (migration 0009).
- **Run** (`steps.run_plan`, pipeline `planned` branch): structure first (moves and a merged section's playground by code; new / split / merged sections and the page title by the page planner via `apply_targets`; removals by code; a new section's playground via `gate_one`). Then every intent (a page-wide step = an intent on every section, plus the playground's level when it doesn't match) goes to the **section editor** (`section_editor.py`): evidence by code + Jev (section, playground spec, checker problems, text↔playground agreement, level, neighbours), then one Haiku call **with Thinking** (system = the page planner prompt, cached; `SECTION_EDIT_NOTE` + `PLANNER_NOTE` or `LITE_REVISE_NOTE` in the user turn) that decides `change`/`none`, a new stage and a playground action (keep / edit / add / remove + instructions). Text merged with `parse_revision` + `guard_untouched` (a stage with no usable blocks is rejected); playgrounds by `codelab_edit` (checker first, repairs, Jev review). A section still naming a removed one gets one more pass. `feedback_check.check` per changed section (≤ 6): one retry through the editor, then that section alone is put back.
- **Report**: `spec.revision.steps` done / kept (the editor's reason) / undone / skipped. Every section checked and right → `steps.Checked`: a message starting `✓ ` (`CHECKED_PREFIX`) that `history._apply_generation` stores as a reply (`status done`, `answer`, no spec) and the app shows as one; not counted. Nothing changed for another reason → `ReaderError` with the reason.
- **App** (`app.ts` `matchFirst` / `planFirst` / `planView` / `runSteps` / `historyOf` / `revert` / `undoVersion`; status `picking`, `rec.plan`): question with options + "Add detail", the plan card, `cannot` with its alternative, answers and checked replies in the thread (`rec.answer`), Undo (`rec.reverted`, no AI call, not counted).
- **Tests:** `test_understand.py`, `test_section_editor.py`, `test_steps.py`, `test_page_index.py` (data in `tests/followup_v3_data.py`). **Eval:** `server/evals/followup_plan_eval.py` (+ `followup_plan_extra_cases.json`; the part isn't scored). **Live:** `followup-live.mjs --plan`.

## Playground and Quick read: sections are picked first (Nov 5 2026)

A typed revise follow-up on a `playground` or `lite-plus` page (not Playground 2.0) is matched to sections **before** anything is built:
- `POST /api/followup/pick` (`followup_pick.py`):
  - Code builds a card of every section from the page JSON (`feedback_pick.section_cards`).
  - **Jev**, one call, narrows down which sections: `scope` (some / every / new / unclear) and one yes/no per section. Its scores are logged ("Follow-up pick (Jev): scope [...]; sections [...]").
  - Jev sure of particular sections (`PICK_SURE`, `SECTION_IN`/`SECTION_OUT`) → `scoped_targets`: `find_targets_ask` on a page holding **only those sections**, so Haiku writes each section's own change (playground or text, and what), or asks. Targets on other sections are dropped (`SCOPED_PARTS`); `by: "jev+haiku"`. (Nov 5: Jev used to give every section the reader's whole message, and its "is it about the widget?" question turned "can this playground be improved?" into a text edit.)
  - Jev sure it's the whole page → `pageWide`, no Haiku call. Otherwise (or the scoped call gives nothing usable), the Haiku finder for these pages (`targets.find_targets_ask`, `TARGET_ASK_SYSTEM`, format `targetsAsk`) decides, or returns a **question** with 2-4 options when the request could mean quite different changes ("make it shorter"). Nothing usable → an error asking the reader to name the part.
  - Not one of the day's explainers; cost recorded as `followup-pick`.
- The app (`app.ts` `pickFirst` / `answerPick` / `cancelPick`, record status `picking`, `rec.pick.question`) shows the question as buttons; a click runs that option's targets with no second AI call. If the pick call itself fails, the revise runs without a pick and the server's finder decides.
- The chosen parts travel as `FollowUp.pick = {targets, pageWide}` (`followup.parse_pick`); target parts include **`remove`** (the section is dropped by code, with its playground; a page keeps at least one section).
- **The pipeline never takes the whole-page Interactive path for these two modes.** `pageWide` → a `text` target on every section (`targets.every_section`), one scoped planner call, and the title, key intuition and level may change (`page_fields`). A missing pick (older app) runs the finder; a finder that can't place it is a `ReaderError`.
- Playground pages: the planner writes prose only (prebuilt blocks stripped); each playground is put back, and rebuilt only where Jev says text and playground no longer match (`agrees`). A playground edit first runs the checker on the current spec and passes its problems to Haiku ("it shows nothing").
- Quick read pages: text edits get `LITE_EDIT_NOTE` (no quizzes, controls or "move the slider"); sliders aren't stripped because charts read their values.
- The Feedback button keeps its own Haiku picker. Playground 2.0 and the other modes keep the old finder path.

## Where the code lives

- **Entry:** `server/explainer/followup.py`; API routes in `routes/ai.py`.
- **Follow-ups v3 (default; `FOLLOWUP_PLAN=0` turns it off):** `server/explainer/modes/followup/understand.py` (what the follow-up asks), `section_editor.py` (evidence + the editor), `steps.py` (running a plan), `run.py` (the follow-up's run), `page_index.py` (page map + `page_index` table); route `/api/followup/plan`; prompts `UNDERSTAND_SYSTEM`, `SECTION_EDIT_NOTE`, `INDEX_SYSTEM`, `LITE_REVISE_NOTE`.
- **Section pick (Playground, Quick read):** `server/explainer/followup_pick.py`; tests `server/tests/test_followup_pick.py`.
- **Which parts change:** `targets.py` (`find_targets`). `FOLLOWUP_TARGETS=0` turns it off.
- **Edit only those parts, restore the rest:** `revise.py` (`restore_kept_widgets`, `guard_untouched`).
- **Playground edits/adds:** `codelab_edit.py` (`PLAYGROUND_EDIT`, same `_pg_until_clean` checks as a new page).
- **Add section:** `addsection.py` (replans the whole outline).
- **Shared rules:** `shared/followup.ts` and its Python port `server/explainer/core/followup.py`; keep them identical (`npm run parity`).
- **Feedback button:** `feedback_pick.py` (section cards + pick), `feedback.py` (plan), `feedback_check.py` (Jev after-edit check), UI in `app/src/shell/feedback.ts`. The button is hidden (`FEEDBACK = false` in `app.ts`); `feedback_pick.section_cards` is also used by the follow-up pick.
- **Browser:** `app/src/shell/app.ts` (follow-ups, Retry `retryFresh`/`settleRetry`, polling), `app/src/api.ts`.
- **Live test:** `node scripts/followup-live.mjs --base <.variety page> --quality <mode> "<request>"` (needs the API on :8787; real, billed calls).
- **Design notes (start here):** `.claude/followup-planning.md` (the pick: problem, architecture and why, workflow, decisions); `.claude/followup-v3-plan.md` (understand → section editor).

