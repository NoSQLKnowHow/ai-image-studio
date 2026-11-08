"""Comparing two versions of a prompt (DESIGN.md section 37.2, criterion 230): the words that changed, the negative prompt, and the settings.

Pure functions, so every case can be tested on its own. The page shows what comes out: a removed word struck through and an added word underlined, in text as well as in
colour, so that the difference does not depend on seeing a colour.
"""

from __future__ import annotations

import difflib
import json
from typing import Any, Mapping, Optional


def word_diff(before: Optional[str], after: Optional[str]) -> list[dict[str, str]]:
    """The change from `before` to `after` as runs of words: [{"op": "same" | "del" | "add", "text": "…"}, …] in reading order. White space is not compared (a line break
    becoming a space is not a change); a changed phrase is its removed words, then its added ones."""
    old, new = (before or "").split(), (after or "").split()
    runs: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            runs.append({"op": "same", "text": " ".join(old[i1:i2])})
        else:
            if i2 > i1:
                runs.append({"op": "del", "text": " ".join(old[i1:i2])})
            if j2 > j1:
                runs.append({"op": "add", "text": " ".join(new[j1:j2])})
    return runs


def canonical(value: Any) -> Any:
    """A setting in the one form it is stored and compared in: a float with no fraction is the whole number (guidance 4.0 is 4), the rest as it is. Two settings are the same
    when their canonical forms are, so that saving 4.0 over 4 does not make a new version for nothing. A boolean stays a boolean (true is not 1)."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def encoded(settings: Optional[Mapping[str, Any]]) -> str:
    """The settings as stored: canonical, with the keys in order."""
    return json.dumps(canonical(dict(settings or {})), sort_keys=True)


def settings_diff(before: Optional[Mapping[str, Any]], after: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each setting of either version, by name: its value in each and whether it is the `same`, `changed`, `added` (only in `after`) or `removed` (only in `before`)."""
    old, new = dict(before or {}), dict(after or {})
    rows = []
    for key in sorted(set(old) | set(new)):
        if key not in new:
            status = "removed"
        elif key not in old:
            status = "added"
        else:
            status = "same" if json.dumps(canonical(old[key]), sort_keys=True) == json.dumps(canonical(new[key]), sort_keys=True) else "changed"
        rows.append({"key": key, "before": old.get(key), "after": new.get(key), "status": status})
    return rows


def changed(runs: list[dict[str, str]]) -> bool:
    return any(run["op"] != "same" for run in runs)
