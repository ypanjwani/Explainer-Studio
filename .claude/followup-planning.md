# Follow-ups on Playground and Quick read pages

Status: built Nov 5 2026. 585 server + 239 frontend tests pass. One live browser test passed.
Nov 9 2026: follow-ups v3 (`.claude/followup-v3-plan.md`: understand → section editor) replaces this pick when `FOLLOWUP_PLAN=1`; with the flag
off (the default) everything here runs unchanged.
Applies to `playground` and `lite-plus` (Quick read) pages only. Playground 2.0 and the other modes keep the old finder path.
File map and rules: `.claude/rules/followup.md`. This file explains what was built and why.

## 1. Problem

A reader types a follow-up under a finished page ("the low rank playground shows nothing", "make it simpler",
"remove section 3"). Before Nov 5:

- **Whole-page requests went through the Interactive planner.** On a Playground page, "make it simpler" took the
  whole-outline path. Playgrounds were lost or rebuilt, and Quick read pages gained quizzes and sliders.
- **One message became one instruction for every section.** "Make section 2's chart log scale and simplify
  section 4" sent both halves to both sections.
- **Playground requests became text edits.** "Can the low rank trick playground be improved?" picked the right
  section, but only its wording changed. Readers say "playground", "demo" or "simulation", not "widget".
- **Unclear requests were guessed.** "Make it shorter" could mean shorter wording or fewer sections; the finder
  picked one silently.
- **No way to remove a section** through a follow-up.

## 2. Why we need it

- Readers are learners. A follow-up usually fixes one thing; losing a working playground to fix a sentence is the
  worst outcome.
- Playgrounds are the costly, checked part of a page. Rebuilding them when they weren't asked about wastes money
  and can make them worse.
- Quick read must stay a short reading page after any number of follow-ups.
- Asking the reader a quick question costs nothing; a wrong rewrite costs a page.

## 3. Architecture (and why)

```
reader text
   │
   ▼
POST /api/followup/pick  (followup_pick.py)       ← runs BEFORE anything is built
   ├─ code: section cards from the page JSON     (feedback_pick.section_cards, no AI)
   ├─ Jev: scope + one yes/no per section         (cheap, fast, one call)
   │     ├─ whole page, sure     → pageWide                       by "jev"
   │     ├─ some sections, sure  → Haiku on ONLY those sections   by "jev+haiku"
   │     └─ unsure / new / error → Haiku on the whole page        by "haiku"
   └─ result: {targets, pageWide} | {question + 2-4 options} | {error}
   │
   ▼
app: shows the question as buttons (no second AI call) or sends the pick
   │
   ▼
revise follow-up with FollowUp.pick  →  pipeline.py  →  targets.apply_targets
   ├─ remove   → dropped by code (keeps at least one section)
   ├─ text     → one scoped planner call (changes only)
   ├─ widget   → codelab_edit (checker problems passed in, same checks as a new page)
   └─ untouched sections restored byte for byte (restore_kept_widgets, guard_untouched)
```

| Piece | Why this way |
|---|---|
| **Pick is its own endpoint, before the build** | An unclear request can be asked about before any money is spent or anything changes. |
| **Section cards built by code** | The page JSON is already there. Short cards (title, kind of playground, key text) are cheap for Jev and Haiku to read; the full JSON goes only to the step that edits. |
| **Jev narrows first** | Jev is cheaper and faster than Haiku and good at yes/no and choice questions. It decides *which* sections; it can't write text. |
| **Haiku writes each section's change** | Only a writing model can split one message into a per-section `what` and choose playground vs text. It sees only the picked sections, so it can't drift to others. |
| **No Jev "is it the widget?" question** | Removed: it read "playground" as not-a-widget and turned playground requests into text edits. Haiku decides the part, with a prompt rule that playground/demo/simulation = widget. |
| **Haiku fallback on the whole page** | When Jev isn't sure (scores between 0.3 and 0.7, or scope under 0.6), a wrong narrow pick is worse than one more Haiku call. |
| **Questions with options carry their own targets** | A click runs that option's targets directly: no second AI call, no second guess. |
| **pageWide = a text target on every section** | Keeps the targeted path (playgrounds kept, untouched parts guarded) instead of the whole-outline Interactive planner. Title, key intuition and level may change (`page_fields`). |
| **Remove done by code** | Deleting a section needs no AI; code also guarantees one section stays. |
| **Playground edit runs the checker first** | "It shows nothing" usually means a broken spec. Haiku gets the checker's actual problems, not just the reader's words. |
| **Jev `agrees()` after text edits** | On Playground pages a playground is rebuilt only when its section's new text no longer matches it. |
| **Quick read: `LITE_EDIT_NOTE`, sliders not stripped** | The note stops quizzes, controls and "move the slider" text. Sliders stay because charts read their values (`noControls` hides them). |
| **Feedback button kept separate** | It already has its own Haiku picker, confirm step and Jev check; merging would change a tested flow. |

## 4. Workflow

1. Reader types a revise follow-up on a Playground or Quick read page.
2. App (`app.ts` `pickFirst`) sets the record to `picking` ("Finding the part of the page to change…") and calls
   `/api/followup/pick` with the text, quality and the revise follow-up.
3. Server rebuilds the page (`restore_kept_widgets`) and makes the section cards.
4. Jev answers `scope` (some / every / new / unclear) and `s<N>` for each section. Scores are logged:
   `Follow-up pick (Jev): scope [...]; sections [...] -> ...`.
5. Decision:
   - `every`, sure → `{pageWide: true}`, by `jev`.
   - `some`, every section clearly in or out → `scoped_targets`: `find_targets_ask` on a page with only those
     sections. Targets on other sections, or with other parts, are dropped. A question is passed through.
   - Anything else, or the scoped call gives nothing → `find_targets_ask` on the whole page.
   - Still nothing → error: "Name the section or the playground and try again."
6. App:
   - question → shows it with option buttons and Cancel (`answerPick` / `cancelPick`).
   - targets → `answerPick` puts them on `FollowUp.pick` and starts the build.
   - pick call failed → builds with no pick; the server's finder decides.
7. Pipeline (`pick or picked_page` branch): `clean_targets`, or `every_section` for pageWide; then
   `apply_targets` → remove, text edit, widget edits, restore the rest, guard untouched parts.
8. New version saved with a revision summary (sections changed, removed, kept). Counted as one of the day's explainers;
   the pick's own cost is logged as `followup-pick`.

## 5. Future improvements

- **Tune Jev's thresholds** (0.6 / 0.7 / 0.3) with an offline replay: 30-50 real follow-ups, compare Jev, Jev+Haiku
  and Haiku-only against the right answer. Right now they're guesses.
- **Level change rechecks playgrounds.** A whole-page "make it for beginners" rewrites text, but playgrounds are only
  rebuilt if `agrees()` says they no longer match; their difficulty isn't checked against the new level.
- **Removed sections leave stale references** ("as we saw in section 3"). Add a text pass on sections that mention it.
- **New sections get no playground** (follow-ups and Add section). Run the Playground marker on the new section.
- **Quick read text edits** still use `planner_system()` plus the note. A Quick read edit prompt would be tighter.
- **Playground 2.0** could use the same pick; its parts come from `pgpick.py`, so the widget edit step differs.
- **Image input** ("this is broken" + a screenshot): left out for now. Haiku can read images; the pick would send it
  only to the scoped call.
- **✏️ Edit chat (v2):** a per-block edit button would skip the pick entirely (target known).
- **Live measurement:** the same 10 follow-ups on 4-5 pages; track cost, time, playgrounds kept, unwanted changes
  (diff of untouched sections should be empty) and whether the request was done.

## 6. Important decisions

- **Modes:** Playground and Quick read only. Not Playground 2.0 (user, Nov 5).
- **Ambiguous requests ask the reader** with 2-4 options, never a silent guess (user).
- **Jev unsure → Haiku finder**, not a question to the reader (user).
- **Feedback button stays separate** (user).
- **No image input for now** (user).
- **These two modes never take the whole-page Interactive path.** A pick that can't be placed is a `ReaderError`,
  not a rewrite.
- **Cards by code, full JSON only to the editor** (user): cheaper picks, full detail where the change is made.
- **`every` stays Jev-only:** the reader's message is the right instruction for every section, so no Haiku call.
- **`TARGET_ASK_SYSTEM` is new; `TARGET_SYSTEM` is untouched**, so other modes behave as before.
- **✏️ Edit chat hidden** in all modes (`EDIT_CHAT = false`), code kept for v2.

## 7. Cost and speed

- Jev pick: one call, input only (cards + request). The cheapest step.
- Scoped Haiku: Low effort, only the picked sections in, a few targets out.
- pageWide by Jev: no Haiku call at all before the build.
- The build then costs what the targets need: a text edit is one scoped planner call; a widget edit is one
  playground call plus checks.

## 8. Testing

- `server/tests/test_followup_pick.py` (27 tests): Jev questions and decisions, scoped Haiku sees only picked
  sections, fallback, questions, errors, request parsing, pipeline cases (pageWide keeps playgrounds, sliders kept,
  level may change, widget pick edits one playground, remove, checker problems passed in).
- `server/tests/test_revise.py`: Quick read page-wide revision becomes targets; an unplaceable one is an error.
- Live: `node scripts/followup-live.mjs --base <.variety page> --quality playground "<request>"` (real, billed calls;
  API on :8787). Read the Jev score line in the server log to see why a pick was made.

## 9. Known limits

- Jev scores aren't calibrated per topic; a page with many similar sections gets more Haiku fallbacks.
- A pick of more than 6 targets becomes pageWide (`MAX_TARGETS`).
- Clicking an option runs exactly that option's targets; the reader can't add detail to it (they can Cancel and retype).
