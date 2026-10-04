"""Where a model's files come from: the local cache first, the network only if files are missing (DESIGN.md §26.11).

Hugging Face's `from_pretrained` asks the hub whether anything changed every time a model is loaded, and downloads the
update if it did. The studio would rather never do that after the first download, so by default (`auto`) a model is
loaded with `local_files_only=True` first, and only if that fails *because files are missing from the cache* is it
loaded again online. `offline` never goes online and `online` is the old behaviour.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

log = logging.getLogger("studio.pipelines.hub")

T = TypeVar("T")

# What a load that wanted a file the cache does not have looks like. Exception classes differ between huggingface_hub,
# transformers and diffusers, and between their versions, so the class names and the usual wording are both checked.
_MISS_NAMES = frozenset({"LocalEntryNotFoundError", "EntryNotFoundError", "OfflineModeIsEnabled", "FileNotFoundError"})
_MISS_WORDS = (
    "local_files_only", "cached files", "in the cache", "not found in cache", "offline mode", "localentrynotfounderror",
    "cannot find the requested files", "couldn't find them", "does not appear to have a file named",
)


def _chain(exc: BaseException):
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)


def is_cache_miss(exc: BaseException) -> bool:
    """Did this load fail because files are missing from the local cache (and not for any other reason)?"""
    for item in _chain(exc):
        if any(cls.__name__ in _MISS_NAMES for cls in type(item).__mro__):
            return True
        text = str(item).lower()
        if any(word in text for word in _MISS_WORDS):
            return True
    return False


def load_with_hub_mode(load: Callable[[bool], T], mode: str, what: str = "the model") -> T:
    """Call `load(local_files_only)` the way `mode` says: "offline" with True, "online" with False, and "auto" with
    True first and, if that fails because of the cache, False. Anything else that goes wrong is raised as it is."""
    if mode == "offline":
        return load(True)
    if mode == "online":
        return load(False)
    try:
        return load(True)
    except Exception as exc:  # Canceled is a BaseException and passes straight through
        if not is_cache_miss(exc):
            raise
        log.info("%s is not completely in the local cache (%s); fetching what is missing", what, type(exc).__name__)
    return load(False)
