"""Filters on the history (DESIGN.md §29.5).

One frozen record with one field per filter. Each field becomes ONE fixed SQL condition with a bound value, so nothing here is ever built
from text the page sent: the API turns its query parameters into a `RunFilter` first, and an unknown value is refused there. The page has the
same record (`frontend/src/history.ts`), and both are tested against one table of cases (`tests/filter_cases.json`).

Adding a filter: a field and its condition in `conditions()`, a query parameter in the API, a name in `COUNTED` if the page shows a count for
it, and a case in the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# What the Images and Music tabs hold, as the runs table's `mode`: the page splits the history by tab and counts per tab.
KINDS: dict[str, tuple[str, ...]] = {"image": ("generate", "edit"), "music": ("music",)}


@dataclass(frozen=True)
class RunFilter:
    kept: Optional[bool] = None  # True: only runs that are kept; False: only runs that are not; None: either

    def conditions(self) -> tuple[list[str], list[Any]]:
        """The SQL conditions (to be joined with AND) and the values bound to their placeholders."""
        clauses: list[str] = []
        values: list[Any] = []
        if self.kept is not None:
            clauses.append("pinned = ?")
            values.append(1 if self.kept else 0)
        return clauses, values


# The counts the page shows, by name: `GET /api/runs/counts` answers {kind: {name: n}}.
COUNTED: dict[str, RunFilter] = {"all": RunFilter(), "kept": RunFilter(kept=True)}
