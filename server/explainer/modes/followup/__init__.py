"""Follow-ups v3 on Reader and Explorer pages (moved here from server/explainer/ on Nov 11 2026).

  understand.py      what the follow-up asks (POST /api/followup/plan)
  page_index.py      the cached page map understand reads
  steps.py           running a plan: structure, then each section through the section editor, then Jev's check
  section_editor.py  one section's change (text and/or playground)
  run.py             the follow-up's run once the plan is chosen (/api/generate)
  playground_edit.py, targets.py, revise.py, check.py, cards.py
                     copies of the parts of codelab_edit / targets / revise / feedback_check / feedback_pick /
                     followup_pick these use, so nothing here imports another mode's code
"""
