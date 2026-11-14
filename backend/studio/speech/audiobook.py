"""The M4B audiobook file (DESIGN.md §44.4): one AAC file with a chapter for each finished part, built with ffmpeg when the person asks for it.

What this module does, in the order it happens:
  * `probe` finds out, once, whether the ffmpeg of `STUDIO_SPEECH_FFMPEG` can make one (it is there, it has an AAC encoder and the MP4 writer) and, when it cannot, says
    why in words; `capabilities.speech.audiobook`, the button and the refusal of a request all say those words;
  * `build_m4b` feeds ffmpeg the samples of the finished parts in pieces of at most 64 KB, straight from the pieces' files, so the memory it needs is the same for a book of
    ten minutes and one of eleven hours; the chapters come from `chapters.py`, so they agree with the ZIP's lists to the millisecond;
  * `Audiobooks` is the part the API talks to: one build at a time, a status the page polls, a file that is kept `STUDIO_SPEECH_TEMP_HOURS` after it was last made or taken,
    and the clean-up of what is left.

Why a file and not a stream: an MPEG-4 file has its index at the end and ffmpeg moves it to the front when it has finished (so a phone can start playing before the whole file
has arrived), which needs an output that can be gone back into. So the file is written in the data folder (the one place the studio knows has room for it), and downloading it
is a separate step that supports range requests. The file is `<data>/speech/audiobooks/<reading>-<signature>.m4b`, written as `….m4b.part` and renamed when complete; the
signature says what went into it (§44.4), so a reading that has made another part since is a different file, and a stale one is never served.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from ..config import Settings
from .audio import PartAudio
from .chapters import Chapter, PartLength, chapters_of, ffmetadata, total_seconds
from .refused import ReadingRefused

log = logging.getLogger("studio.speech.audiobook")

SIGNATURE_VERSION = 1  # bump when what a file is made of changes, so that files built the old way are not served as the new
STALL_SECONDS = 300  # a build whose ffmpeg takes in no samples for this long is stopped (DESIGN.md §44.4)
RECHECK_SECONDS = 30  # how long a "cannot" about ffmpeg is believed before it is looked into again; a "can" is believed for as long as the process lives
DISK_MARGIN = 1.3  # the file's size is estimated from its bit rate, and the estimate is made a third larger
DISK_RESERVE = 256 * 1024 ** 2  # and this much is kept free besides, for the database and the next job
GENRE = "Audiobook"
EXTENSION = ".m4b"
PART_SUFFIX = ".part"


# ---------------------------------------------------------------- can this ffmpeg make one?
@dataclass(frozen=True)
class Tools:
    """What `probe` found: the ffmpeg to run, or why there is none."""

    available: bool
    path: Optional[str] = None
    reason: Optional[str] = None
    hint: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        """`capabilities.speech.audiobook`."""
        return {"available": self.available, "reason": self.reason, "hint": self.hint}


_ZIP_STILL_WORKS = "The ZIP still has every part and its chapter lists."
_probed: dict[str, tuple[float, Tools]] = {}
_probe_lock = threading.Lock()


def _locate(ffmpeg: str) -> Optional[str]:
    """The program `ffmpeg` names: a path that is a file one may run, or a name found on the PATH."""
    if os.sep in ffmpeg or (os.altsep and os.altsep in ffmpeg):
        return ffmpeg if os.path.isfile(ffmpeg) and os.access(ffmpeg, os.X_OK) else None
    return shutil.which(ffmpeg)


def _lists(path: str, flag: str) -> Optional[str]:
    """What `ffmpeg -hide_banner <flag>` (`-encoders` or `-muxers`) prints, or None when it could not be run."""
    try:
        done = subprocess.run([path, "-hide_banner", flag], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def probe(ffmpeg: str, now: Optional[float] = None) -> Tools:
    """Whether the ffmpeg named by `STUDIO_SPEECH_FFMPEG` can make an M4B: it is there, it can be run, it has an AAC encoder and it can write the MP4 family of files. It is run
    to find out (twice, quickly) the first time and the answer is kept: a yes for the life of the process, a no for 30 seconds, so that an ffmpeg that is put in place while
    the studio runs is found without a restart and a page that polls `capabilities` does not start a program each time."""
    now = time.monotonic() if now is None else now
    with _probe_lock:
        known = _probed.get(ffmpeg)
        if known is not None and (known[1].available or now - known[0] < RECHECK_SECONDS):
            return known[1]
    found = _probe_uncached(ffmpeg)
    with _probe_lock:
        _probed[ffmpeg] = (now, found)
    return found


def forget_probes() -> None:
    """Forget what `probe` knows (tests, which change the ffmpeg under the same name)."""
    with _probe_lock:
        _probed.clear()


def _probe_uncached(ffmpeg: str) -> Tools:
    path = _locate(ffmpeg)
    if path is None:
        return Tools(False, None, f"The audiobook file needs ffmpeg, and it was not found on this server (STUDIO_SPEECH_FFMPEG is “{ffmpeg}”).",
                     f"{_ZIP_STILL_WORKS} The studio's image has ffmpeg; if you run the server yourself, install it and restart.")
    encoders = _lists(path, "-encoders")
    if encoders is None:
        return Tools(False, path, f"ffmpeg is there ({path}) but could not be run to find out whether it can make the audiobook file.", _ZIP_STILL_WORKS)
    # a line of the list is `A....D aac    AAC (Advanced Audio Coding)`: the flags, then the name of the encoder
    if not re.search(r"^\s*A\S*\s+aac\s", encoders, flags=re.MULTILINE):
        return Tools(False, path, "The audiobook file needs ffmpeg with an AAC encoder, and this server's ffmpeg has none.",
                     f"{_ZIP_STILL_WORKS} The studio's image has an ffmpeg with one; if you run the server yourself, install a full ffmpeg.")
    muxers = _lists(path, "-muxers")
    if muxers is None or not re.search(r"^\s*\S*E\S*\s+ipod\s", muxers, flags=re.MULTILINE):
        return Tools(False, path, "The audiobook file needs ffmpeg that can write MP4 audiobooks, and this server's ffmpeg cannot.", f"{_ZIP_STILL_WORKS} Install a full ffmpeg.")
    return Tools(True, path)


# ---------------------------------------------------------------- what goes into a file
@dataclass(frozen=True)
class FinishedPart:
    """A finished part with its audio, as the ZIP and the M4B use it."""

    idx: int
    title: str
    audio: PartAudio

    @property
    def length(self) -> PartLength:
        """Its length as the chapter list needs it: whole frames at a rate, from the sizes of the files that will be played."""
        return PartLength(self.title, self.audio.data_bytes // (2 * self.audio.channels), self.audio.sample_rate)


def signature(parts: Sequence[PartLength], title: str, kbps: int) -> str:
    """Twelve characters that say what a file is made of: each finished part's title and length in samples (so another part finished, or a part that was made again, is
    another file), the reading's title, the bit rate, and the version of this recipe."""
    payload = json.dumps([[p.title, p.frames, p.sample_rate] for p in parts] + [title, kbps, SIGNATURE_VERSION], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def estimated_bytes(seconds: float, kbps: int) -> int:
    """About how big the file will be: the audio's length at the bit rate (a kilobit is 125 bytes), and nothing for the little the container adds."""
    return int(seconds * kbps * 125)


class BuildFailed(Exception):
    """A build that did not make a file. The message is for the person, in words; `hint` says what to try."""

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.message, self.hint = message, hint


def ffmpeg_command(path: str, rate: int, meta: Path, out: Path, kbps: int) -> list[str]:
    """The ffmpeg that makes the file: raw 16-bit mono samples on its standard input, the chapters and tags from the metadata file, AAC at `kbps`, and the index moved to the
    front of the file when it is finished. Every value is a separate argument: nothing here goes through a shell."""
    return [path, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
            "-i", str(meta), "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
            "-c:a", "aac", "-b:a", f"{kbps}k", "-movflags", "+faststart", "-f", "ipod",
            # the file is written under another name (`.m4b.part`) and renamed, so ffmpeg cannot tell from the name that it is an audiobook and would write the brand of a plain
            # AAC file (`M4A `); the brand is said outright, which is what the players that sniff the file instead of trusting its extension look for
            "-brand", "M4B ", str(out)]


def build_m4b(path: str, parts: Sequence[FinishedPart], title: str, note: str, kbps: int, out: Path, on_progress: Callable[[int, int, int], None],
              *, stall_seconds: float = STALL_SECONDS, popen: Callable[..., Any] = subprocess.Popen) -> list[Chapter]:
    """Make the M4B at `out`, from the samples of `parts`, and return its chapters. `on_progress(bytes_fed, bytes_in_all, part_number)` is called as the samples go in.

    The samples reach ffmpeg in the pieces `PartAudio.iter_bytes` gives (at most 64 KB), one after the other, so the book is never in memory. The file is written to
    `out` with `.part` added and renamed when ffmpeg has finished well; anything left over is removed. Raises BuildFailed."""
    if not parts:
        raise BuildFailed("No part of this reading has been finished yet.")
    rates = {p.audio.sample_rate for p in parts}
    if len(rates) != 1 or {p.audio.channels for p in parts} != {1}:
        raise BuildFailed("The parts of this reading are not all the same kind of audio, so they cannot be made into one file.")
    (rate,) = rates
    chapters = chapters_of([p.length for p in parts])
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + PART_SUFFIX)
    meta = out.with_name(out.name + ".meta")
    total = sum(p.audio.data_bytes for p in parts)
    meta.write_text(ffmetadata(chapters, title, {"genre": GENRE, "comment": note}), encoding="utf-8")
    process = None
    tail: list[bytes] = []
    last = [time.monotonic()]  # when ffmpeg last took anything in: the watchdog's clock
    stalled = threading.Event()
    done = threading.Event()
    try:
        process = popen(ffmpeg_command(path, rate, meta, partial, kbps), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        def drain() -> None:
            """Keep ffmpeg's messages (its errors, with `-loglevel error`), the last few thousand characters of them, and never let its pipe fill."""
            for line in iter(process.stderr.readline, b""):
                tail.append(line)
                del tail[:-40]

        def watch() -> None:
            while not done.wait(1.0):
                if time.monotonic() - last[0] > stall_seconds:
                    stalled.set()
                    process.kill()
                    return

        threading.Thread(target=drain, daemon=True, name="m4b-stderr").start()
        threading.Thread(target=watch, daemon=True, name="m4b-watch").start()
        fed = 0
        try:
            for number, part in enumerate(parts, 1):
                for chunk in part.audio.iter_bytes(start=len(part.audio.header)):  # the samples only, without the WAV header
                    process.stdin.write(chunk)
                    fed += len(chunk)
                    last[0] = time.monotonic()
                    on_progress(fed, total, number)
            process.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            if not stalled.is_set():  # ffmpeg stopped reading: it has said why (below)
                process.wait()
                raise BuildFailed("ffmpeg stopped before it had all the audio." + _said(tail), "Check that STUDIO_SPEECH_FFMPEG is a working ffmpeg.") from exc
        returncode = process.wait()
        if stalled.is_set():
            raise BuildFailed(f"ffmpeg took in no audio for {int(stall_seconds)} seconds, so it was stopped.", "Try again; if it keeps stopping, check the load on the machine.")
        if returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
            raise BuildFailed(f"ffmpeg could not make the file (it ended with code {returncode})." + _said(tail), "Check that STUDIO_SPEECH_FFMPEG is a working ffmpeg.")
        os.replace(partial, out)
        return chapters
    except OSError as exc:  # the data folder could not be written, or ffmpeg could not be started
        raise BuildFailed(f"The audiobook file could not be made: {exc.strerror or exc}", "Check the disk space, and that STUDIO_SPEECH_FFMPEG is a working ffmpeg.") from exc
    finally:
        done.set()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        partial.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)


def _said(tail: Sequence[bytes]) -> str:
    """ffmpeg's own last words, for the message, or nothing."""
    text = b"".join(tail).decode("utf-8", errors="replace").strip()
    return f" It said: {text[-400:]}" if text else ""


# ---------------------------------------------------------------- the part the API talks to
class _Build:
    """A build that is waiting its turn, under way, or failed (a finished one is a file on the disk, not an entry here)."""

    def __init__(self, signature: str, total_bytes: int):
        self.signature = signature
        self.state = "waiting"
        self.fed = 0
        self.total_bytes = total_bytes
        self.part = 0
        self.error: Optional[dict[str, Optional[str]]] = None


class Audiobooks:
    """The audiobook files of every reading: what can be made, what is being made, and the files that are kept. `speech` is the `SpeechService`, which knows the parts."""

    def __init__(self, settings: Settings, directory: Path, speech: Any):
        self.settings, self.dir, self.speech = settings, directory, speech
        self._builds: dict[str, _Build] = {}
        self._lock = threading.Lock()
        self._turn = threading.Semaphore(1)  # one build at a time: ffmpeg uses a processor for as long as the book is long

    # -------------------------------------------------------------- what can be made
    def tools(self) -> Tools:
        return probe(self.settings.speech_ffmpeg)

    def path_for(self, run_id: str, sig: str) -> Path:
        return self.dir / f"{run_id}-{sig}{EXTENSION}"

    def plan(self, row: Any) -> dict[str, Any]:
        """What a build would be made of now, worked out from the database (no audio file is opened): the finished parts, with the signature of the file they make."""
        totals = self.speech.store.totals([row["id"]])[row["id"]]
        lengths, total_parts = [], len(totals["parts"])
        for part in totals["parts"]:
            if part["pieces"] and part["made"] == part["pieces"] and part["sample_rate"]:
                lengths.append(PartLength(part["title"], int(part["frames"]), int(part["sample_rate"])))
        title = json.loads(row["options_json"]).get("title") or row["prompt"]
        return {"lengths": lengths, "parts_total": total_parts, "title": title, "signature": signature(lengths, title, self.settings.speech_audiobook_kbps),
                "seconds": float(total_seconds(lengths)), "chapters": chapters_of(lengths)}

    def status(self, row: Any) -> dict[str, Any]:
        """Where the audiobook of a reading stands, for the page (§44.6): what can be made, and whether a file is ready, being made, or failed."""
        plan = self.plan(row)
        tools = self.tools()
        out: dict[str, Any] = {
            **tools.as_dict(), "state": "none" if not plan["lengths"] else "idle", "parts_made": len(plan["lengths"]), "parts_total": plan["parts_total"],
            "complete": len(plan["lengths"]) == plan["parts_total"] and plan["parts_total"] > 0, "seconds": round(plan["seconds"], 3), "chapters": len(plan["chapters"]),
            "kbps": self.settings.speech_audiobook_kbps, "estimated_bytes": estimated_bytes(plan["seconds"], self.settings.speech_audiobook_kbps),
            "progress": None, "part": None, "bytes": None, "url": None, "error": None,
        }
        if not plan["lengths"]:
            return out
        file = self.path_for(row["id"], plan["signature"])
        with self._lock:
            build = self._builds.get(row["id"])
            if build is not None and build.signature != plan["signature"] and build.state == "failed":
                build = None  # a failure of other parts, which are not these
        if file.is_file():
            out.update(state="ready", bytes=file.stat().st_size, url=f"/api/speech/{row['id']}/audiobook.m4b", progress=1.0)
        elif build is not None and build.signature == plan["signature"]:
            out.update(state=build.state, progress=round(build.fed / build.total_bytes, 4) if build.total_bytes else 0.0, part=build.part or None, error=build.error)
        return out

    def start(self, row: Any) -> dict[str, Any]:
        """Start making the audiobook of a reading, unless a file for these parts is there or being made. Raises ReadingRefused (409 when ffmpeg cannot make one or there is
        nothing finished yet, 507 when the disk is too full). Returns the status."""
        plan = self.plan(row)
        if not plan["lengths"]:
            raise ReadingRefused(409, "not_finished", "No part of this reading has been finished yet, so there is nothing to make an audiobook of.")
        tools = self.tools()
        if not tools.available:
            raise ReadingRefused(409, "audiobook_unavailable", tools.reason or "The audiobook file cannot be made here.", None, tools.hint)
        run_id = row["id"]
        if self.path_for(run_id, plan["signature"]).is_file():
            return self.status(row)
        with self._lock:
            known = self._builds.get(run_id)
            if known is not None and known.signature == plan["signature"] and known.state in ("waiting", "building"):
                return self.status(row)
        needed = int(estimated_bytes(plan["seconds"], self.settings.speech_audiobook_kbps) * DISK_MARGIN) + DISK_RESERVE
        self.dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.dir).free
        if free < needed:
            raise ReadingRefused(507, "storage_full", f"The audiobook file needs about {needed / 1024 ** 3:.1f} GB of disk and {free / 1024 ** 3:.1f} GB is free.", None,
                                 "Delete readings you no longer need, or free disk space, and try again. The ZIP does not need the room.")
        total = sum(p.frames * 2 for p in plan["lengths"])
        with self._lock:
            self._builds[run_id] = _Build(plan["signature"], total)
        threading.Thread(target=self._run, args=(row, plan), daemon=True, name=f"m4b-{run_id[:8]}").start()
        return self.status(row)

    def file_for(self, row: Any) -> Optional[Path]:
        """The file for the parts finished now, if it is there: it is touched, so that taking it again keeps it for another `STUDIO_SPEECH_TEMP_HOURS`."""
        file = self.path_for(row["id"], self.plan(row)["signature"])
        if not file.is_file():
            return None
        try:
            os.utime(file)
        except OSError:
            pass
        return file

    # -------------------------------------------------------------- making it
    def _run(self, row: Any, plan: dict[str, Any]) -> None:
        """The build, on a thread of its own: wait for the turn, make the file, say how it went. Never raises (the page learns from the status)."""
        run_id = row["id"]
        build = self._builds[run_id]
        try:
            with self._turn:
                with self._lock:
                    build.state = "building"
                parts = self.speech.finished_parts(row)
                # the lengths the file is made from are the files' own, and must be the ones the signature was made from; a part made again since means another file
                if [p.length for p in parts] != plan["lengths"]:
                    raise BuildFailed("The audio of the reading changed while the file was being started.", "Try again.")

                def progress(fed: int, _total: int, part: int) -> None:
                    build.fed, build.part = fed, part

                build_m4b(self.tools().path or self.settings.speech_ffmpeg, parts, plan["title"], self._note(row), self.settings.speech_audiobook_kbps,
                          self.path_for(run_id, plan["signature"]), progress, stall_seconds=STALL_SECONDS)  # (looked up now, so that a test can make it short)
            with self._lock:
                self._builds.pop(run_id, None)
            self._remove_others(run_id, keep=self.path_for(run_id, plan["signature"]))
            log.info("audiobook for reading %s made: %d chapter(s)", run_id, len(plan["chapters"]))
        except BuildFailed as exc:
            log.warning("audiobook for reading %s failed: %s", run_id, exc.message)
            with self._lock:
                build.state, build.error = "failed", {"message": exc.message, "hint": exc.hint}
        except Exception as exc:  # a bug here must not leave a build "building" for ever
            log.exception("audiobook for reading %s crashed", run_id)
            with self._lock:
                build.state, build.error = "failed", {"message": f"The audiobook file could not be made ({type(exc).__name__}).", "hint": "The studio's log has the details."}

    def _note(self, row: Any) -> str:
        """The note in the file's tags, as the WAVs carry theirs (DESIGN.md §38.4): that it is machine-generated speech, by what, and the engine's licence notice."""
        from .service import audio_info

        options = json.loads(row["options_json"])
        return audio_info(options, "", row["created_at"])["comment"]

    def _remove_others(self, run_id: str, keep: Path) -> None:
        """The older files of this reading (made from fewer parts) go once the new one is there."""
        for other in self.dir.glob(f"{run_id}-*{EXTENSION}"):
            if other != keep:
                other.unlink(missing_ok=True)

    # -------------------------------------------------------------- tidying up
    def sweep(self, now: Optional[float] = None) -> dict[str, int]:
        """Remove files kept longer than `STUDIO_SPEECH_TEMP_HOURS` since they were made or last taken, and any half-made file that no build here is making (a restart left
        it). Returns how many of each went."""
        now = time.time() if now is None else now
        cutoff = now - self.settings.speech_temp_hours * 3600
        removed = {"files": 0, "partial": 0}
        if not self.dir.is_dir():
            return removed
        with self._lock:
            active = {run_id for run_id, b in self._builds.items() if b.state in ("waiting", "building")}
        for path in self.dir.iterdir():
            run_id = path.name.split("-", 1)[0]
            try:
                if path.name.endswith(EXTENSION) and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    removed["files"] += 1
                elif (path.name.endswith(EXTENSION + PART_SUFFIX) or path.name.endswith(EXTENSION + ".meta")) and run_id not in active:
                    path.unlink(missing_ok=True)
                    removed["partial"] += 1
            except OSError:
                log.warning("could not remove %s", path, exc_info=True)
        return removed
