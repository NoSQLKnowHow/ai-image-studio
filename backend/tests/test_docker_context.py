"""Everything the Dockerfile copies must be in the build context.

`.dockerignore` keeps the context small, and an entry there can silently remove a file the Dockerfile copies: the
image build then fails ("not found") on the Spark, where nothing here can run it. This reads both files and checks
that no COPY source from the build context is excluded, using Docker's rules as far as they matter here: the patterns
are applied in order, the last one that matches decides, `!` re-includes, a pattern also covers everything under a
folder it matches, `*` stops at a slash and `**` does not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _regex(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"(?:/.*)?$")  # the path itself, or anything inside it


def excluded(path: str, ignore_lines: list[str]) -> bool:
    """Is `path` (relative to the context, no leading slash) left out of the build context by these .dockerignore lines?"""
    result = False
    for raw in ignore_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:] if negate else line
        if _regex(pattern.strip("/")).match(path):
            result = not negate
    return result


def copy_sources(dockerfile_text: str) -> list[str]:
    """The context paths named by COPY lines (not `--from=`), without a trailing slash."""
    sources: list[str] = []
    for line in dockerfile_text.replace("\\\n", " ").splitlines():
        parts = line.split()
        if not parts or parts[0].upper() != "COPY":
            continue
        args = [p for p in parts[1:] if not p.startswith("--")]
        if any(p.startswith("--from") for p in parts[1:]) or len(args) < 2:
            continue
        sources += [src.rstrip("/") or "." for src in args[:-1]]
    return sources


def test_nothing_the_dockerfile_copies_is_left_out_of_the_build_context():
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    sources = copy_sources((ROOT / "Dockerfile").read_text())
    assert "scripts/minimax_music.py" in sources and "backend/studio" in sources and "docker" in sources  # the reader works
    for source in sources:
        assert (ROOT / source).exists(), f"the Dockerfile copies {source}, which is not in the repository"
        assert not excluded(source, ignore), f"the Dockerfile copies {source}, but .dockerignore leaves it out of the build"


# ------------------------------------------------------------------ the rules themselves, so the test above can be trusted
OLD_RULES = ["**/node_modules", "# a comment", "scripts", "docs", "backend/tests", "README.md"]
FIXED_RULES = ["scripts/*", "!scripts/minimax_music.py", "docs"]


@pytest.mark.parametrize("path,rules,gone", [
    ("scripts/minimax_music.py", OLD_RULES, True),  # the mistake that broke the 1.8 build
    ("scripts/minimax_music.py", FIXED_RULES, False),
    ("scripts/qwen_image.py", FIXED_RULES, True),  # the rest of the folder is still left out
    ("docs/DESIGN.md", FIXED_RULES, True),  # a folder pattern covers what is inside it
    ("backend/studio/jobs.py", OLD_RULES, False),
    ("backend/tests/test_api.py", OLD_RULES, True),
    ("frontend/node_modules/react/index.js", OLD_RULES, True),  # ** matches at any depth
    ("README.md", OLD_RULES, True),
    ("README.mdx", OLD_RULES, False),  # a pattern is not a prefix
    ("scripts/x", ["scripts/*", "!scripts/x", "scripts/x"], True),  # the last matching pattern decides
])
def test_the_dockerignore_rules_are_read_as_docker_reads_them(path, rules, gone):
    assert excluded(path, rules) is gone


def test_a_copy_line_is_read_with_its_flags_and_continuations():
    text = "COPY --chown=1000:1000 a.txt b/ /dest/\nCOPY --from=ui /ui/dist /app/static\nRUN echo \\\n  hi\nCOPY x \\\n  /y\n"
    assert copy_sources(text) == ["a.txt", "b", "x"]
