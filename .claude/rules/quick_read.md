---
description: Rules for Quick Read mode (lite-plus), the cheapest text-first mode
paths:
  - "server/explainer/modes/reader.py"
  - "server/explainer/modes/common.py"
  - "server/explainer/modes/followup/**"
  - "server/explainer/pricing.py"
---

# Reader (`lite-plus`; was Quick read)

- Fast and the cheapest mode: a short, mostly-reading page with charts at fixed values.
- **Its own pipeline (Nov 11 2026):** a new Reader page runs in `server/explainer/modes/reader.py`, with shared steps from `modes/common.py`. Nothing under `modes/` imports mode logic from outside it (`server/tests/test_modes_boundary.py`). Its v3 follow-ups run in `modes/followup/` (`run.py`).
- **Planning:** its own short prompt (`LITE_SYSTEM`) in the compact format, with `LITE_BUDGET`.
- **Jev:** Jev's page brief runs (level, theme), without the random-process check.
- **Blocks dropped:** after planning, the page loses quizzes, reveal/predict, worked examples, flip cards, experiments, sampling and training blocks (`LITE_DROPPED` in `shared/spec.ts`).
- **No controls, no chatbot:** the spec gets `noControls` (sliders, choices, toggles and presets aren't drawn) and `noAsk` (no "Ask about this" chatbot).
- **Options that don't apply** (`modes/reader.py`, `requests.py`):
  - Effort is always **Low**, whatever the composer says.
  - How hands-on is **ignored** (no interactivity hint is sent).
  - There's no Thinking option.
  - There's no "What do you want to add to each section?" question (extras are Explorer only).
  - Level and page colours do apply.
- **Follow-ups:**
  - Pages have no widgets, so a "widget" request becomes a section rewrite (`modes/followup/v3/targets.py`).
  - Text changes are scoped to the sections the plan names (`modes/followup/`, see `followup.md`): a whole-page request is a change to every section, never a full rewrite.
  - A typed revise follow-up stays in Reader, even if Explorer is selected in the composer.
  - A targeted edit is still written with the full planner prompt (`planner_system()`, change format), plus `LITE_EDIT_NOTE` (Nov 5 2026): no quizzes, examples or new controls, and never "move the slider". Dropped blocks are stripped afterwards; sliders are not (charts read their values), only hidden by `noControls`.
- **Retry** works as in other modes: same request, a fresh take, the old page replaced.
