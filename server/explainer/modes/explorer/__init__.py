"""Explorer (`playground`): its own pipeline (pipeline.py), playground builder (playground.py), Jev's gate and review
(judge.py), and the page-level part that starts playgrounds while the outline streams (stream.py)."""

from .pipeline import generate

__all__ = ["generate"]
