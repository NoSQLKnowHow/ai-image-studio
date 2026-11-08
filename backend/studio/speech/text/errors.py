"""The ways preparing a source can fail, each with a code the page can act on, a sentence for the person, and where it helps a suggestion (DESIGN.md §38.9)."""

from __future__ import annotations

from typing import Optional

# code -> the HTTP status the API answers with
STATUS = {
    "private_address": 422, "bad_address": 422, "too_large": 413, "no_text_layer": 422, "unsupported": 415, "unreachable": 502,
    "extract_failed": 422, "timeout": 504, "encrypted": 422, "no_text": 422, "empty": 422, "bad_encoding": 422,
}
PASTE_HINT = "You can paste the text into the box instead."


class PrepareError(Exception):
    """A source could not be turned into text. `code` is one of `STATUS`'s keys."""

    def __init__(self, code: str, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint

    @property
    def status(self) -> int:
        return STATUS.get(self.code, 422)

    def as_dict(self) -> dict[str, Optional[str]]:
        return {"code": self.code, "message": self.message, "hint": self.hint}
