"""Telling the page how far a model's load has got (DESIGN.md section 39).

A model load is two things done one after the other, and until 1.21 the page was told neither: the **download** of the model's files into the Hugging Face cache (the first time a
model is used, and again after a cache clean; several GB), and then **loading** those files into memory (every time). Both take minutes, and all the page ever heard was "loading".

The downloads are done by libraries (`from_pretrained`, `hf_hub_download`, a vLLM server) that the studio does not own, so it does not try to hook them. It looks from outside
instead, at the one thing every one of them does the same way: they write the model's files into the cache folder, as `<cache>/models--Org--Name/blobs/<hash>`, under the name
`<hash>.incomplete` while a file is arriving. A `LoadWatcher` thread in the worker adds up the size of those files about once a second and says so (`load_progress`); the API
process passes it on in the worker's status. That works for every engine and for the image and music models alike, needs no change to what downloads, and cannot slow the load.

What it can say is therefore exactly this and no more: how many bytes of the model are in the cache now; the size the Hub says the model has, where that is known and fits the files
that will be fetched; whether the number is still growing; and how long the load has taken. Loading into memory has no measure, so it is shown as elapsed time only.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger("studio.progress")

# A Hugging Face repository id: "Org/Name". A path on disk (a folder the person made, "/models/x", "./x", "~/x") is not one, and has no download to watch.
_HUB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")


def hub_repo(value: Optional[str]) -> Optional[str]:
    """`value` if it names a Hugging Face repository that is not also a folder here, else None."""
    if not value or not _HUB_ID.match(value) or Path(value).expanduser().is_dir():
        return None
    return value


@dataclass(frozen=True)
class DownloadPlan:
    """What a pipeline will fetch into the cache while it loads, so that the watcher knows where to look.

    `repos` are Hugging Face repository ids. `ask_hub` says whether the repository's whole size, as the Hub gives it, is what will be downloaded: true when the library takes the
    whole repository (a checkpoint, a diffusers pipeline), false when it takes a few files of a big one (Kokoro's voices, one of Piper's thousand), where that size would be
    wrong and none is better than a wrong one. `total_bytes` is for a pipeline that knows the size itself (the fake ones)."""

    repos: tuple[str, ...] = ()
    ask_hub: bool = True
    total_bytes: Optional[int] = None


def plan_for(repo: Optional[str], hub_mode: str = "auto", ask_hub: bool = True) -> Optional[DownloadPlan]:
    """The plan for one model setting: None when it is not a Hub repository (nothing to watch). The Hub is not asked in offline mode, where nothing is downloaded."""
    found = hub_repo(repo)
    return None if found is None else DownloadPlan((found,), ask_hub=ask_hub and hub_mode != "offline")


def hub_cache_dir(env: Optional[dict[str, str]] = None) -> Path:
    """Where the Hugging Face libraries keep downloaded repositories, by their own rule: HF_HUB_CACHE, else HF_HOME/hub, else ~/.cache/huggingface/hub."""
    env = os.environ if env is None else env
    if env.get("HF_HUB_CACHE"):
        return Path(env["HF_HUB_CACHE"]).expanduser()
    home = env.get("HF_HOME") or str(Path("~/.cache/huggingface").expanduser())
    return Path(home).expanduser() / "hub"


def repo_dir(cache: Path, repo: str) -> Path:
    """The cache's folder for a repository: "Qwen/Qwen-Image-2.1" is `models--Qwen--Qwen-Image-2.1`."""
    return cache / ("models--" + repo.replace("/", "--"))


def cache_bytes(cache: Path, repos: Iterable[str]) -> int:
    """The bytes of these repositories in the cache now: every file in each one's `blobs` folder, the ones still arriving (`*.incomplete`) as far as they have got. Never raises."""
    total = 0
    for repo in repos:
        try:
            with os.scandir(repo_dir(cache, repo) / "blobs") as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue  # a file that was renamed as it was looked at: the next look will see it
        except OSError:
            continue  # not there yet
    return total


def fetch_hub_total(repos: Iterable[str], timeout: float = 10.0) -> Optional[int]:
    """The size the Hub gives these repositories (the sum of their files), or None if it cannot be had: no network, a private repository, a library that is not installed."""
    try:
        from huggingface_hub import HfApi
    except Exception:  # noqa: BLE001 (the worker of the fake pipeline has no such library, and a size is only a nicety)
        return None
    total = 0
    try:
        api = HfApi()
        for repo in repos:
            info = api.model_info(repo, files_metadata=True, timeout=timeout)
            sizes = [s.size for s in (info.siblings or []) if getattr(s, "size", None)]
            if not sizes:
                return None
            total += sum(sizes)
    except Exception as exc:  # noqa: BLE001
        log.info("the size of %s could not be had from the Hub (%s)", ", ".join(repos), type(exc).__name__)
        return None
    return total or None


def progress_interval() -> float:
    """Seconds between two reports: `STUDIO_LOAD_PROGRESS_MS` (the browser tests make it short), else one second."""
    try:
        return min(60.0, max(0.05, int(os.environ.get("STUDIO_LOAD_PROGRESS_MS", "1000")) / 1000))
    except ValueError:
        return 1.0


class LoadWatcher:
    """Reports the progress of one load while it runs, from a thread of its own: `start()` before the load, `stop()` after it (in a `finally`).

    Each report is `emit("load_progress", phase=..., done=..., total=..., elapsed=...)`:

    * `done`: bytes of the plan's repositories in the cache now (0 when there is nothing to look at);
    * `total`: the size to expect, or None. A total that the cache has already outgrown was wrong (the Hub lists files the library does not fetch), and is dropped;
    * `phase`: "downloading" while `done` grew in the last `grow_window` seconds (a second and a half, with the one-second reports), else "loading". A download that stalls for longer than
      that is shown as "loading" until it moves again, which is the price of not hooking the downloader. A model that is already in the cache never grows, so it is "loading" throughout;
    * `elapsed`: seconds since `start()`.
    """

    def __init__(self, emit: Callable[..., None], plan: Optional[DownloadPlan], *, cache_dir: Optional[Path] = None, interval: Optional[float] = None,
                 grow_window: Optional[float] = None, clock: Callable[[], float] = time.monotonic, fetch_total: Callable[[Iterable[str]], Optional[int]] = fetch_hub_total):
        self._emit = emit
        self._plan = plan
        self._cache = cache_dir if cache_dir is not None else hub_cache_dir()
        self._interval = interval if interval is not None else progress_interval()
        self._grow_window = grow_window if grow_window is not None else max(0.3, 1.5 * self._interval)
        self._clock = clock
        self._fetch_total = fetch_total
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = clock()
        self._total: Optional[int] = plan.total_bytes if plan else None
        self._last_done: Optional[int] = None
        self._grew_at: Optional[float] = None

    # ------------------------------------------------------------------ one look
    def snapshot(self) -> dict[str, Any]:
        """Look at the cache now and say what is seen. Called by the thread once an interval, and by tests directly."""
        now = self._clock()
        done = cache_bytes(self._cache, self._plan.repos) if self._plan else 0
        if self._last_done is not None and done > self._last_done:
            self._grew_at = now
        self._last_done = done
        total = self._total
        if total is not None and done > total:
            total = None  # the cache holds more than the Hub said: the size does not fit what is fetched, so say none
        downloading = self._grew_at is not None and now - self._grew_at <= self._grow_window
        return {"phase": "downloading" if downloading else "loading", "done": done, "total": total, "elapsed": round(now - self._started, 1)}

    # ------------------------------------------------------------------ the thread
    def start(self) -> None:
        self._started = self._clock()
        self._thread = threading.Thread(target=self._run, name="load-progress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """End the reports. Returns after the last one has been sent, so that nothing is said after the load is over."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            if self._plan is not None and self._plan.ask_hub and self._total is None and self._plan.repos:
                threading.Thread(target=self._ask_hub, name="load-progress-size", daemon=True).start()  # a network call must not hold up the reports
            while True:
                self._emit("load_progress", **self.snapshot())
                if self._stop.wait(self._interval):
                    return
        except Exception:  # noqa: BLE001 (progress is a nicety: whatever goes wrong here must never touch the load)
            log.exception("the load progress watcher stopped")

    def _ask_hub(self) -> None:
        assert self._plan is not None
        try:
            total = self._fetch_total(self._plan.repos)
        except Exception:  # noqa: BLE001 (a size is a nicety; the reports go on without one)
            log.info("the size of the model could not be had")
            return
        if total:
            self._total = total


# ------------------------------------------------------------------ the fake pipelines: a download that can be watched without a network

def fake_download_bytes() -> int:
    """`STUDIO_FAKE_DOWNLOAD_MB` as bytes (0 = off): how large a download the fake pipelines pretend to make, so that the browser tests and the tests of the watcher see a real
    one grow in a real cache folder. The files are sparse, so nothing like that much disk is used."""
    try:
        return max(0, int(os.environ.get("STUDIO_FAKE_DOWNLOAD_MB", "0"))) * 1024 * 1024
    except ValueError:
        return 0


def fake_plan(repo: str) -> Optional[DownloadPlan]:
    size = fake_download_bytes()
    return DownloadPlan((repo,), ask_hub=False, total_bytes=size) if size else None


def fake_load(repo: str, seconds: float) -> None:
    """Take `seconds` to load, and, when a fake download is asked for, spend them downloading: a file grows to its size in the repository's `blobs` folder as `<name>.incomplete`
    and is renamed when it is whole, as the Hub's downloader does it. The folder is made new each time, so that every load downloads."""
    size = fake_download_bytes()
    if not size or seconds <= 0:
        time.sleep(max(0.0, seconds))
        return
    folder = repo_dir(hub_cache_dir(), repo)
    shutil.rmtree(folder, ignore_errors=True)
    blobs = folder / "blobs"
    blobs.mkdir(parents=True)
    partial = blobs / "fake0000.incomplete"
    steps = 10
    with open(partial, "wb") as handle:
        for step in range(1, steps + 1):
            time.sleep(seconds * 0.7 / steps)  # the rest is "loading into memory": nothing arrives
            handle.truncate(size * step // steps)
            handle.flush()
    os.replace(partial, blobs / "fake0000")
    time.sleep(seconds * 0.3)
