"""Filters on the history (DESIGN.md §29.5).

One frozen record with one field per filter. Each field becomes ONE fixed SQL condition with a bound value, so nothing here is ever built
from text the page sent: the API turns its query parameters into a `RunFilter` first, and an unknown value is refused there. The page has the
same record (`frontend/src/history.ts`), and both are tested against one table of cases (`tests/filter_cases.json`).

Adding a filter: a field and its condition in `conditions()`, a query parameter in the API, a name in `COUNTED` if the page shows a count for
it, and a case in the table. (`project` is the one filter that holds a value and not a yes-or-no, §32.4.)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

# What the Images and Music tabs hold, as the runs table's `mode`: the page splits the history by tab and counts per tab.
KINDS: dict[str, tuple[str, ...]] = {"image": ("generate", "edit"), "music": ("music",)}

# The value of `RunFilter.project` that means "runs that are in no project" (DESIGN.md §32.4). A project's id is 32 hex digits, so this can
# never be one.
NO_PROJECT = "none"


@dataclass(frozen=True)
class RunFilter:
    kept: Optional[bool] = None  # True: only runs that are kept; False: only runs that are not; None: either
    deleted: Optional[bool] = False  # False: the history (runs not in the bin); True: only the bin (DESIGN.md §30); None: either
    project: Optional[str] = None  # None: any project or none; NO_PROJECT: only runs in no project; a project's id: only that project (§32.4)

    def conditions(self) -> tuple[list[str], list[Any]]:
        """The SQL conditions (to be joined with AND) and the values bound to their placeholders."""
        clauses: list[str] = []
        values: list[Any] = []
        if self.kept is not None:
            clauses.append("pinned = ?")
            values.append(1 if self.kept else 0)
        # The bin is a filter like the others. The default (False) makes every list the history WITHOUT the bin, so nothing that was
        # written before the bin existed shows a deleted run by accident; True is only the bin; None is both.
        if self.deleted is not None:
            clauses.append("deleted_at IS NOT NULL" if self.deleted else "deleted_at IS NULL")
        # A project is the first filter that holds a value and not a yes-or-no: "none" is one fixed condition and a project's id is another,
        # bound as a value like the rest. An id that does not exist is not an error: it simply matches no run.
        if self.project is not None:
            if self.project == NO_PROJECT:
                clauses.append("project_id IS NULL")
            else:
                clauses.append("project_id = ?")
                values.append(self.project)
        return clauses, values


# The counts the page shows, by name: `GET /api/runs/counts` answers {kind: {name: n}}.
COUNTED: dict[str, RunFilter] = {"all": RunFilter(), "kept": RunFilter(kept=True), "deleted": RunFilter(deleted=True)}


def counted_within(project: Optional[str]) -> Mapping[str, RunFilter]:
    """`COUNTED` narrowed to one project (or to no project), so the numbers on the filter bar describe what is on the screen while a project
    is chosen (DESIGN.md §32.5). With `project` None it is `COUNTED` itself."""
    return COUNTED if project is None else {name: replace(run_filter, project=project) for name, run_filter in COUNTED.items()}
