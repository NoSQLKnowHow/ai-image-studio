"""A chat's pictures on the disk (DESIGN.md section 37.3 rule 13, criterion 244).

A picture attached to a chat is COPIED into the chat's own folder, `<data>/builder/<chat>/`, shrunk to at most 1536 pixels on its long side, and goes when the chat
is deleted for good. It is checked exactly as an image added to an edit is (`inputs.decode_upload`: the type is decided by decoding, never by the name). It is sent to
Ollama and nowhere else, and is served back to the page only as a thumbnail of the chat. A picture without transparency is kept as a JPEG (a fraction of the size, and all
a model that looks at pictures needs); one with transparency stays a PNG.
"""

from __future__ import annotations

import io
import logging
import os
import secrets
import shutil
from typing import Optional

from PIL import Image

from ..inputs import UploadError, decode_upload
from ..storage import Storage, check_id

MAX_SIDE = 1536
JPEG_QUALITY = 90
MAX_PER_MESSAGE = 8  # the same as the most pictures an edit may be given (STUDIO_MAX_INPUT_IMAGES at its highest)
log = logging.getLogger("studio.builder")


def chat_dir(storage: Storage, chat_id: str):
    return storage.root / "builder" / check_id(chat_id)


def save_picture(storage: Storage, chat_id: str, data: bytes) -> tuple[str, int, int, str]:
    """Check, shrink and keep one picture. Returns (path relative to the data folder, width, height, media type). Raises `UploadError` with the words for the person."""
    image = decode_upload(data)
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    alpha = image.mode == "RGBA"
    folder = chat_dir(storage, chat_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{secrets.token_hex(16)}.{'png' if alpha else 'jpg'}"
    target, partial = folder / name, folder / (name + ".part")
    try:
        buffer = io.BytesIO()
        if alpha:
            image.save(buffer, "PNG", optimize=True)
        else:
            image.save(buffer, "JPEG", quality=JPEG_QUALITY, optimize=True)
        partial.write_bytes(buffer.getvalue())
        os.replace(partial, target)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise UploadError(507, f"The picture could not be kept: {exc.strerror or exc}") from None
    return storage.rel(target), image.width, image.height, "image/png" if alpha else "image/jpeg"


def media_type(path: str) -> str:
    return "image/png" if path.endswith(".png") else "image/jpeg"


def remove_picture(storage: Storage, path: str) -> None:
    try:
        storage.abs(path).unlink(missing_ok=True)
    except Exception:
        log.warning("a chat's picture could not be removed")


def remove_chat_files(storage: Storage, chat_id: Optional[str]) -> None:
    """Delete a chat's folder, with every picture in it. A folder that is not there is nothing to do; one that cannot be removed is logged and left."""
    if not chat_id:
        return
    try:
        shutil.rmtree(chat_dir(storage, chat_id), ignore_errors=False)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("a chat's folder could not be removed: %s", exc.strerror or exc)
