---
description: Rules for Playground 2.0 (playground-2), where Jev picks each playground's parts and Haiku fills them in (beta)
paths:
  - "server/explainer/pgpick.py"
  - "server/explainer/codelab.py"
  - "server/tests/test_pgpick.py"
  - "prompts/prompts.ts"
---

# Playground 2.0 (`playground-2`, beta, Nov 4 2026)

Playground with one change: **Jev decides which parts each playground has, Haiku only fills them in.** Everything else is
Playground's (`playground.md`): marks, cap, checker, autofixes, layout/soft repair, Jev review, storage, runtime.

- **Flow per marked section:**
  1. The planner's `PLANNER_NOTE_PG2` also writes `interaction.needs`: up to 6 short items to change, read or compare. `read_mark` → `pgpick.clean_needs`. No extra call.
  2. `pgpick.pick`: ONE Jev call. State = `gate_state(full=True)` + needs. Questions:
     - one yes/no per tool in `TOOLS` (27: controls, readout, plotted marks, diagram structures)
     - `main_view` (plotted/diagram/both)
     - `controls` score (1 / 2-3 / 4-5)
     - one choice per need (which tool delivers it)
     - `none` (nothing fits)
  3. `pgpick.blueprint` (code):
     - `PICK_IN` 0.7 = in, `PICK_MAYBE` 0.4 = optional
     - the main view always gets a chart; plotted marks go on `c1`, structures on `d1`
     - drag/click go on the main chart
     - each need gets its own control / readout, or the chart that draws it
     - at least 1 control, sliders ≤ `MAX_SLIDERS` (by the score), ≤ 16 components
     - `none` ≥ 0.7 → no blueprint
  4. `_pg_ask` sends `payload["blueprint"]` with `PLAYGROUND2_SYSTEM` (any call whose payload has a blueprint, so repairs too).
  5. `pgpick.merge` (every reply) adds blueprint covers items Haiku left out back to `plan.covers`, so the validator's coverage check reports a dropped need and the one soft repair gets it.
  6. `pgpick.deviations` (on the kept spec, logged + test-run stats only, never fatal): missing ids, changed types, a chart not calling its marks' helpers, missing onDrag/onClick, Haiku's own `plan.deviations`.
- **Fails open:** no key, a Jev error or no usable blueprint → the section is built exactly like Playground (`PLAYGROUND_SYSTEM`), with a "Playground 2.0 (...)" note. Gate-opened unmarked sections have no needs, so only the tool questions are asked.
- **Prompt:** `PLAYGROUND_SYSTEM` is now assembled from shared parts in `prompts.ts`, and stays byte-identical. `PLAYGROUND2_SYSTEM` reuses the intro, "The section" rest, and the whole reference (Spec format → Rules). It replaces:
  - "Your views" + Process steps 3/5/7 → "Your blueprint" + a short Process
  - "Choose freely" → "use the blueprint's components and marks"
  
  Haiku may split one chart into two (first keeps the id) and change a part only with a `plan.deviations` entry. Tests check the reference sections are shared word for word.
- **Follow-ups:** `codelab_edit.page_kind` maps `playground-2` → `playground`, so edits and added playgrounds on a 2.0 page use Playground's path (no blueprint yet).
- **Test runs:** `node scripts/variety.mjs --mode playground-2 --effort low --no-think 1 4 6`. The done event's `labBlueprint` (debugRejected) has needs, picks, optional tools, deviations and whether it was built per section; the saved `.variety/*.json` keeps it.
- **Adding a tool:** one `TOOLS` entry (topic-neutral `what`; `helpers` must be runtime helpers, `test_pgpick` checks it). If it is a new component type, also add it to `ID_PREFIX` and `_ordered`.
