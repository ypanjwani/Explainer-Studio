"""Follow-ups v4 (FOLLOWUP_V4=1; design: .claude/followup-v4-plan.md): a tool-using editor in place of v3's typed plan.

talk.py    POST /api/followup/plan: answer, ask (2-4 options), cannot (with an alternative), new page, revert, or a change
           as one brief for the editor
editor.py  /api/generate: Haiku with tools changes the page (page.py holds the working copy and the change log), Jev
           checks each changed section, reply.py writes the friendly reply from what really happened
tools.py   the editor's tools, each applied and checked by code
"""

from __future__ import annotations

import os


def on() -> bool:
    """Follow-ups v4 are switched on (FOLLOWUP_V4=1); v3 stays the default until they are compared."""
    return os.environ.get("FOLLOWUP_V4", "") == "1"
