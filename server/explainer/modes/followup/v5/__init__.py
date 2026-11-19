"""Follow-ups v5 (the default follow-up; FOLLOWUP_V5=0 turns it off; design: .claude/followup-v5-plan.md): the version store and main file in front of one
agent. Stands alone: never imports v3 or v4 (test_modes_boundary.py runs its suite with both deleted).

talk.py    POST /api/followup/plan, code only (phase 4, Nov 16 2026): "undo" reverts; anything else is a plan for the agent
           carrying the conversation, the option picked and the answer given just before
agent.py   /api/generate: one Haiku agent reads the version's main file, the version log, the page and the conversation, and
           answers, asks, says it's a new topic, or changes the page with tools (tools.py, page.py: any version readable,
           start_from, restore_section / restore_playground). The checks (editor.problems, review.py) come back to it as
           feedback at its first finish; a final guard puts back only what's still broken; it writes its own reply
base.py    the base version as a follow-up (a port of shared/followup.ts buildFollowUp), so run.py edits what the server holds
"""

from __future__ import annotations

import os


def on() -> bool:
    """Follow-ups v5 are the default follow-up (on unless FOLLOWUP_V5=0; Nov 19 2026)."""
    return os.environ.get("FOLLOWUP_V5", "1") != "0"


def look_on() -> bool:
    """The agent can look at Explorer playgrounds (screenshots, look.py; on unless FOLLOWUP_V5_LOOK=0; Nov 19 2026)."""
    return os.environ.get("FOLLOWUP_V5_LOOK", "1") != "0"


def full_page() -> bool:
    """The agent also gets the page's full text when it fits (FOLLOWUP_V5_FULLPAGE, on unless set to 0)."""
    return os.environ.get("FOLLOWUP_V5_FULLPAGE", "1") != "0"
