"""The refusals of the Speech tab (DESIGN.md §38.6, §38.9) and the one check every request for an engine's work makes first.

Kept apart from service.py and voices.py, which both use them and one of which uses the other.
"""

from __future__ import annotations

from typing import Any, Optional


class ReadingRefused(Exception):
    """A request for a reading (or something done to one) was refused. `status` and `code` are the response's; `field` names what is wrong where one thing is."""

    def __init__(self, status: int, code: str, message: str, field: Optional[str] = None, hint: Optional[str] = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field, self.hint = status, code, message, field, hint


def require_engine(engines: dict[str, dict[str, Any]], engine_id: str) -> dict[str, Any]:
    """The engine's entry in `capabilities.speech.engines`, if it can be used now. Raises ReadingRefused: no such engine (422), still being checked (409),
    or cannot run here, with its own reason and hint (409). An engine that is short of memory is accepted, because the worker makes room for it."""
    entry = engines.get(engine_id)
    if entry is None:
        raise ReadingRefused(422, "unknown_engine", f"There is no speech engine {engine_id!r} on this server.", "engine")
    if entry["state"] == "checking":  # not a verdict: the check made at start-up has not finished, and the page disables Start until it has
        raise ReadingRefused(409, "engine_checking", f"{entry.get('title') or engine_id} is still being checked. Try again in a moment.", "engine")
    if entry["state"] not in ("ready", "needs_memory"):
        raise ReadingRefused(409, "engine_unavailable", entry.get("reason") or "That speech engine cannot run here.", "engine", entry.get("hint"))
    return entry
