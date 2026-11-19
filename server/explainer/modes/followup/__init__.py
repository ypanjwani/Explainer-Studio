"""Follow-ups on Reader and Explorer pages: three versions side by side, v3 (the default), v4 (FOLLOWUP_V4=1) and v5
(FOLLOWUP_V5=1, which wins over v4).

  entry.py     POST /api/followup/plan: the request checked, then v5/talk (code only), v4/talk or v3/understand
  run.py       /api/generate once a plan is chosen: shared work around the edit, then v5/editor, v4/editor or v3/steps
               (entry.py and run.py are the only files that know the versions; each imports a version only in its branch)
  v3/          understand, steps, section_editor, targets (see v3/__init__.py)
  v4/          talk, editor, tools, page, reply (see v4/__init__.py)
  v5/          talk, editor, tools, page, reply, review, base (see v5/__init__.py); imports neither v3 nor v4

Shared follow-up tools, used by both (they import neither version):
  shared.py           Checked / CHECKED / CHECK_MAX, max_usd, is_undo, history_text, request limits
  page_index.py       the cached page map both read
  check.py            Jev's after-edit check
  playground_edit.py  playground edits and adds through Explorer's checks (modes/explorer/)
  revise.py, cards.py restoring kept playgrounds, ids, section cards, the plan request's checks
  main_file.py        a version's main file (v5); the version store itself is explainer/versions.py

Deleting v3 or v4: remove its folder, its branches in entry.py and run.py, its tests and prompts (keep the prompt helpers
editorIntro / fieldGuide, which v5 uses too). test_modes_boundary.py proves v4 runs with v3/ unimportable and v5 runs
with v3/ and v4/ unimportable.
"""
