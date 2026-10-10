"""Project folders: what a project's name may be (DESIGN.md §32.4).

A project is a name that runs can be filed under. It is a label in the database and nothing on the disk, so this module is only about
the name: the one piece of text from the page that becomes part of what the studio stores and shows. It is pure (no database, no
files), so every rule can be tested on its own.
"""

from __future__ import annotations

import re
import unicodedata

from .presets import PROJECT_NAME_MAX


class BadProjectName(ValueError):
    """The name cannot be used; the message says why, in words the page can show as they are."""


# Runs of white space of every kind (spaces, no-break spaces, and so on) are made one plain space. Control characters are refused before
# this runs, so a tab or a newline never gets as far as being "collapsed".
_WHITE_SPACE = re.compile(r"\s+")


def normalize_name(raw: str) -> str:
    """The name as it is stored and shown: trimmed, with each run of white space made one space.

    Raises `BadProjectName` when nothing is left, when it is longer than `PROJECT_NAME_MAX` characters, or when it holds a character that
    is not text: a control character (a tab or a newline too), an invisible format character, or one that is unassigned or private. Those
    would let two names look the same and be different (a zero-width character), or break the line the name is shown on.
    """
    for character in raw:
        # Unicode's category letter C covers every kind of non-text character (control, format, surrogate, private use, unassigned)
        if unicodedata.category(character).startswith("C"):
            raise BadProjectName("A project name can only hold letters, numbers, spaces and punctuation.")
    name = _WHITE_SPACE.sub(" ", raw).strip()
    if not name:
        raise BadProjectName("Give the project a name.")
    if len(name) > PROJECT_NAME_MAX:
        raise BadProjectName(f"A project name is at most {PROJECT_NAME_MAX} characters; this one is {len(name)}.")
    return name


def name_key(name: str) -> str:
    """What makes two names the same project: the normalised name with its case folded away, so *Logo* and *logo* are one project.

    `casefold` rather than `lower` because it is the comparison Unicode defines for this (a German ß and *ss* are the same word in
    capitals). The database keeps this in a column that is unique.
    """
    return normalize_name(name).casefold()
