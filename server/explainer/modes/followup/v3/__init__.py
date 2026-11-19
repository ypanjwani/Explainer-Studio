"""Follow-ups v3 (the default until v4 replaces it; design: .claude/followup-v3-plan.md).

understand.py      POST /api/followup/plan (through entry.py): the follow-up as sections with the reader's intent and typed
                   structural steps, a question, a refusal, an answer or an undo
steps.py           /api/generate (through run.py): running that plan: structure, each section through the section editor,
                   Jev's check, the report
section_editor.py  one section's change (text and/or playground)
targets.py         applying a section's change, structure written by the page planner

Uses only the shared follow-up tools (shared.py, check.py, playground_edit.py, revise.py, page_index.py, cards.py), never
v4/; nothing outside entry.py and run.py imports this package, so deleting it touches only their v3 branches.
"""
