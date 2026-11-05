---
description: Rules for follow-ups (revise / new / Add section / Feedback / Retry) - a follow-up edits only the targeted parts, never rewrites the page
paths:
  - "server/explainer/followup.py"
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
- **Section pick (Playground, Quick read):** `server/explainer/followup_pick.py`; tests `server/tests/test_followup_pick.py`.
- **Which parts change:** `targets.py` (`find_targets`). `FOLLOWUP_TARGETS=0` turns it off.
- **Edit only those parts, restore the rest:** `revise.py` (`restore_kept_widgets`, `guard_untouched`).
- **Playground edits/adds:** `codelab_edit.py` (`PLAYGROUND_EDIT`, same `_pg_until_clean` checks as a new page).
- **Add section:** `addsection.py` (replans the whole outline).
- **Shared rules:** `shared/followup.ts` and its Python port `server/explainer/core/followup.py`; keep them identical (`npm run parity`).
- **Feedback button:** `feedback_pick.py` (section cards + pick), `feedback.py` (plan), `feedback_check.py` (Jev after-edit check), UI in `app/src/shell/feedback.ts`. The button is hidden (`FEEDBACK = false` in `app.ts`); `feedback_pick.section_cards` is also used by the follow-up pick.
- **Browser:** `app/src/shell/app.ts` (follow-ups, Retry `retryFresh`/`settleRetry`, polling), `app/src/api.ts`.
- **Live test:** `node scripts/followup-live.mjs --base <.variety page> --quality <mode> "<request>"` (needs the API on :8787; real, billed calls).
- **Design notes (start here):** `.claude/followup-planning.md`: problem, architecture and why, workflow, decisions, future work.

