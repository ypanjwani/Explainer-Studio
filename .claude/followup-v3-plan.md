# Follow-ups v3: understand → inspect → section editor → validate → verify

Status: built Nov 9 2026 behind `FOLLOWUP_PLAN=1` (off by default; off = the pick in `.claude/followup-planning.md`).
Not committed. Explorer (`playground`) and Reader (`lite-plus`) only. Replaces v2 (same day), which made the first call
pick a fixed in-section op (edit_text / edit_playground / fact_check …) before anything had looked at the section.

## Why

A follow-up like "check the last playground; if it's right leave it, otherwise fix it" can't be routed before looking:
it could need the text, the playground, both, or nothing. v2 chose `fact_check` (text only), Jev found the text fine,
and the reader got "Something went wrong". The text-versus-playground choice was made too early, by a call that never
saw the content, and could not be revised.

v3 decides **where** early and **what** late: the first call only names the sections and what the reader wants in each;
an editor that sees the full section, its playground and code-gathered evidence decides what to change.

## Flow

```
reader text + the conversation's earlier prompts (with what each did)
 1 UNDERSTAND (understand.py, Haiku Low, page map in; shortcuts: undo, "remove section N")
     change {sections: [{section, intent, quote}], steps: [structural]} | ask | cannot + alternative | answer | new_page | revert
     + knock-on intents from the page map (mentions of removed/moved/merged sections, ideas only a removed section explained)
     + budget check (over FOLLOWUP_MAX_USD → ask with cheaper options) + confirm for big/destructive plans
 2 INSPECT (section_editor.evidence, code + Jev): the section (widgets as placeholders), the playground spec, the
     checker's problems, text↔playground agreement, the playground's level, neighbours
 3 EDIT: structure first (steps.py: moves/carried playgrounds by code; new/split/merged sections and the page title written
     by the page planner via targets.apply_targets; removals by code; a new section's playground via the marker gate).
     Then one SECTION EDITOR call per section (Haiku + Thinking, side by side; system prompt = the page planner's, cached):
     {decision change|none, reason, stage|null, playground keep|edit|add|remove + instructions}. Text merged and guarded;
     playground edits/adds by codelab_edit (checker first, repair loop, Jev review).
 4 VALIDATE: untouched sections byte-identical (guard_untouched); a stage with no usable blocks is rejected; a section
     that still names a removed one gets one more editor pass
 5 VERIFY: feedback_check.check per changed section (≤ 6): one retry through the editor with the reason, then that
     section alone is put back
 REPORT: spec.revision.steps done / kept (editor's reason) / undone / skipped. Everything checked and right →
     steps.Checked: a "✓ " message the server stores and the app shows as a reply (no new version, not counted).
```

Fixed: the stages, the page's limits (blocks, playground capabilities, mode rules, 4-playground cap) and the typed
structural ops (add / remove / move / split / merge a section, level, tone, length, page title, undo). Not fixed: what
changes inside a section.

## Section names and quotes (Nov 10 2026)

Two ways the understand step lost valid requests, fixed by one rule: code repairs what has one right answer (how a
section was written), meaning goes to the reader, nothing is dropped silently.
- **Handles:** the map shows `S3 (section 3)`, not ids, so Haiku has one name to copy. `resolve_ref` turns a handle, a
  number in any form or a label into the id; two possible sections → a question built by code (`which`); none → skip
  that part or an error naming it. Not done (eval first): a per-request enum schema, a Haiku retry with the error.
- **Quotes flag, not drop:** `flag` marks a part `unsure` (quote not in what the reader said, or naming another section);
  the plan card then shows it with a box to untick. Not done: Jev deciding instead of the reader.

## Decisions (user, Nov 9 2026)

- v3 replaces v2 on `FOLLOWUP_PLAN` and reuses its parts (page map cache, undo, answers, refusals, ask + "Add detail",
  step report). The pick stays as the flag-off path.
- Section editor: Haiku with Thinking. Aim: good quality at a manageable cost.
- Reader and Explorer only. Explorer 2.0, Add section button, Feedback and Edit chat are out of scope.

## Cost

Understand: one Haiku Low call (+ the page map's one cheap call per page version, cached). Per opened section: Jev
agreement (input only), one editor call with Thinking, and a playground build only when the editor edits or adds one.
`understand.estimate_usd` (EDIT_USD 0.01 per section, PLAYGROUND_USD 0.012 for half the sections with a playground) is
checked against `FOLLOWUP_MAX_USD` (default 0.15).

## Not done / next

- Run the eval (`server/evals/followup_plan_eval.py`, 82 rows; needs `.variety/` pages, billed) and live tests.
- The coherence check after structural changes is code-based (removed-section names); a Jev page-coherence pass could be
  added if the eval shows misses.
- Image input (left out).
