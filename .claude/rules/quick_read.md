---
description: Rules for Quick Read mode (lite-plus), the cheapest text-first mode
paths:
  - "server/explainer/pipeline.py"
  - "server/explainer/targets.py"
  - "server/explainer/revise.py"
  - "server/explainer/pricing.py"
---

# Quick Read mode (`lite-plus`)

- Fast and the cheapest mode: a short, mostly-reading page with charts at fixed values.
- **Planning:** its own short prompt (`LITE_SYSTEM`) in the compact format, with `LITE_BUDGET`.
- **Jev:** Jev's page brief runs (level, theme), without the random-process check.
- **Blocks dropped:** after planning, the page loses quizzes, reveal/predict, worked examples, flip cards, experiments, sampling and training blocks (`LITE_DROPPED` in `shared/spec.ts`).
- **No controls, no chatbot:** the spec gets `noControls` (sliders, choices, toggles and presets aren't drawn) and `noAsk` (no "Ask about this" chatbot).
- **Options that don't apply** (`pipeline.py`, `requests.py`):
  - Effort is always **Low**, whatever the composer says.
  - How hands-on is **ignored** (no interactivity hint is sent).
  - There are no Review rounds: the server drops `reviewRounds` and the Options row is greyed out.
  - There's no Thinking option.
  - There's no "What do you want to add to each section?" question (extras are Playground/Quality only).
  - Level and page colours do apply.
- **Follow-ups:**
  - Pages have no widgets, so a "widget" request becomes a section rewrite (`targets._as_blocks`).
  - Text changes are scoped to the targeted sections only (`targets.py`, `revise.py`).
  - A typed revise follow-up stays in Quick read, even if Playground is selected in the composer.
  - Sections are picked first (`followup_pick.py`, see `followup.md`): a whole-page request is a text change of every section, never a full rewrite.
  - A targeted edit is still written with the full planner prompt (`planner_system()`, change format), plus `LITE_EDIT_NOTE` (Nov 5 2026): no quizzes, examples or new controls, and never "move the slider". Dropped blocks are stripped afterwards; sliders are not (charts read their values), only hidden by `noControls`.
- **Retry** works as in other modes: same request, a fresh take, the old page replaced.
