"""Filters on the history (DESIGN.md §29.5).

One frozen record with one field per filter. Each field becomes ONE fixed SQL condition with a bound value, so nothing here is ever built
from text the page sent: the API turns its query parameters into a `RunFilter` first, and an unknown value is refused there. The page has the
same record (`frontend/src/history.ts`), and both are tested against one table of cases (`tests/filter_cases.json`).

Adding a filter: a field and its condition in `conditions()`, a query parameter in the API, a name in `COUNTED` if the page shows a count for
it, and a case in the table. (`project` is the one filter that holds a value and not a yes-or-no, §32.4.)

Release 1.26 (DESIGN.md §45) adds five: `q` (a search over a run's words, notes and tags), `model`, `size`, and a date range as two instants (`since`, `until`). The five are
"scope" filters like `project`: the numbers on the filter bar follow them (`counted_within`).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

from .runsearch import PLAIN_HAYSTACK, RUN_HEIGHT, RUN_WIDTH, match_string, parse_size, plain_pattern, search_terms

# What the Images, Music, Vector and Speech tabs hold, as the runs table's `mode`: the page splits the history by tab and counts per tab (DESIGN.md §31.12.7).
KINDS: dict[str, tuple[str, ...]] = {"image": ("generate", "edit"), "music": ("music",), "vector": ("vector",), "speech": ("speech",)}

# The value of `RunFilter.project` that means "runs that are in no project" (DESIGN.md §32.4). A project's id is 32 hex digits, so this can
# never be one.
NO_PROJECT = "none"


# What makes a run belong in the Deleted view (DESIGN.md §33.3, §35.4): it is in the bin itself, or one of its pictures, tracks or versions is. A fixed
# fragment with no placeholder: nothing in it comes from outside.
_IN_THE_BIN = (
    "(deleted_at IS NOT NULL"
    " OR EXISTS (SELECT 1 FROM images WHERE images.run_id = runs.id AND images.kind = 'output' AND images.deleted_at IS NOT NULL)"
    " OR EXISTS (SELECT 1 FROM tracks WHERE tracks.run_id = runs.id AND tracks.deleted_at IS NOT NULL)"
    " OR EXISTS (SELECT 1 FROM vectors WHERE vectors.run_id = runs.id AND vectors.deleted_at IS NOT NULL))"
)


@dataclass(frozen=True)
class RunFilter:
    kept: Optional[bool] = None  # True: only runs that are kept; False: only runs that are not; None: either
    deleted: Optional[bool] = False  # False: the history (runs not in the bin); True: only the bin (§30, §33); None: either
    project: Optional[str] = None  # None: any project or none; NO_PROJECT: only runs in no project; a project's id: only that project (§32.4)
    prompt: Optional[str] = None  # None: any run; a saved prompt's id: only the runs made from it (`GET /api/runs?prompt=`, DESIGN.md §37.4)
    family: Optional[str] = None  # None: any run; a run's id: that run and every variation made from it (`GET /api/runs?family=`, DESIGN.md §43.4)
    q: Optional[str] = None  # None: any run; the text of a search (DESIGN.md §45.4 rule 2): every word of it must be the start of a word in the run's words, notes or tags
    model: Optional[str] = None  # None: any run; a model's name: only the runs that model made (`runs.model_id`, §45.4 rule 5)
    size: Optional[str] = None  # None: any run; "WIDTHxHEIGHT": only the picture runs of exactly that size (§45.4 rule 5, 6)
    since: Optional[str] = None  # None: no start; a UTC instant in the studio's own form (`2026-11-01T05:00:00.000Z`): only runs made at or after it (§45.5)
    until: Optional[str] = None  # None: no end; a UTC instant: only runs made before it (§45.5)

    def conditions(self, fts: bool = True) -> tuple[list[str], list[Any]]:
        """The SQL conditions (to be joined with AND) and the values bound to their placeholders. `fts` is whether this SQLite has the word index (`Database.has_run_fts`): a search
        uses it where it is and a plain match where it is not. Only a search differs."""
        clauses: list[str] = []
        values: list[Any] = []
        if self.kept is not None:
            clauses.append("pinned = ?")
            values.append(1 if self.kept else 0)
        # The bin is a filter like the others. The default (False) makes every list the history WITHOUT the bin, so nothing that was
        # written before the bin existed shows a deleted run by accident; True is only the bin; None is both. The bin holds two kinds of thing
        # (DESIGN.md §33.1): a run that was sent to it, and a run that is still in the history but has a picture or track of its own in it. The
        # second kind is a run too, so the Deleted view lists it (as one card with its deleted pictures); the history, False, still lists it
        # as well, since the run itself was not deleted.
        if self.deleted is False:
            clauses.append("deleted_at IS NULL")
        elif self.deleted is True:
            clauses.append(_IN_THE_BIN)
        # A project is the first filter that holds a value and not a yes-or-no: "none" is one fixed condition and a project's id is another,
        # bound as a value like the rest. An id that does not exist is not an error: it simply matches no run.
        if self.project is not None:
            if self.project == NO_PROJECT:
                clauses.append("project_id IS NULL")
            else:
                clauses.append("project_id = ?")
                values.append(self.project)
        if self.prompt is not None:
            clauses.append("prompt_id = ?")
            values.append(self.prompt)
        # A family is the run it started from and every run whose `family_id` is that run (a variation of a variation has the same `family_id` as the variation,
        # which is the first run's id, so the family is flat however deep it goes). The one id is bound twice, once for each side of the OR; an id that is not a
        # run matches nothing.
        if self.family is not None:
            clauses.append("(id = ? OR family_id = ?)")
            values.extend([self.family, self.family])
        # A search (DESIGN.md §45.4 rule 2). The text is cut into WORDS first (`search_terms`), and only those words reach the SQL, as bound values: with the word index one match string
        # made from them (every word in quotes with a `*`, so nothing typed is query syntax), without it one LIKE for each word. A text with no word in it is no search and adds nothing.
        terms = search_terms(self.q)
        if terms:
            if fts:
                clauses.append("id IN (SELECT run_id FROM run_search WHERE run_search MATCH ?)")
                values.append(match_string(terms))
            else:
                for term in terms:
                    clauses.append(f"{PLAIN_HAYSTACK} LIKE ? ESCAPE '\\'")
                    values.append(plain_pattern(term))
        if self.model is not None:
            clauses.append("model_id = ?")
            values.append(self.model)
        # A size is a PICTURE run's (the Images tab's): a music track or a reading has none, and a Vector run's `width` and `height` are the picture the model drew, not the size in millimetres
        # its card shows, so no run of those kinds matches (§45.4 rule 6). A text that is not two whole numbers matches nothing, rather than being an error: the API refuses it first.
        if self.size is not None:
            wanted = parse_size(self.size)
            if wanted is None:
                clauses.append("0")
            else:
                clauses.append(f"mode IN ('generate', 'edit') AND {RUN_WIDTH} = ? AND {RUN_HEIGHT} = ?")
                values.extend(wanted)
        # The range is of instants, never of dates: `created_at` is stored in UTC in the form `...sss Z`, so a text comparison with an instant in the same form is a comparison in time.
        if self.since is not None:
            clauses.append("created_at >= ?")
            values.append(self.since)
        if self.until is not None:
            clauses.append("created_at < ?")
            values.append(self.until)
        return clauses, values


# The counts the page shows, by name: `GET /api/runs/counts` answers {kind: {name: n}}. The count named `deleted` is of THINGS in the bin (§33.1):
# each run in it and each picture or track in it of a run that is not; `database.run_counts` also answers `deleted_items`, the second part alone.
COUNTED: dict[str, RunFilter] = {"all": RunFilter(), "kept": RunFilter(kept=True), "deleted": RunFilter(deleted=True)}


def counted_within(project: Optional[str] = None, *, q: Optional[str] = None, model: Optional[str] = None, size: Optional[str] = None, since: Optional[str] = None,
                   until: Optional[str] = None) -> Mapping[str, RunFilter]:
    """`COUNTED` narrowed to one project (or to no project) and to a search and the filters, so the numbers on the filter bar describe what is on the screen while a project, a
    search or a filter is chosen (DESIGN.md §32.5, §45.8). With none of them given it is `COUNTED` itself."""
    scope = {"project": project, "q": q, "model": model, "size": size, "since": since, "until": until}
    given = {name: value for name, value in scope.items() if value is not None}
    return {name: replace(run_filter, **given) for name, run_filter in COUNTED.items()} if given else COUNTED
