"""Images that go into an edit: uploads, staging, and the copies a run owns (DESIGN.md §11, §21.6, §21.7).

An upload is checked, normalised and stored as a PNG in a staging folder, with a thumbnail. It waits there
until a run claims it, and is deleted after STUDIO_UPLOAD_TTL_HOURS if none does. A run never shares a file:
when it is created, the files of its inputs (staged uploads, or images from earlier runs) are *copied* into
its own folder, so deleting or expiring the original can never break it.

Everything here that touches pixels or the disk is synchronous; callers run it in a thread.
"""

from __future__ import annotations

import io
import logging
import secrets
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from . import presets as P
from .db import Database
from .serialize import format_ts, utcnow
from .storage import Storage, check_id

log = logging.getLogger("studio.inputs")

ORPHAN_AGE_SECONDS = 3600  # a file or folder nothing in the database claims is only removed once it is this old
SWEEP_BATCH = 200


class UploadError(Exception):
    """An upload was refused. `status` is the HTTP status: 413 too large, 415 wrong type, 422 damaged."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _has_transparency(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in image.info


def decode_upload(data: bytes) -> Image.Image:
    """Check an uploaded file and return it as an 8-bit RGB or RGBA image, upright, with its transparency
    kept (the model reads all four channels, so an upload is never flattened onto a background).

    Raises UploadError. The type is decided by decoding, never by the file name or the Content-Type header."""
    if not data:
        raise UploadError(422, "The file is empty.")
    try:
        image = Image.open(io.BytesIO(data))
    except UnidentifiedImageError:
        raise UploadError(415, "That isn't a PNG, JPEG or WebP image.") from None
    except Image.DecompressionBombError:
        raise UploadError(413, f"The image has far more than {P.UPLOAD_MAX_PIXELS / 1e6:.0f} megapixels.") from None
    except Exception:  # a recognisable file whose header is damaged
        raise UploadError(422, "The image is damaged and can't be read.") from None
    if image.format not in P.UPLOAD_FORMATS:
        raise UploadError(415, f"That is a {image.format} file; only PNG, JPEG and WebP images are accepted.")
    pixels = image.width * image.height
    if pixels > P.UPLOAD_MAX_PIXELS:
        raise UploadError(
            413, f"The image is {pixels / 1e6:.1f} megapixels ({image.width}x{image.height}); the limit is "
                 f"{P.UPLOAD_MAX_PIXELS / 1e6:.0f}. Make it smaller first.")
    try:
        image.load()
        image = ImageOps.exif_transpose(image)  # phone photos carry their rotation in a tag the model never sees
        return image.convert("RGBA" if _has_transparency(image) else "RGB")
    except Exception:
        raise UploadError(422, "The image is damaged and can't be read.") from None


def store_upload(db: Database, storage: Storage, data: bytes) -> dict[str, Any]:
    """Validate, normalise and stage an upload. Returns what the page needs to show it."""
    image = decode_upload(data)
    upload_id = secrets.token_hex(16)
    path = storage.staged / f"{upload_id}.png"
    partial = path.with_name(path.name + ".part")
    try:
        image.save(partial, format="PNG")
        partial.replace(path)
    finally:
        partial.unlink(missing_ok=True)
    try:
        thumb = storage.make_thumbnail_at(path, storage.staged_thumbs / f"{upload_id}.webp")
        db.add_image({
            "id": upload_id, "run_id": None, "kind": "input", "idx": None, "seed": None,
            "width": image.width, "height": image.height, "has_alpha": int(image.mode == "RGBA"),
            "bytes": path.stat().st_size, "path": storage.rel(path), "thumb_path": storage.rel(thumb),
            "created_at": utcnow(),
        })
    except BaseException:
        storage.delete_staged_files(upload_id)
        raise
    return {
        "upload_id": upload_id, "width": image.width, "height": image.height, "has_alpha": image.mode == "RGBA",
        "bytes": path.stat().st_size, "url": f"/api/images/{upload_id}", "thumb_url": f"/api/images/{upload_id}/thumb",
    }


def delete_upload(db: Database, storage: Storage, upload_id: str) -> bool:
    """Remove a staged upload (the page's ✕). False if there is no such upload, or a run has already claimed it."""
    if db.delete_staged(check_id(upload_id)) is None:
        return False
    storage.delete_staged_files(upload_id)
    return True


@dataclass(frozen=True)
class SourceImage:
    """One input of a new run, resolved to a file that exists."""

    position: int  # 1-based: "image 1"
    role: str
    row: Any  # the images row it comes from
    staged: bool  # taken from a staged upload (which the run consumes) rather than copied from an earlier image


def resolve_refs(db: Database, storage: Storage, refs: list[Any]) -> tuple[list[SourceImage], list[tuple[tuple[Any, ...], str]]]:
    """Find the image behind every reference of a run request. Returns (sources, errors); an error names the
    position, so a page can mark the right thumbnail: input_images[1] is "Image 2"."""
    sources: list[SourceImage] = []
    errors: list[tuple[tuple[Any, ...], str]] = []
    for i, ref in enumerate(refs):
        where, label = ("input_images", i), f"Image {i + 1}"
        if ref.upload_id is not None:
            row = db.get_staged(ref.upload_id)
            if row is None:
                errors.append((where, f"{label}: that upload was already used, or has expired (uploads are kept "
                                      "for a limited time). Add the image again."))
                continue
            staged = True
        else:
            row = db.get_image(ref.image_id)
            if row is None or row["run_id"] is None:
                errors.append((where, f"{label}: that image no longer exists (its run may have been deleted). "
                                      "Add it again."))
                continue
            staged = False
        try:
            exists = storage.abs(row["path"]).is_file()
        except Exception:
            exists = False
        if not exists:
            errors.append((where, f"{label}: the image file is missing on the server. Add it again."))
            continue
        sources.append(SourceImage(i + 1, ref.role, row, staged))
    return sources, errors


def copy_inputs(db: Database, storage: Storage, run_id: str, sources: list[SourceImage]) -> list[dict[str, Any]]:
    """Copy each source into the run's own folder. Returns the image rows the run will own (not yet in the
    database). On any failure everything copied so far is removed. A missing source file raises
    FileNotFoundError naming the position."""
    folder = storage.run_inputs_dir(run_id)
    rows: list[dict[str, Any]] = []
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for src in sources:
            dst = folder / f"{src.position}.png"
            try:
                shutil.copyfile(storage.abs(src.row["path"]), dst)
            except FileNotFoundError:
                raise FileNotFoundError(src.position) from None
            thumb_dst = storage.thumbs / run_id / f"in-{src.position}.webp"
            thumb_src = storage.abs(src.row["thumb_path"]) if src.row["thumb_path"] else None
            if thumb_src is not None and thumb_src.is_file():
                thumb_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(thumb_src, thumb_dst)
            else:
                storage.make_thumbnail_at(dst, thumb_dst)
            rows.append({
                "id": secrets.token_hex(16), "run_id": run_id, "kind": "input", "idx": None, "seed": None,
                "width": src.row["width"], "height": src.row["height"], "has_alpha": src.row["has_alpha"],
                "bytes": dst.stat().st_size, "path": storage.rel(dst), "thumb_path": storage.rel(thumb_dst),
                "created_at": utcnow(), "position": src.position, "role": src.role,
            })
    except BaseException:
        storage.delete_run_files(run_id)
        raise
    return rows


def shape_dimensions(resolution: int, width: int, height: int) -> tuple[int, int]:
    """The explicit size for a result that should follow an image of this shape, as the pipeline would give it."""
    return P.calculate_dimensions(resolution * resolution, width / height)


def sweep_staged(db: Database, storage: Storage, ttl_hours: int) -> int:
    """Delete uploads no run claimed within the TTL, and files or folders that nothing in the database
    owns any more (left by a crash between writing a file and recording it). Returns how many were removed."""
    removed = 0
    cutoff = format_ts(datetime.now(timezone.utc) - timedelta(hours=ttl_hours))
    while True:
        rows = db.delete_expired_staged(cutoff, SWEEP_BATCH)
        if not rows:
            break
        for row in rows:
            storage.delete_staged_files(row["id"])
        removed += len(rows)

    now = time.time()
    run_ids, staged_ids = db.known_ids()
    for folder, suffix in ((storage.staged, ".png"), (storage.staged_thumbs, ".webp")):
        for path in folder.iterdir() if folder.is_dir() else ():
            if path.suffix == suffix and path.stem not in staged_ids and now - path.stat().st_mtime > ORPHAN_AGE_SECONDS:
                path.unlink(missing_ok=True)
                removed += 1
    if storage.inputs.is_dir():
        for path in storage.inputs.iterdir():
            if (path.is_dir() and path.name != "staged" and len(path.name) == 32 and path.name not in run_ids
                    and now - path.stat().st_mtime > ORPHAN_AGE_SECONDS):
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    if removed:
        log.info("removed %d staged upload(s) or leftover file(s) (STUDIO_UPLOAD_TTL_HOURS=%d)", removed, ttl_hours)
    return removed
