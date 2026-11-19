---
description: Rules for follow-ups (revise / new / Retry) - a follow-up edits only the parts it is about, never rewrites the page
paths:
  - "server/explainer/followup.py"
  - "server/explainer/modes/followup/**"
  - "server/tests/test_steps.py"
  - "server/tests/test_understand.py"
  - "server/tests/test_section_editor.py"
  - "server/tests/test_page_index.py"
  - "server/tests/test_modes_route.py"
  - "server/tests/test_modes_boundary.py"
  - "server/explainer/core/followup.py"
  - "server/explainer/routes/ai.py"
  - "shared/followup.ts"
  - "app/src/shell/app.ts"
  - "app/src/api.ts"
  - "scripts/followup-live.mjs"
  - "server/tests/test_retry.py"
  - "server/tests/test_followup_v4.py"
  - "server/tests/test_readings.py"
  - "shared/playground/readings.cjs"
  - "tests/followup.test.ts"
---

# Follow-ups

## v3 and v4 are kept apart (Nov 13 2026), so v3 can be deleted without touching v4

`server/explainer/modes/followup/`:
- `entry.py`: `POST /api/followup/plan` (`parse_plan_request`, `handle`). Picks `v4/talk` under `FOLLOWUP_V4=1`, else `v3/understand`.
- `run.py`: `/api/generate`. Picks `v4/editor` for a plan with a `v4` brief, else `v3/steps.run_plan`.
- `v3/` (`understand`, `steps`, `section_editor`, `targets`) and `v4/` (`talk`, `editor`, `tools`, `page`, `reply`).
- Shared by both: `shared.py` (`Checked`/`CHECKED`/`CHECK_MAX`, `max_usd`, `is_undo`, `history_text`, request limits), `page_index.py`, `check.py`, `playground_edit.py`, `revise.py`, `cards.py`.

**The rule:**
- Only `entry.py` and `run.py` name both versions, and each imports v3 only inside its v3 branch.
- `v4/` never imports `v3/` (or the other way round), and the shared files import neither.
- Something both versions need goes in a shared file, never in one version imported by the other.

`server/tests/test_modes_boundary.py` checks this both statically and by running the whole v4 suite with `v3` unimportable.

**Deleting v3:**
1. Remove `v3/`, the v3 branches in `entry.py` and `run.py`, and the `FOLLOWUP_V4` switch.
2. Remove v3's tests (`test_understand`, `test_steps`, `test_section_editor`, `followup_v3_data` if unused) and the v3 cases in `test_modes_boundary`/`test_modes_route`.
3. Remove the prompts `UNDERSTAND_SYSTEM` and `SECTION_EDIT_NOTE` (also from the found-prompts assert in `test_modes_boundary`), then run `npm run export:server`.

## Follow-ups v5 (Nov 16 2026; the default since Nov 19 2026, on unless `FOLLOWUP_V5=0`, and it wins over `FOLLOWUP_V4`)

Design: `.claude/followup-v5-plan.md`. Code: `server/explainer/modes/followup/v5/`. **Stands alone:** never imports
`v3/` or `v4/` (`test_v5_runs_with_v3_and_v4_deleted` runs `test_followup_v5.py` with both blocked); v4's editor
pieces were copied in, not shared.

**One agent (phase 4, Nov 16 2026).** There is no talk step and no brief any more: one Haiku agent answers, asks, changes
and writes the reply, so it works from the reader's own words and the conversation.
- `talk.py` (`/api/followup/plan`): **code only, no AI**. "undo" → `revert` (`shared.is_undo`, which also takes fillers
  and "the/those changes": "so revert the changes", Nov 17 2026; a message naming a part still goes to the agent); anything else → a non-confirm `change`
  with `v5 = {agent: true, conversation?, chose?, extra?, earlier?}` (the conversation via `shared.history_text`, the
  option the reader picked, the answer just before via `shared.last_answer`). The app runs it at once (`runSteps`).
  `followup.parse_plan` keeps these, and still reads an older plan's `brief` / `base` / `refs` (the agent gets them as text).
- **Does the message ask for a change? (`v5/intent.py`, Nov 17 2026).** The plan step asks Jev one yes/no question
  (`wants_change`: the message, the turn before it, a picked option), with no examples in it. At or above `CHANGE_P`
  (0.6) → `v5.intent = "change"`; below → `"answer"` (an unsure call counts as only asking: a wrong "no" costs the reader
  one "yes", a wrong "yes" a version they didn't want). A picked option is `"change"` without a call; Jev failing or no key
  → no intent (the agent decides). On `"answer"` the agent runs answer-only (`Ctx.no_writes`, every write and
  `start_from` refused) and offers its fixes as suggestions. The agent prompt also says to change the page only when the
  reader asks for a change or says yes to an offered one. Cause: the agent edited on "can you check …" and "anything to
  change?" (it holds the edit tools and had found something to improve).
- `run.py`: a `v5` plan loads its base on the server (`v5/base.load`, `follow_of` = a port of `buildFollowUp`), skips
  Jev's page brief, and calls `v5/agent.run` with a `Store` over the line. `run.is_agent(req)` names such a run.
- `agent.py`: first message = mode, the base version's main file (or the page without the store), the version log, the
  full text when ≤ 24k (`FOLLOWUP_V5_FULLPAGE`, on unless `0`), the paper the page was made from, the conversation, the
  reader's message and option. Prompts `FOLLOWUP_V5_AGENT_SYSTEM` / `_LITE_SYSTEM` (`v5AgentIntro` + `fieldGuide`).
  - **The whole paper (Nov 18 2026):** a page's own source (`chatbot.context`, `follow.source`) is only the summarize
    step's summary, which drops most results tables: the agent told a reader five times "the paper doesn't give the
    LSTM / BiLSTM / mBERT scores" while the PDF's tables had every row. `base.paper_of` reads the paper's sections from
    the line's first page record (`request.sections`; a Retry's record first, via the line's `new` versions), so nothing
    new is stored and old pages get it too; `[]` for a concept page. `agent.paper_part` puts it in the first message after
    `PAPER_NOTE` (take numbers from it; never say the paper lacks something without looking): whole up to `PAPER_CHARS`
    (60k characters, ~15k tokens), else the first sections that fit, whole, plus the names of the rest. `search_source`
    then searches the whole paper instead of the summary. `follow.source` is unchanged, so a new version's chatbot
    context stays the summary. Jev's checks still read the summary.
  - **Budget by Effort** (`BUDGET`): Low 20 rounds / $0.06, Medium 30 / $0.12, High 40 / $0.20 (rounds were 10 / 20 / 30
    until Nov 17 2026: "fix all" ran out on Low; the $ cap is the real limit); Reader pages always
    Low; `FOLLOWUP_MAX_USD` overrides the cap. At 80% a wrap-up note; at the end tools off and its text is the reply.
  - **A reply cut off at `EDIT_TOKENS` (32k, was 16k; Nov 17 2026)** no longer fails the run (which lost every change
    already made): the cut reply is dropped (its last tool call is incomplete, never applied or sent back) and the agent
    is told `CUT_OFF` (smaller steps). `MAX_CUTS` (2) in a row end the run with what's done, checked as usual, and a
    note in the reply; with nothing done yet, a plain `ReaderError` (`TOO_LONG`). A refusal still fails (`check_stop`).
  - **No mid-word cuts (Nov 17 2026):** text a reader or the agent reads is cut at a sentence or word (`page.cut`), and the
    read tools return sections, playgrounds and changes whole up to 40k with a plain "cut here" note past it (`page.whole`;
    were 14k, cut silently mid-JSON). Limits raised: questions 600 / options 200 (`editor.QUESTION_CHARS` /
    `OPTION_CHARS`; a picked label keeps 200 in `entry.py`, `talk.py`, `followup.parse_plan`), builder instructions 5000
    (`tools.BUILDER_CHARS`), a rewritten reply 3000 characters / 2000 tokens (`reply.py`), the version log's replies 800
    and offers 400 (`base.py`).
  - **Effort = the reader's Effort from the first call (Nov 19 2026; Reader pages: Low).** It used to start Low and step
    up at the first write, so all the reading and judging (which sections to open, which playgrounds to run) was Low, and
    a check-only message ("check the whole page") never left Low. Thinking = the page's setting (off on Reader pages).
    Neither changes mid-loop (a thinking or effort change invalidates the prompt cache). The after-edit review stays Low.
  - **Jev checks numbers only (Nov 17 2026):** each changed section's text against its playground's readings
    (`readings_contradict`). It no longer judges whether a change did what the reader asked (`check.judge`): it saw only
    the last message, so after "fix those" it undid the fixes the reader had agreed to ("the change didn't deal with what
    you asked for"), and they came back as issues the next turn. The agent, which reads the conversation, decides that.
  - **Checks are feedback:** the first `finish` after a change runs `editor.problems` (Jev's numbers check) and
    `review.review`; problems → `finish` refused once with the list. `check_changes` runs the same on demand. Nothing is
    put back there.
  - **Final guard** (`Gate.guard`): Jev's numbers check again on sections changed since the checks or failing then
    (`editor.check_changes` puts back each the change made contradict its playground). A review problem the agent left
    alone becomes a note in the reply (`Still to fix in "…"`), **never a put-back** (Nov 17 2026: putting its cause back
    undid fixes the reader asked for; `editor.put_back` was removed). When the guard undid anything, the reply is
    rewritten from the log alone (`reply.write` with no editor note: the agent's own reply claimed a revert that had been
    put back); with only notes, from the log plus the agent's reply; otherwise it is the agent's own `finish.reply`.
  - **"Yes, make these fixes" (Nov 17 2026):** when the message only asked (intent "answer", not past the day's
    explainers) and the agent's answer lists fixes, it sets `finish.offers_fixes`; the answer ends with `OFFER = "✓+ "`
    (`shared.OFFER` = `history.OFFER` = `OFFER_PREFIX`), stored as `answer` + `offersFix`. The app shows a button under it
    (last message only) that sends "Yes, make the fixes you listed." as a picked option (`acceptFixes` → `submit(…, option)`
    → `planFirst`), so the plan step reads it as a change without Jev, and the agent gets the answer as `v5.earlier`.
    The agent prompt also says: list only problems confirmed by reading or a playground's numbers, never guesses.
  - **Ends:** a change → the outline + revision (`suggestions`: the agent's own ideas only, ≤ `MAX_SUGGESTIONS` 2, each
    ≤ 600 characters cut at a sentence, none already offered (`fresh`: word for word in the conversation or the version
    log, `Store.offered`), under "Ideas, if you want more:" in the reply, and in the version log).
  - **Why so few ideas (Nov 17 2026):** the review used to add up to 5 "could be better" ideas under every reply as
    "Something else I noticed", so every "fix all" ended with a fresh list. The review now reports only what a change
    broke, and the agent prompt says: at most 2, none after "fix those", never one offered before, and a problem it couldn't
    fix goes plainly in the reply. In answer mode the fixes it would make are listed in the reply itself. No change → `shared.Checked` (an answer, ≤ `MESSAGE_CHARS` 6000: it travels in the generation's error, `Text`
    since migration 0012; at `String(1000)` answers were cut mid-word). `ask_user` before any write → `editor.Asked`. `new_topic` → `shared.NewTopic` (`NEW_TOPIC = "↗ "`;
    `history._apply_generation` and `app.ts` show it as `cannot` + `newTopic`, with the "New explainer" button).
    **Since Nov 19 2026 `new_topic` takes a `brief`** (the tool is `modes/topic.new_topic_tool(under_page=True)`, shared
    with the first-message step): the message is then `NEW_TOPIC_BRIEF = "↗+ "` + JSON `{reply, brief}`, stored as
    `plan.brief`, and `newFromPick` builds from the brief (`direct: true`), not the reader's raw words. The prompt says
    AI topics only: a non-AI topic is answered with `finish`, never `new_topic`.
- `tools.py`, `page.py`: v4's, plus `version` on `get_section` / `get_playground` / `try_playground`, `get_overview`,
  `get_change`, `search_page`, `restore_section`, `restore_playground`, **`start_from(version)`** (only before any write:
  `Page.rebase` onto that version; `start_from` + `finish` = going back to it), `check_changes`, `new_topic`,
  `finish(reply, suggestions?)` (the last three applied by `agent.py`, `END_TOOLS`); new ids avoid `versions.used_ids(line)`.
- **Answer-only past the day's explainers:** `routes/ai.py` starts a v5 agent run even then (`quota.start_generation(...,
  allow_over=True)`, `quota.over_daily`) with `answer_only`: every write and `start_from` is refused, answers work, and a
  run without a page isn't counted.
- `review.py`: the after-edit review (`report` tool, `followupV5Review`), skipped only for a title-only or look-only
  change; the agent runs it (no fix round of its own any more). It reports only what the change broke elsewhere on the page
  (Nov 17 2026): no ideas, and no judging of the request (it sees only the last message).
- **Looking at playgrounds (Nov 18 2026, `FOLLOWUP_V5_LOOK`, on by default since Nov 19 2026, `0` turns it off; Explorer pages only)** — `v5/look.py`.
  Otherwise the agent sees a playground only as text (readings, layout check), so it can't judge how it looks.
  - Tool `look_at_playground(section, title?, version?)`: screenshots (`explorer.playground.snap_widget`, at most 3: the
    start, a slider at its ends) in the **page's own colours** (`page.look_theme` = `page.look_theme_of(follow)`: dark only
    when the page is set to dark, plus its palette; `start_from` resets it), each with its caption, then the readings
    (numbers come from readings, never from pixels). When to use it is in the tool description (no prompt change). At
    most `LOOKS` per message by the reader's Effort (Low 1, Medium 2, High 3); frames cached by html + colours. No
    browser → the readings alone (fails open). A frame is ~640×1200 px, ~1k tokens.
  - Only the newest screenshots stay in the conversation (`strip_old_images`, before a round's new ones go in).
  - The gate (`Gate.run`) also screenshots each playground the agent changed and `look_review` (Code Lab's judge, told
    `PG_LOOK_CHECKED`) lists what looks wrong, at the reader's Effort: `kind: "look"` problems in the same feedback.
    Judged once per html (`page.look_judged`). **Never a put-back or a note:** one left alone becomes at most one
    suggestion (`Gate.look_offers`, offered before the agent's own).
  - `scripts/snap-widget.mjs` takes JSON `{html, mode, palette, maxFrames}` (raw html still works) and reads the colours
    from `app/src/styles/tokens.css` and `themes.css` (copied into the Docker image), layered like the page; a `custom`
    palette falls back to the default one.
- History: `version_meta` uses an older plan's `plan.v5.base` as parent; for the agent, a `done` event with `startFrom`
  (from `run.generate`) sets the parent and `madeFrom` before the version is saved (`history.generation_event`). The
  version records `under` (the version the reader was on), and **undo goes back to `under`**, else to the parent.
- `test_the_whole_generate_path_goes_back_to_an_older_version` runs `run.generate` end to end (`start_from(1)`, an edit).
- Tests: `test_followup_v5.py`; boundary: `test_modes_boundary.py`.
- **Deleting v3 or v4:** remove its folder and its branch in `entry.py` / `run.py`, its tests and its prompts; v5 keeps
  working.

## Version store (follow-ups v5 phase 1, Nov 16 2026; every follow-up path uses it)

Design: `.claude/followup-v5-plan.md`. Code only, no AI, no flag.
- `server/explainer/versions.py` (base code, not under `modes/`; `history.py` calls it). Tables `page_versions` and
  `page_lines` (migration 0010); `Generation.meta`.
- **The server numbers versions, per page line.** A line is a page and its revisions; a new explainer or a new-topic
  page starts one (v1). `page_lines.next_version` only grows, so a number is never reused.
- **What makes a version:** a generation that finishes with a page (`history.generation_event` on `done`, inside a
  savepoint, so a store failure never loses the page). The number goes into the `done` event and the record
  (`version`; a page's first version shows no number, as before: `history.shown_version`). Answers, questions,
  `cannot`, `✓ ` Checked and `? ` Asked make none.
- **Every save takes the record's number from its version row** (`history.save`), so a stale tab or a renumbered
  backfill can't put an old browser number back.
- Retry / Edit: a revision takes over a version of its own line; a new page only a line's first page. A replaced
  page's section ids stay reserved (merged into `section_ids`).
- `/api/generate` takes `baseId` (the record a revise changes) and `replaces` (Retry / Edit keep that version's number);
  a stale tab that sends neither gets them from the stored record. `history.version_meta` decides line, parent, kind.
- Undo / revert: `POST /api/explainers/<id>/revert {baseId, to?}` (`history.revert_version`), a new version made by
  code; the app no longer copies the page itself. Checked against the 200 MB cap (records + versions) before saving.
- Delete = tombstone (page cleared; number, parent, log and section ids kept). Account delete cascades.
- Every version is a full gzip copy of canonical JSON, checked on save to unpack to exactly the page. Sizes count
  toward the 200 MB cap.
- Pages saved before the store are registered lazily (`versions.backfill_line`) with the app's numbers; a duplicate
  number is renumbered and written back to the record.
- `generations.spec` is cleared once the record holds the same page (`history._clear_applied`, in `generation_event`,
  `settle` and `save`); `done` with an empty spec means "already applied".
- Reading (for v5): `load`, `of_record`, `used_ids`, `version_log`, `change_of` / `diff`, `search_page`.
- Tests: `server/tests/test_versions.py`.

## Main file (follow-ups v5 phase 2, Nov 16 2026; nothing calls it yet outside tests)

`server/explainer/modes/followup/main_file.py` (a shared follow-up file): a version's short description for Haiku to
find what is where. `main_file_of(db, client, user_id, line, version, usage)` returns the saved one or builds it.
- Per section: code part (`section_code`: `S3 · id`, label, blurb, one line per block, quiz questions, playground
  controls with ranges and defaults, what it shows and teaches), readings at the start (`section_readings`, no AI,
  fails open) and AI part (`MAIN_FILE_SYSTEM` / format `mainFileNotes`, Haiku Low without thinking: introduces, uses,
  claims; each section described on its own).
- Cached per user in `section_notes` (migration 0011) by sha256 of `MAIN_FILE_FORMAT` + the section's canonical JSON:
  only new or changed sections get readings and the AI call. Notes unused for 60 days are deleted on the user's next
  build; account delete cascades.
- Saved gzip JSON in `page_versions.main_file` once every section has its AI part and its readings (`complete`); a
  saved main file in an older format is rebuilt. Its cost goes on the caller's `UsageLog`.
- **No endless retries:** the AI part goes `AI_BATCH` (8) sections per call; a batch that answers covers every section
  in it (a skipped one gets an empty AI part, `NO_AI`, and is never asked about again); only a failed batch is retried.
- **No kept failures:** a playground run that fails (no node, a timeout, a crash) gives readings `None`, which is never
  kept: the run is tried again next time, and the main file isn't saved until it works.
- `handle_of(main, ref)`: a handle or id resolved against this version's main file only.
- Tests: `server/tests/test_main_file.py`.

## Follow-ups v4: a tool-using editor (Nov 13 2026, behind `FOLLOWUP_V4=1`; v3 below stays the default)

Design and file map: `.claude/followup-v4-plan.md`. Code: `server/explainer/modes/followup/v4/`.
- `talk.py`: `/api/followup/plan`, used by `entry.handle` under the switch. Returns v3 `PlanResponse` kinds; a change is one `v4.brief`.
- `editor.py`: Haiku with tools in place of v3's `steps.run_plan`, branched in `run.py` on `plan.v4`.
- `tools.py`, `page.py`: tools applied and checked by code; the change log.
- `reply.py`: the friendly reply, `spec.revision.reply`.

**Playground readings (Nov 13 2026):** the AI sees what a playground really shows, not only its code. `explorer.playground.read_playground` runs `scripts/read-playground.mjs` (`shared/playground/readings.cjs`), which gives each readout's value and a summary of each chart. The default settings are the start, each slider's ends, the options, Play and button presses. It's seeded, and fails open.
- `talk.py` adds them for every playground on Explorer pages, so "is this playground right?" is answered from the numbers.
- The editor has `try_playground`. Every playground write (`patch` / `replace` / `add`) answers with the new readings plus Jev's text-vs-readings check (`explorer.judge.readings_contradict`, yes/no only). A text change with a number beside a playground gets a reminder.
- `check_changes` checks the first `CHECK_MAX` changed sections (no longer none when more changed). Each one with a playground gets the readings check: a contradiction the change brought in puts the section back; one already there stays as a note for the reply.
- **History (Nov 13 2026):** the app sends the last 12 turns whole (`historyOf`: the prompt, a whole answer, a change's whole reply plus every step, kept or undone with why). `shared.history_text` keeps the last 8 whole (up to 8000 characters each, `HISTORY_CHARS`), cuts older ones to 300 and drops the oldest past 24,000 characters in all (Nov 17 2026: was 4000 / 15,000, which cut the end of a 6000-character v5 answer's fix list; `EARLIER_ANSWER_CHARS`, `followup.parse_plan`'s `earlier` / `conversation` caps and `v5/intent.PREVIOUS_CHARS` follow). It was 500 characters a turn (answers 240), which cut off what "fix that" pointed at.
- **The talk step reads the whole page (Nov 13 2026):** `cards.page_text` (every block in full: table rows, tabs, compare, flip cards, reveal, quiz/predict with answers, concept map, and the card variant, e.g. "common mistakes: each title is the wrong belief"), cut at `PAGE_CHARS` = 24k. It was `common.outline_context` (text, callouts, formulas and cards only), so table rows were seen only as the page map's first 120 characters. Readings: `READINGS_CHARS` = 2500 a playground (was 1200).
- **"Fix those" (Nov 13 2026):** `FOLLOWUP_TALK_SYSTEM` tells the talk step that the editor never sees the conversation, so a brief spells out every item (section, part, what's wrong, numbers), never "the issues named earlier". When the reader's previous message got an answer, `talk.plan` adds it as `v4.earlier` (`shared.last_answer`, ≤ 4000 characters; kept by `followup.parse_plan`), and the editor gets it as `earlier_answer` next to the brief, only to see what the brief refers to. Jev's check still judges against the brief alone.
- Eval: `server/evals/playground_readings_eval.py` (`--no-jev` is free; `--id <thread>`).
- Tests: `test_followup_v4.py`, `test_readings.py`, `tests/readings.test.ts`.

**Phase 3 (Nov 13 2026):** `replace_playground` / `add_playground` take `custom: true` when no built-in view can show what the reader wants: the builder gets `PLAYGROUND_DRAWING_NOTE` and may draw that part with the whitelisted `drawing` mark (`shared/playground/`).

The editor's `ask_user` ends the run with `Asked` (`ASKED_PREFIX`), which is shown as a question and not counted. Each changed section is checked by Jev and put back alone if it fails. The app shows the reply only (the chips per change were removed Nov 19 2026; `revision.steps` is still stored). `FollowUpPlan.v4` is parsed in `server/explainer/followup.parse_plan`. Tests: `server/tests/test_followup_v4.py`.

## Follow-ups v3: understand → section editor (Nov 9 2026; the only follow-up path since Nov 11 2026)

**Where it lives (Nov 11 2026):** `server/explainer/modes/followup/`, Reader and Explorer's own follow-up path; nothing there imports another mode's code (`server/tests/test_modes_boundary.py`).
- `v3/understand.py`, `v3/steps.py`, `v3/section_editor.py`, `v3/targets.py`: v3's own code (in `v3/` since Nov 13 2026).
- `page_index.py`: the page map.
- `run.py`: the run once the plan is chosen, `/api/generate` via `modes.handles`.
- `playground_edit.py`: playground edits and adds, built on `modes/explorer/`.
- `revise.py`, `check.py` (Jev's after-edit check), `cards.py` (section cards): shared with v4.
(These began as copies of the old modes' code, deleted on Nov 11 2026.)

The app shows a failed plan call as an error with "Try again". The server runs a revise follow-up only when it carries a plan (`modes/followup/run.handles`); anything else (an old app tab, a follow-up on an old mode's page) is refused with `mode_off`.

Design: `.claude/followup-v3-plan.md`. A typed revise follow-up on a `playground` or `lite-plus` page goes to `POST /api/followup/plan` first.
- **Understand** (`understand.py`, `UNDERSTAND_SYSTEM`, format `followupUnderstand`, Haiku Low; reads the page map `page_index.map_text` and the conversation's earlier turns with what each did, see History above): `change` / `ask` / `cannot` / `answer` / `new_page` / `revert`. A change is **sections with the reader's intent** (`{section, intent, quote}`, never "edit the text/playground") plus **typed structural steps** (`OPS` = `PLAN_OPS` in `shared/spec.ts`, a test checks): add / remove / move / split / merge a section, set_level / set_tone / set_length, edit_page_meta, revert. Shortcuts with no AI call: undo, "remove section N".
- **Section names and quotes (Nov 10 2026):** the map names sections by **handle** (`S1`, `S2` …, never ids); `resolve_ref` turns what Haiku wrote into the id by code (handle, a number in any form, `after: start/end`, exact or partial label). Only how a section was written is fixed, never meaning: two possible sections ("the last one" = last section or last playground, a shared label) → `which` asks the reader with one option per section (no AI call); none → "Skip that part, do the rest", or an error naming it. Fixes are logged and returned as `fixes`. **Nothing is dropped for its quote:** `flag` marks a part `unsure` when its quote isn't in what the reader said (`quote_ok`: any script, rewording, numbers, earlier messages) or names another section; an unsure part forces the plan card, where an unsure section part has a box to untick. `steps.run_plan` strips the note.
- **Knock-ons** (`understand.knock_ons`, from the page map, `derived` + `because`): mentions of a removed / moved / merged section, ideas only a removed section explained. **Budget** (`estimate_usd` vs `FOLLOWUP_MAX_USD`, default 0.15): over it → ask with cheaper options (text only, or the first sections). **Confirm** (`needs_confirm`): remove/merge, level, > 4 sections, > 2 knock-ons, any part marked unsure.
- **Page map** (`page_index.py`): code part (cards, references found by code) + one Haiku Low call (`INDEX_SYSTEM`); cached in the `page_index` table by page hash (migration 0009).
- **Run** (`steps.run_plan`, via `run.py`): structure first (moves and a merged section's playground by code; new / split / merged sections and the page title by the page planner via `apply_targets`; removals by code; a new section's playground via `gate_one`). Then every intent (a page-wide step = an intent on every section, plus the playground's level when it doesn't match) goes to the **section editor** (`section_editor.py`): evidence by code + Jev (section, playground spec, checker problems, text↔playground agreement, level, neighbours), then one Haiku call **with Thinking** (system = the page planner prompt, cached; `SECTION_EDIT_NOTE` + `PLANNER_NOTE` or `LITE_REVISE_NOTE` in the user turn) that decides `change`/`none`, a new stage and a playground action (keep / edit / add / remove + instructions). Text merged with `parse_revision` + `guard_untouched` (a stage with no usable blocks is rejected); playgrounds by `playground_edit` (checker first, repairs, Jev review). A section still naming a removed one gets one more pass. `check.check` per changed section (≤ 6): one retry through the editor, then that section alone is put back.
- **Report**: `spec.revision.steps` done / kept (the editor's reason) / undone / skipped. Every section checked and right → `steps.Checked`: a message starting `✓ ` (`CHECKED_PREFIX`) that `history._apply_generation` stores as a reply (`status done`, `answer`, no spec) and the app shows as one; not counted. Nothing changed for another reason → `ReaderError` with the reason.
- **App** (`app.ts` `beginFollowUp` / `matchFirst` / `planFirst` / `planView` / `newFromPick` / `runSteps` / `historyOf` / `revert` / `undoVersion`; status `picking`, `rec.plan`): **no router (removed Nov 13 2026; it was `POST /api/route`, `ROUTE_SYSTEM`)**. Every message under a Reader or Explorer page goes straight to the plan step; a new paper, or a page made with a retired mode, is a new explainer. Shown: question with options + "Add detail", the plan card, `cannot` with its alternative, a new topic (`cannot` + `newTopic`) with a "✨ New explainer about this" button (`newFromPick`: the composer's mode and options, never automatic, so a wrong guess spends no explainer), answers and checked replies in the thread (`rec.answer`), Undo (`rec.reverted`, no AI call, not counted).
- **Tests:** `test_understand.py`, `test_section_editor.py`, `test_steps.py`, `test_page_index.py` (data in `tests/followup_v3_data.py`). **Eval:** `server/evals/followup_plan_eval.py` (+ `followup_plan_extra_cases.json`; the part isn't scored). **Live:** `followup-live.mjs`.

## Where the code lives

- **Entry:** `server/explainer/followup.py` (validating `FollowUp`); `modes/followup/entry.py` (`POST /api/followup/plan`); API routes in `routes/ai.py`.
- **Follow-ups v3:** `server/explainer/modes/followup/v3/`: `understand.py` (what the follow-up asks), `section_editor.py` (evidence + the editor), `steps.py` (running a plan), `targets.py` (apply a section's change, `guard_untouched`); shared in `modes/followup/`: `run.py` (the follow-up's run), `page_index.py` (page map + `page_index` table), `revise.py` (restore what's kept: `restore_kept_widgets`), `playground_edit.py` (playground edits/adds through Explorer's checks, `modes/explorer/`), `check.py` (Jev's after-edit check), `cards.py`; route `/api/followup/plan`; prompts `UNDERSTAND_SYSTEM`, `SECTION_EDIT_NOTE`, `INDEX_SYSTEM`, `LITE_REVISE_NOTE`.
- **Shared rules:** `shared/followup.ts` and its Python port `server/explainer/core/followup.py`; keep them identical (`npm run parity`).
- **Browser:** `app/src/shell/app.ts` (follow-ups, Retry `retryFresh`/`settleRetry`, polling), `app/src/api.ts`.
- **Live test:** `node scripts/followup-live.mjs --base <.variety page> --quality <lite-plus|playground> "<request>"` (needs the API on :8787; real, billed calls; always plans first).
- **Design notes:** `.claude/followup-v3-plan.md` (understand → section editor). `.claude/followup-planning.md` describes the older section pick (removed Nov 11 2026) and is kept as history.
