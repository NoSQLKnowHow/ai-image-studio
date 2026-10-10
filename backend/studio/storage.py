"""Files on the data volume (DESIGN.md §8).

    <data>/studio.sqlite
    <data>/images/<run>/<idx>.png      written by the worker
    <data>/images/<run>/<idx>-4k.png   the 4K copy of that image, made on request by the API process (DESIGN.md §27)
    <data>/thumbs/<run>/<idx>.webp     made by the API process
    <data>/inputs/staged/<id>.png      an uploaded image that no run has claimed yet (deleted after a day)
    <data>/thumbs/staged/<id>.webp     its thumbnail
    <data>/inputs/<run>/<position>.png the images an edit run was given: copies the run owns (position 1 = "image 1")
    <data>/thumbs/<run>/in-<position>.webp   their thumbnails (in the run's own thumbs folder, so they go with it)
    <data>/audio/<run>/<idx>.wav       the tracks of a music run, written by the worker (DESIGN.md §26.5)

All file access is by database id; paths reported by the worker are checked to be
inside the run's own folder before they are trusted.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

THUMB_MAX = 512
_ID = re.compile(r"^[0-9a-f]{32}$")


class StorageError(Exception):
    pass


def check_id(value: str) -> str:
    if not _ID.match(value or ""):
        raise StorageError(f"Malformed id: {value!r}")
    return value


def has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height: int
    has_alpha: bool
    bytes: int


class Storage:
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir).resolve()
        self.images = self.root / "images"
        self.audio = self.root / "audio"
        self.thumbs = self.root / "thumbs"
        self.inputs = self.root / "inputs"
        self.staged = self.inputs / "staged"
        self.staged_thumbs = self.thumbs / "staged"

    def ensure_layout(self) -> None:
        try:
            for directory in (self.root, self.images, self.audio, self.thumbs, self.inputs, self.staged, self.staged_thumbs):
                directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=self.root):
                pass
        except OSError as exc:
            uid, gid = os.getuid(), os.getgid()
            raise StorageError(
                f"The data directory {self.root} is not writable by this process (uid {uid}, gid {gid}): "
                f"{exc.strerror or exc}. If it's a folder mounted into the container, give it to that user "
                f"on the host: sudo chown -R {uid}:{gid} <the folder>"
            ) from exc

    def run_dir(self, run_id: str) -> Path:
        return self.images / check_id(run_id)

    def audio_dir(self, run_id: str) -> Path:
        return self.audio / check_id(run_id)

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.root).as_posix()

    def abs(self, rel: str) -> Path:
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            raise StorageError(f"Path escapes the data directory: {rel!r}")
        return path

    def four_k_path(self, image_rel: str) -> Path:
        """Where the 4K copy of an image lives (DESIGN.md §27.3): beside it, `<idx>-4k.png`. It goes with the run's folder."""
        path = self.abs(image_rel)
        return path.with_name(f"{path.stem}-4k.png")

    def accept_worker_image(self, rel: str, run_id: str) -> Path:
        """Validate a path reported by the worker: inside this run's folder, an existing file."""
        path = self.abs(rel)
        if path.parent != self.run_dir(run_id).resolve():
            raise StorageError(f"Worker reported an image outside the run folder: {rel!r}")
        if not path.is_file():
            raise StorageError(f"Worker reported a missing image file: {rel!r}")
        return path

    def accept_worker_audio(self, rel: str, run_id: str) -> Path:
        """Validate a path reported by the worker for a track: inside this run's audio folder, an existing file."""
        path = self.abs(rel)
        if path.parent != self.audio_dir(run_id).resolve():
            raise StorageError(f"Worker reported a track outside the run folder: {rel!r}")
        if not path.is_file():
            raise StorageError(f"Worker reported a missing track file: {rel!r}")
        return path

    @staticmethod
    def image_info(path: Path) -> ImageInfo:
        with Image.open(path) as im:
            im.verify()  # cheap integrity check
        with Image.open(path) as im:
            return ImageInfo(im.width, im.height, has_alpha(im), path.stat().st_size)

    def make_thumbnail(self, src: Path, run_id: str, idx: int) -> Path:
        return self.make_thumbnail_at(src, self.thumbs / check_id(run_id) / f"{int(idx)}.webp")

    def make_thumbnail_at(self, src: Path, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        partial = dst.with_name(dst.name + ".part")
        try:
            with Image.open(src) as im:
                im.load()
                thumb = im.convert("RGBA" if has_alpha(im) else "RGB")
            thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
            thumb.save(partial, format="WEBP", quality=80, method=4)
            os.replace(partial, dst)
        finally:
            partial.unlink(missing_ok=True)
        return dst

    def run_inputs_dir(self, run_id: str) -> Path:
        return self.inputs / check_id(run_id)

    def delete_run_files(self, run_id: str) -> None:
        """Everything on disk that belongs to a run: its images or tracks, its inputs and all their thumbnails."""
        check_id(run_id)
        shutil.rmtree(self.images / run_id, ignore_errors=True)
        shutil.rmtree(self.audio / run_id, ignore_errors=True)
        shutil.rmtree(self.thumbs / run_id, ignore_errors=True)
        shutil.rmtree(self.inputs / run_id, ignore_errors=True)

    def delete_staged_files(self, upload_id: str) -> None:
        check_id(upload_id)
        (self.staged / f"{upload_id}.png").unlink(missing_ok=True)
        (self.staged_thumbs / f"{upload_id}.webp").unlink(missing_ok=True)

    def cleanup_partials(self) -> int:
        """Remove half-written files left by a crash. Returns how many were removed."""
        removed = 0
        for path in self.root.rglob("*.part"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed
