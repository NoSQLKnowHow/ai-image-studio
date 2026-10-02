"""Meaningful download filenames, using the same scheme as scripts/qwen_image.py.

    <mode>_[<source>_]<prompt-words>_<W>x<H>_s<seed>_[rgba_]<YYYYmmdd-HHMMSS>.png
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import PurePath
from typing import Optional
from urllib.parse import quote

MAX_SLUG_WORDS = 6
MAX_SLUG_BYTES = 60
MAX_STEM_BYTES = 40
FILLER_WORDS = frozenset("a an the of in on at to and or with by for from into is are was very".split())


def slugify(text: str, max_bytes: int = MAX_SLUG_BYTES, max_words: int = MAX_SLUG_WORDS) -> str:
    """Filesystem-safe label from the first few meaningful words. Keeps non-Latin letters."""
    out = ""
    all_words = [w for w in re.split(r"[\W_]+", text.casefold()) if w]
    words = ([w for w in all_words if w not in FILLER_WORDS] or all_words)[:max_words]
    for word in words:
        candidate = f"{out}-{word}" if out else word
        if len(candidate.encode("utf-8")) > max_bytes:
            if not out:  # a single enormous word
                out = candidate.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
            break
        out = candidate
    return out or "untitled"


def download_filename(
    *,
    mode: str,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    created_at: datetime,
    transparent: bool = False,
    source_name: Optional[str] = None,
) -> str:
    parts = [mode]
    if source_name:
        parts.append(slugify(PurePath(source_name).stem, MAX_STEM_BYTES, max_words=4))
    parts += [slugify(prompt), f"{width}x{height}", f"s{seed}"]
    if transparent:
        parts.append("rgba")
    parts.append(created_at.strftime("%Y%m%d-%H%M%S"))
    return "_".join(parts) + ".png"


def thumbnail_filename(image_filename: str) -> str:
    """The name of an image's thumbnail download: `<name>_thumb.webp` for `<name>.png`."""
    stem = image_filename[:-4] if image_filename.lower().endswith(".png") else image_filename
    return f"{stem}_thumb.webp"


def content_disposition(filename: str, default: str = "image.png") -> str:
    """`attachment` header with an ASCII fallback plus the exact UTF-8 name (RFC 6266 / 5987)."""
    fallback = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", fallback)
    fallback = re.sub(r"-*_[-_]*", "_", fallback).strip("_-")  # no dangling "-_" from dropped characters
    if not fallback or fallback.startswith("."):  # nothing but an extension was left (a name in another script)
        fallback = default
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
