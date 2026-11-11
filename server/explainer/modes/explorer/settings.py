"""Explorer's per-page settings: how hard Haiku thinks at each playground step (copied from codelab.CodeLabSettings,
keeping only what Explorer uses: the page's one Effort everywhere, Thinking on or off, and the screenshot check)."""

from __future__ import annotations

import os
from dataclasses import dataclass

# The screenshot check of each kept playground (look_review): off unless PG_LOOK=1.
PG_LOOK = os.environ.get("PG_LOOK", "0") == "1"

EFFORTS = ("low", "medium", "high")


@dataclass(frozen=True)
class ExplorerSettings:
    plan: str = "medium"
    build: str = "medium"
    fix: str = "medium"
    think: bool = True  # False: Haiku writes, repairs and backs up playgrounds without thinking (cheaper)
    look: bool = False  # PG_LOOK: screenshots of each kept playground are judged by Haiku; problems get one repair

    @staticmethod
    def for_effort(effort: str, think: bool = True) -> "ExplorerSettings":
        e = effort if effort in EFFORTS else "medium"
        return ExplorerSettings(plan=e, build=e, fix=e, think=think, look=PG_LOOK)


def _lower(effort: str) -> str | None:
    """One effort level down (for a build that spent its whole budget thinking), or None below low."""
    i = EFFORTS.index(effort) if effort in EFFORTS else 1
    return EFFORTS[i - 1] if i > 0 else None
