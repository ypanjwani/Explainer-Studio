# Follow-ups v4: a tool-using editor (Nov 13 2026; behind `FOLLOWUP_V4=1`)

**Why:** v3 squeezes every request into fixed plan types (`understand.OPS`), rewrites whole sections, then restores what wasn't asked (`targets.guard_untouched`). It had no direct section rename, changed the page title by rewriting stage 1, and its playgrounds could only use the existing marks.

**v4's aim:** Haiku may change anything the reader asks, using small tools that code applies and checks at once. It writes its own blocks when no tool fits, asks a multiple-choice question when a wrong guess would waste the reader's time, answers questions (Reader pages too), and always replies in a friendly way.

Code: `server/explainer/modes/followup/v4/`. With the switch off, v3 runs unchanged.

## Flow (the same two calls as v3, so the app needs no flag)

1. **Talk** (`v4/talk.py`, `POST /api/followup/plan`; a chat turn, not counted as an explainer)
   - `understand.handle` hands over to it when `FOLLOWUP_V4=1`.
   - Undo needs no AI call; neither does a picked option with no added words (the brief is the request plus the choice).
   - Otherwise one Haiku Low call (`FOLLOWUP_TALK_SYSTEM`, format `followupTalk`) reads:
     - the page map (`page_index`)
     - the page text (`outline_context`, cut at 12k)
     - the last 12 turns (8 whole, older cut; `shared.history_text`)
   - It returns one of the v3 `PlanResponse` kinds:
     - `answer` (Reader pages too)
     - `ask`: 2–4 options, as labels only
     - `cannot`, with an alternative
     - `new_page`, shown as `cannot` + `newTopic`
     - `revert`
     - `change`: `{sections: [], steps: [], v4: {brief}, summary: [brief], confirm: false}`
2. **Edit** (`v4/editor.py`, `/api/generate` with `followUp.plan.v4`; counted like any follow-up)
   - `run.generate` keeps the rest of the pipeline: brief, design, assembly, examples, clarity and fact check on changed sections, notes. Only `run_plan` is swapped for `v4_editor.run`.
   - **Loop:**
     - Haiku runs with Thinking per the page's setting.
     - The system prompt is `FOLLOWUP_EDITOR_SYSTEM` (Explorer) or `FOLLOWUP_EDITOR_LITE_SYSTEM` (Reader); each ends with that mode's block guide (`fieldGuide`).
     - The first user turn is cached: the brief, the reader's words, and the page overview (playgrounds shown as `{type: widget, title}`).
     - It stops when the editor calls `finish` or `ask_user`, or no tool. After `MAX_ROUNDS` = 12 rounds, or once spend passes `FOLLOWUP_MAX_USD`, tools switch off.
   - **Tools** (`v4/tools.py`): each call is applied to `v4/page.Page` and checked. A refused call (`ToolError`) goes back to Haiku as a sentence it can act on.
     - read: `get_section`, `get_playground`, `search_source`
     - page: `set_page_meta`, `set_level`
     - sections: `rename_section`, `edit_text` (exact, must occur once), `set_blocks`, `add_block` / `edit_block` / `remove_block`
     - structure: `add_section`, `remove_section`, `move_section` (split = add + edit; merge = set_blocks + remove)
     - playgrounds (Explorer only):
       - `patch_playground`: path/value sets into the spec (a list item by index or id), then `check_playground`
       - `replace_playground`, through `playground_edit.edit_widget` (builder, checker, repairs, Jev review)
       - `add_playground`, through `add_widget` (cap `MAX_MARKED`)
       - `remove_playground`
     - talk: `ask_user` (refused after any write), `finish`
   - **Blocks** go through `parse_block` + `check_edit`.
     - A type the mode doesn't allow (Reader: `LITE_DROPPED`, controls, chart, scene, widget; Explorer: `explorer.stream.PREBUILT`) is refused unless the section already holds that type.
     - A sanity problem the section already had elsewhere on the page doesn't block the edit.
     - Widgets: the editor keeps each playground's `{type: widget, title}` entry and code puts the real block back. It never writes one itself.
   - **After the loop:**
     - **Asked:** nothing was written, so the run raises `Asked`: `ASKED_PREFIX` (`"? "`) + compact JSON with option labels, under 1000 characters.
       - `history._apply_generation` and the app (`askedOf`) turn it into `status: picking` with `plan: {kind: ask}`.
       - It counts as a failed run, so it isn't one of the day's explainers.
     - **Jev check:** each changed section (≤ `CHECK_MAX`) gets `check.judge`. A failure puts that section back alone (a new section is removed); its log entries become `undone`.
     - **Agreement:** a playground changed without its text gets `playground_edit.agrees`. A mismatch is a note for the reply.
     - **Reply** (`v4/reply.py`, `FOLLOWUP_REPLY_SYSTEM`, Haiku Low, no thinking), written from the change log, so it never claims an undone change. A plain reply by code if the call fails. Stored as `spec.revision.reply`.
     - **Nothing changed:** `steps.Checked` with the reply, a calm reply in the thread (not counted).
   - **Report:** `spec.revision.steps` holds one `StepReport` per logged change, with a `section` id. The app shows the reply plus chips; a chip on a section links to `#/c/<id>/x/<section>`.

## Playground `table` mark

`table(columns, rows, {label, digits, highlight: {row, col}})`, so a playground can show comparisons that update with the controls.
- Up to 8 columns and 12 rows; cells are numbers or text up to 40 characters.
- Code: `structures.tables`, `components.cjs`, `sandbox.cjs` (shape check), `validator.cjs` (click probes, labels for Jev), CSS `.st-thead` / `.st-thtext`.
- Prompt: `PLAYGROUND_SYSTEM` (views list and helper line). Tests: `tests/playground.test.ts`.

## Phase 3: a playground's own drawing (Nov 13 2026)

When no built-in view (chart, diagram, graph, tree, matrix, sequence, grid, table) can show what the reader wants, the editor sets `custom: true` on `replace_playground` / `add_playground`.
- `tools._builder_what` adds `PLAYGROUND_DRAWING_NOTE` to the builder's instructions; the builder, checker, repairs and Jev's review run unchanged. The log says "with its own drawing".
- The builder may then return a **`drawing` mark** from a chart's `draw`: `drawing(items, {w, h, label})`, items made with `svgEl(tag, attrs, text)`.
- **Not raw SVG/HTML:** only rect, circle, ellipse, line, polyline, polygon, path and text, each with its own attributes plus fill / stroke (palette, `none`, `#hex`), widths, opacities, dasharray and a numbers-only transform. A `d` holds path commands and numbers only. There are no styles, links, images or `url()`, and at most 300 items. `sandbox.checkDrawing` refuses anything else, naming it. `structures.drawings` rebuilds each element from the whitelist with every value escaped, in a nested svg with the drawing's own viewBox.
- It is a mark like any other, so the dry run ("every control reaches a view"), readings (`readings.cjs`: shapes, words and the first shapes' numbers), Jev's text-vs-readings check, facts labels and click probes all apply.
- `patch_playground` can write one directly; it goes through the real checker.
- New Explorer pages don't get it: `PLAYGROUND_SYSTEM` is unchanged, and its helper-line test leaves out `drawing` and `svgEl`.
- Tests: `tests/playground.test.ts` ("drawing mark"), `tests/readings.test.ts`, `server/tests/test_followup_v4.py` (custom note, real checker).

## Not done (later)

- An undo tool for a named earlier change. Undo stays one version per follow-up.
- A confirm card before big v4 edits; Undo covers it for now.

## Tests

- `server/tests/test_followup_v4.py`: the tools, the loop with a fake streaming client, ask/Checked, the Jev undo, the reply, talk, and the route switch.
- `tests/playground.test.ts`: the table mark.
- Live: `FOLLOWUP_V4=1 npm run dev`, then `node scripts/followup-live.mjs ...`; compare with the switch off.
