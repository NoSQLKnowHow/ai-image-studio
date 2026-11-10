"""How far a model's load has got, as the page can be told (DESIGN.md section 39).

Until 1.21 the only word the page had during a load was "loading", through a download of several GB and then the loading into memory. The worker now watches the model's files arrive in
the Hugging Face cache (`LoadWatcher`) and reports it about once a second; the API process keeps the last report in the worker's status as `progress`. These tests try each piece:

* the pure parts (what is a repository, where the cache is, what its files add up to);
* the watcher, with a clock that is moved by hand, and then as the thread it is;
* what each pipeline and engine says it will download (a wrong repository would watch a folder that never fills);
* the worker: the reports come between "loading" and "ready" or the failure, and never after;
* the server: the status says it, says what was unloaded to make room, and forgets it when the load is over.

Nothing here downloads anything. The fake pipelines pretend to (`STUDIO_FAKE_DOWNLOAD_MB`): a sparse file grows in a cache folder the test made, which is exactly what the watcher looks at.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import pytest
from conftest import wait_for_worker_state

import studio.pipelines.progress as P
from studio.pipelines.fake import FakePipeline
from studio.pipelines.progress import DownloadPlan, LoadWatcher
from studio.speech.engine import EngineSettings
from studio.speech.registry import make_engine
from studio.worker import Worker

MB = 1024 * 1024


def fill(cache: Path, repo: str, files: dict[str, int]) -> None:
    """Put files of these sizes (sparse: only their size is real) into a repository's `blobs` folder of a cache."""
    blobs = P.repo_dir(cache, repo) / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    for name, size in files.items():
        with open(blobs / name, "wb") as handle:
            handle.truncate(size)


class Clock:
    """A clock that moves only when told to, so that "a second later" is exact."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ------------------------------------------------------------------ the pure parts

@pytest.mark.parametrize("value, expected", [
    ("Qwen/Qwen-Image-2.1", "Qwen/Qwen-Image-2.1"),
    ("hexgrad/Kokoro-82M", "hexgrad/Kokoro-82M"),
    ("mistralai/Voxtral-4B-TTS-2603", "mistralai/Voxtral-4B-TTS-2603"),
    ("/models/mine", None),            # a folder on disk
    ("./mine", None),
    ("../mine", None),
    ("~/mine", None),
    ("just-a-name", None),             # not Org/Name
    ("a/b/c", None),
    ("bad name/with space", None),
    ("", None),
    (None, None),
])
def test_what_is_a_hub_repository(value, expected):
    assert P.hub_repo(value) == expected


def test_a_folder_that_looks_like_a_repository_is_a_folder(tmp_path, monkeypatch):
    """`Org/Name` that is also a folder here is the folder (the libraries load a path that exists before they try the Hub), so there is nothing to watch."""
    (tmp_path / "org" / "name").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert P.hub_repo("org/name") is None


def test_the_cache_folder_follows_the_hub_libraries_own_rule():
    assert P.hub_cache_dir({"HF_HUB_CACHE": "/a", "HF_HOME": "/b"}) == Path("/a")
    assert P.hub_cache_dir({"HF_HOME": "/b"}) == Path("/b/hub")
    assert P.hub_cache_dir({}) == Path("~/.cache/huggingface").expanduser() / "hub"


def test_a_repository_is_a_folder_named_models_org_name():
    assert P.repo_dir(Path("/c"), "Qwen/Qwen3-TTS-12Hz-1.7B-Base") == Path("/c/models--Qwen--Qwen3-TTS-12Hz-1.7B-Base")


def test_the_bytes_in_the_cache_are_the_blobs_of_the_repositories_asked_about(tmp_path):
    fill(tmp_path, "a/one", {"whole": 100, "arriving.incomplete": 50})
    fill(tmp_path, "b/two", {"whole": 1000})
    fill(tmp_path, "c/other", {"whole": 7})                     # another tool's model: never counted
    (P.repo_dir(tmp_path, "a/one") / "snapshots" / "rev").mkdir(parents=True)
    (P.repo_dir(tmp_path, "a/one") / "snapshots" / "rev" / "link").symlink_to("../../blobs/whole")   # the snapshot's links are the same bytes again
    assert P.cache_bytes(tmp_path, ["a/one"]) == 150                                  # a file still arriving counts as far as it has got
    assert P.cache_bytes(tmp_path, ["a/one", "b/two"]) == 1150
    assert P.cache_bytes(tmp_path, ["nobody/nothing"]) == 0                           # not there yet: not an error
    assert P.cache_bytes(tmp_path / "no-cache-at-all", ["a/one"]) == 0
    assert P.cache_bytes(tmp_path, []) == 0


def test_the_hub_is_not_asked_for_a_size_where_nothing_is_downloaded():
    assert P.plan_for("Qwen/Qwen-Image-2.1", "auto").ask_hub is True
    assert P.plan_for("Qwen/Qwen-Image-2.1", "online").ask_hub is True
    assert P.plan_for("Qwen/Qwen-Image-2.1", "offline").ask_hub is False
    assert P.plan_for("hexgrad/Kokoro-82M", "auto", ask_hub=False).ask_hub is False
    assert P.plan_for("/models/mine") is None


@pytest.mark.parametrize("raw, expected", [(None, 1.0), ("250", 0.25), ("0", 0.05), ("999999", 60.0), ("soon", 1.0)])
def test_the_interval_between_reports(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("STUDIO_LOAD_PROGRESS_MS", raising=False)
    else:
        monkeypatch.setenv("STUDIO_LOAD_PROGRESS_MS", raw)
    assert P.progress_interval() == expected


# ------------------------------------------------------------------ the size the Hub gives

def fake_hub(monkeypatch, sizes_by_repo: dict, fail: bool = False):
    """A `huggingface_hub` that knows these repositories' file sizes, put where `import huggingface_hub` finds it."""
    calls: list[tuple] = []

    class Api:
        def model_info(self, repo, files_metadata=False, timeout=None):
            calls.append((repo, files_metadata, timeout))
            if fail:
                raise OSError("no network")
            return types.SimpleNamespace(siblings=[types.SimpleNamespace(size=s) for s in sizes_by_repo[repo]])

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(HfApi=Api))
    return calls


def test_the_size_is_the_sum_of_the_files_the_hub_lists(monkeypatch):
    calls = fake_hub(monkeypatch, {"a/one": [100, 200, None, 0], "b/two": [1000]})
    assert P.fetch_hub_total(["a/one", "b/two"]) == 1300
    assert [c[0] for c in calls] == ["a/one", "b/two"] and all(c[1] is True for c in calls)   # with the files' sizes asked for, and a time limit
    assert all(c[2] and c[2] > 0 for c in calls)


def test_no_size_when_the_hub_cannot_say(monkeypatch):
    fake_hub(monkeypatch, {"a/one": [100]}, fail=True)
    assert P.fetch_hub_total(["a/one"]) is None                                       # no network
    fake_hub(monkeypatch, {"a/one": [None, 0]})
    assert P.fetch_hub_total(["a/one"]) is None                                       # a repository that lists no sizes
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    assert P.fetch_hub_total(["a/one"]) is None                                       # the library is not installed (the fake pipelines' worker)


# ------------------------------------------------------------------ one look at a time (a clock moved by hand)

def watcher_on(tmp_path, repos=("a/one",), total=None, **kw):
    clock = Clock()
    watcher = LoadWatcher(lambda *a, **k: None, DownloadPlan(tuple(repos), ask_hub=False, total_bytes=total), cache_dir=tmp_path, interval=1.0, clock=clock,
                          fetch_total=lambda repos: None, **kw)
    return watcher, clock


def test_a_first_download_is_seen_as_soon_as_the_files_start_to_arrive(tmp_path):
    watcher, clock = watcher_on(tmp_path)
    first = watcher.snapshot()
    assert (first["phase"], first["done"], first["elapsed"]) == ("loading", 0, 0.0)  # nothing there yet: the library is still finding out what to fetch
    fill(tmp_path, "a/one", {"x.incomplete": 10})
    clock.advance(1)
    grown = watcher.snapshot()
    assert (grown["phase"], grown["done"]) == ("downloading", 10)                    # from nothing to something is growth


def test_downloading_while_the_cache_grows_and_loading_once_it_has_stopped(tmp_path):
    fill(tmp_path, "a/one", {"x.incomplete": 10})                                     # a partial download left from before: the baseline, which is not growth
    watcher, clock = watcher_on(tmp_path)
    assert watcher.snapshot()["phase"] == "loading"
    clock.advance(1)
    assert watcher.snapshot()["phase"] == "loading"                                   # and it stays as it is
    fill(tmp_path, "a/one", {"x.incomplete": 25})
    clock.advance(1)
    grown = watcher.snapshot()
    assert (grown["phase"], grown["done"], grown["elapsed"]) == ("downloading", 25, 2.0)
    clock.advance(1)
    assert watcher.snapshot()["phase"] == "downloading"                               # a second without growth is still a download (the window is a second and a half)
    clock.advance(1)
    assert watcher.snapshot()["phase"] == "loading"                                   # two seconds is not: it is being loaded into memory
    fill(tmp_path, "a/one", {"x.incomplete": 60})
    clock.advance(1)
    assert watcher.snapshot()["phase"] == "downloading"                               # and moves again


def test_a_model_that_is_already_in_the_cache_is_loading_throughout(tmp_path):
    fill(tmp_path, "a/one", {"whole": 4 * MB})
    watcher, clock = watcher_on(tmp_path)
    for _ in range(5):
        shot = watcher.snapshot()
        assert (shot["phase"], shot["done"]) == ("loading", 4 * MB)
        clock.advance(1)


def test_the_total_is_dropped_once_the_cache_has_outgrown_it(tmp_path):
    fill(tmp_path, "a/one", {"x": 50})
    watcher, clock = watcher_on(tmp_path, total=100)
    assert watcher.snapshot()["total"] == 100
    fill(tmp_path, "a/one", {"x": 100})
    clock.advance(1)
    assert watcher.snapshot()["total"] == 100                                     # equal is fine: the download is whole
    fill(tmp_path, "a/one", {"x": 101})
    clock.advance(1)
    assert watcher.snapshot()["total"] is None                                    # more than was said to come: the size did not fit what is fetched, so none is shown


def test_with_nothing_to_watch_only_the_time_is_reported(tmp_path):
    clock = Clock()
    watcher = LoadWatcher(lambda *a, **k: None, None, cache_dir=tmp_path, interval=1.0, clock=clock)
    clock.advance(12.34)
    assert watcher.snapshot() == {"phase": "loading", "done": 0, "total": None, "elapsed": 12.3}


# ------------------------------------------------------------------ the thread

class Events:
    """What the worker's `emit` would write, collected; safe for the thread that fills it."""

    def __init__(self) -> None:
        self.items: list[tuple[str, dict]] = []
        self.lock = threading.Lock()

    def __call__(self, event: str, **fields) -> None:
        with self.lock:
            self.items.append((event, fields))

    def named(self, name: str) -> list[dict]:
        with self.lock:
            return [f for e, f in self.items if e == name]


def wait_until(condition, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "the condition never came true"
        time.sleep(0.01)


def test_the_thread_reports_until_it_is_stopped_and_not_a_moment_after(tmp_path):
    events = Events()
    watcher = LoadWatcher(events, DownloadPlan(("a/one",), ask_hub=False), cache_dir=tmp_path, interval=0.02)
    watcher.start()
    wait_until(lambda: len(events.named("load_progress")) >= 3)
    watcher.stop()
    count = len(events.named("load_progress"))
    time.sleep(0.15)
    assert len(events.named("load_progress")) == count                            # nothing said after stop() returned
    assert set(events.named("load_progress")[0]) == {"phase", "done", "total", "elapsed"}
    elapsed = [f["elapsed"] for f in events.named("load_progress")]
    assert elapsed == sorted(elapsed)


def test_the_hub_is_asked_once_and_only_when_it_should_be(tmp_path):
    asked: list = []
    events = Events()

    def hub(repos):
        asked.append(tuple(repos))
        return 7 * MB

    watcher = LoadWatcher(events, DownloadPlan(("a/one", "b/two"), ask_hub=True), cache_dir=tmp_path, interval=0.02, fetch_total=hub)
    watcher.start()
    wait_until(lambda: any(f["total"] == 7 * MB for f in events.named("load_progress")))
    watcher.stop()
    assert asked == [("a/one", "b/two")]
    for plan in (DownloadPlan(("a/one",), ask_hub=False), DownloadPlan(("a/one",), ask_hub=True, total_bytes=5), DownloadPlan((), ask_hub=True), None):
        asked.clear()
        quiet = LoadWatcher(Events(), plan, cache_dir=tmp_path, interval=0.02, fetch_total=hub)
        quiet.start()
        time.sleep(0.1)
        quiet.stop()
        assert asked == [], plan                                                  # the plan said no size is wanted, or knows it, or has nothing to look at


def test_a_hub_that_fails_does_not_stop_the_reports(tmp_path):
    events = Events()

    def broken(repos):
        raise RuntimeError("the Hub is down")

    watcher = LoadWatcher(events, DownloadPlan(("a/one",), ask_hub=True), cache_dir=tmp_path, interval=0.02, fetch_total=broken)
    watcher.start()
    wait_until(lambda: len(events.named("load_progress")) >= 3)
    watcher.stop()
    assert all(f["total"] is None for f in events.named("load_progress"))


def test_a_report_that_cannot_be_sent_ends_the_watching_and_nothing_else(tmp_path):
    """Progress is a nicety: if the line cannot be written, the thread ends, quietly, and `stop()` still returns."""
    def broken(event, **fields):
        raise BrokenPipeError("the server went away")

    watcher = LoadWatcher(broken, None, cache_dir=tmp_path, interval=0.02)
    watcher.start()
    time.sleep(0.1)
    watcher.stop()                                                                # no exception


def test_stop_without_start_is_harmless(tmp_path):
    LoadWatcher(Events(), None, cache_dir=tmp_path).stop()


# ------------------------------------------------------------------ the fake download

def test_the_fake_download_grows_a_file_in_the_cache_and_then_makes_it_whole(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setenv("STUDIO_FAKE_DOWNLOAD_MB", "5")
    plan = P.fake_plan("fake/image")
    assert plan == DownloadPlan(("fake/image",), ask_hub=False, total_bytes=5 * MB)
    seen: list[int] = []
    thread = threading.Thread(target=P.fake_load, args=("fake/image", 0.4))
    thread.start()
    while thread.is_alive():
        seen.append(P.cache_bytes(tmp_path, ["fake/image"]))
        time.sleep(0.01)
    thread.join()
    assert seen == sorted(seen) and 0 < len({*seen}) and max(seen) <= 5 * MB          # it grew, and never shrank
    assert any(0 < s < 5 * MB for s in seen)                                          # and was seen part-way
    blobs = list((P.repo_dir(tmp_path, "fake/image") / "blobs").iterdir())
    assert [b.name for b in blobs] == ["fake0000"] and blobs[0].stat().st_size == 5 * MB   # whole, and renamed from .incomplete as the Hub's downloader does


def test_the_fake_download_starts_again_each_time_and_is_off_unless_asked_for(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("STUDIO_FAKE_DOWNLOAD_MB", raising=False)
    assert P.fake_plan("fake/image") is None
    start = time.monotonic()
    P.fake_load("fake/image", 0.05)
    assert time.monotonic() - start >= 0.05 and not (tmp_path / "models--fake--image").exists()   # it only waited
    monkeypatch.setenv("STUDIO_FAKE_DOWNLOAD_MB", "1")
    P.fake_load("fake/image", 0.05)
    fill(tmp_path, "fake/image", {"leftover": 3})
    P.fake_load("fake/image", 0.05)
    assert [b.name for b in (P.repo_dir(tmp_path, "fake/image") / "blobs").iterdir()] == ["fake0000"]   # the folder is made new: every load downloads
    monkeypatch.setenv("STUDIO_FAKE_DOWNLOAD_MB", "lots")
    assert P.fake_download_bytes() == 0


# ------------------------------------------------------------------ what each pipeline and engine says it will download

def plan_of(engine: str, **settings) -> DownloadPlan | None:
    return make_engine(engine, EngineSettings(**settings)).download_plan()


def test_the_image_and_music_models_are_whole_repositories():
    from studio.pipelines.real import RealPipeline
    from studio.pipelines.real_music import RealMusicPipeline

    assert RealPipeline("Qwen/Qwen-Image-2.1").download_plan() == DownloadPlan(("Qwen/Qwen-Image-2.1",), ask_hub=True)
    assert RealMusicPipeline("MiniMaxAI/MiniMax-Music3").download_plan() == DownloadPlan(("MiniMaxAI/MiniMax-Music3",), ask_hub=True)
    assert RealPipeline("Qwen/Qwen-Image-2.1", hub_mode="offline").download_plan().ask_hub is False   # nothing is downloaded, so no size is asked for
    assert RealPipeline("/models/my-copy").download_plan() is None                                   # a folder on disk
    assert RealMusicPipeline("/models/my-copy").download_plan() is None


@pytest.mark.parametrize("variant, repo", [
    ("custom", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"),
    ("design", "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"),
    ("clone", "Qwen/Qwen3-TTS-12Hz-1.7B-Base"),
])
def test_qwen3_tts_watches_the_checkpoint_of_the_variant_that_is_loading(variant, repo):
    """It is three checkpoints, one for each kind of voice, and the one that is loading is the one whose folder fills."""
    assert plan_of("qwen3-tts", variant=variant) == DownloadPlan((repo,), ask_hub=True)


def test_qwen3_tts_follows_the_setting_that_moves_its_checkpoints(tmp_path):
    assert plan_of("qwen3-tts", variant="design", model="myorg").repos == ("myorg/Qwen3-TTS-12Hz-1.7B-VoiceDesign",)    # a namespace of one's own copies
    assert plan_of("qwen3-tts", variant="design", model="myorg/").repos == ("myorg/Qwen3-TTS-12Hz-1.7B-VoiceDesign",)   # (a trailing slash is allowed)
    assert plan_of("qwen3-tts", variant="design", model=str(tmp_path)) is None                                          # a folder on disk
    assert plan_of("qwen3-tts", variant="design", hub_mode="offline").ask_hub is False


def test_kokoro_and_piper_take_a_few_files_of_a_big_repository_so_no_size_is_offered(tmp_path):
    assert plan_of("kokoro") == DownloadPlan(("hexgrad/Kokoro-82M",), ask_hub=False)
    assert plan_of("kokoro", model="someone/kokoro-fork").repos == ("someone/kokoro-fork",)
    assert plan_of("kokoro", model=str(tmp_path)) is None
    assert plan_of("piper") == DownloadPlan(("rhasspy/piper-voices",), ask_hub=False)
    assert plan_of("piper", model="/voices/jenny.onnx") is None                                                          # a voice file on disk
    assert plan_of("piper", model="en_GB-jenny").repos == ("rhasspy/piper-voices",)                                      # not a file: the engine ignores it and so does this


def test_voxtral_watches_the_repository_that_its_server_fetches():
    assert plan_of("voxtral") == DownloadPlan(("mistralai/Voxtral-4B-TTS-2603",), ask_hub=True)
    assert plan_of("voxtral", model="myorg/voxtral-copy").repos == ("myorg/voxtral-copy",)


def test_the_fake_engines_and_pipelines_say_nothing_unless_a_download_is_asked_for(monkeypatch):
    monkeypatch.delenv("STUDIO_FAKE_DOWNLOAD_MB", raising=False)
    assert plan_of("fake") is None and FakePipeline().download_plan() is None
    monkeypatch.setenv("STUDIO_FAKE_DOWNLOAD_MB", "3")
    assert plan_of("fake", variant="design").repos == ("fake/speech-fake-design",)
    assert FakePipeline().download_plan().total_bytes == 3 * MB


# ------------------------------------------------------------------ the worker: reports between "loading" and "ready", never after

def run_load(tmp_path, monkeypatch, pipeline=None, **env):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("STUDIO_LOAD_PROGRESS_MS", "40")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    events = Events()
    worker = Worker(pipeline or FakePipeline(step_delay_ms=1), tmp_path / "data", events)
    result = worker.load()
    time.sleep(0.15)  # long enough for a report that came after the end to show itself
    return result, events


def test_the_worker_reports_the_download_between_loading_and_ready(tmp_path, monkeypatch):
    result, events = run_load(tmp_path, monkeypatch, STUDIO_FAKE_DOWNLOAD_MB="6", STUDIO_FAKE_LOAD_DELAY_MS="600")
    assert result is True
    names = [e for e, _ in events.items]
    assert names[0] == "state" and events.items[0][1] == {"state": "loading"}
    assert names[-1] == "state" and events.items[-1][1]["state"] == "ready"
    assert names.count("load_progress") >= 5 and set(names[1:-1]) == {"load_progress"}      # reports in between, and nothing after "ready"
    done = [f["done"] for f in events.named("load_progress")]
    assert done == sorted(done) and max(done) == 6 * MB and min(done) < 6 * MB                # it grew to the whole of it
    assert {f["total"] for f in events.named("load_progress")} == {6 * MB}
    assert "downloading" in {f["phase"] for f in events.named("load_progress")}


def test_the_worker_stops_reporting_when_the_load_fails(tmp_path, monkeypatch):
    result, events = run_load(tmp_path, monkeypatch, STUDIO_FAKE_DOWNLOAD_MB="6", STUDIO_FAKE_LOAD_DELAY_MS="300", STUDIO_FAKE_LOAD_FAIL="1")
    assert result is False
    names = [e for e, _ in events.items]
    assert names[0] == "state" and names[-1] == "load_failed"                                 # nothing after the failure


def test_a_pipeline_that_says_nothing_about_downloads_still_loads_and_still_reports_the_time(tmp_path, monkeypatch):
    class Plain:
        name = "plain"

        def load(self):
            time.sleep(0.2)
            return {}

    result, events = run_load(tmp_path, monkeypatch, pipeline=Plain())
    assert result is True
    reports = events.named("load_progress")
    assert reports and all(f["done"] == 0 and f["total"] is None and f["phase"] == "loading" for f in reports)


def test_a_pipeline_that_cannot_say_what_it_will_download_does_not_fail_the_load(tmp_path, monkeypatch):
    class Odd:
        name = "odd"

        def download_plan(self):
            raise RuntimeError("no idea")

        def load(self):
            return {"ok": True}

    result, events = run_load(tmp_path, monkeypatch, pipeline=Odd())
    assert result is True and events.items[-1][1]["state"] == "ready"


# ------------------------------------------------------------------ the server: the status

def watch(client, post, timeout: float = 20.0) -> list[dict]:
    """Ask for a load and look at `/api/status` as fast as it answers until the worker is ready: every `worker` seen while it was loading."""
    assert post().status_code == 202
    seen: list[dict] = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        worker = client.get("/api/status").json()["worker"]
        if worker["state"] == "loading":
            seen.append(worker)
        elif worker["state"] == "ready":
            return seen
        time.sleep(0.03)
    raise AssertionError("the model never became ready")


@pytest.fixture
def slow_download(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("STUDIO_FAKE_DOWNLOAD_MB", "8")
    monkeypatch.setenv("STUDIO_FAKE_LOAD_DELAY_MS", "900")
    monkeypatch.setenv("STUDIO_LOAD_PROGRESS_MS", "60")


def test_the_status_says_how_much_has_arrived_while_the_model_loads(client_factory, slow_download):
    client = client_factory()
    seen = watch(client, lambda: client.post("/api/model/load"))
    progress = [w["progress"] for w in seen]
    assert all(p is not None and set(p) == {"phase", "done_bytes", "total_bytes", "elapsed_s", "after"} for p in progress)
    downloading = [p for p in progress if p["phase"] == "downloading"]
    assert len(downloading) >= 3
    done = [p["done_bytes"] for p in progress]
    assert done == sorted(done) and 0 < max(done) <= 8 * MB and any(0 < d < 8 * MB for d in done)
    assert {p["total_bytes"] for p in downloading} == {8 * MB}
    assert [p["elapsed_s"] for p in progress] == sorted(p["elapsed_s"] for p in progress)
    assert progress[0]["after"] is None                                                       # nothing was unloaded to make room


def test_the_status_has_no_progress_when_nothing_is_loading(client_factory, slow_download):
    client = client_factory()
    assert client.get("/api/status").json()["worker"]["progress"] is None                      # unloaded
    watch(client, lambda: client.post("/api/model/load"))
    assert wait_for_worker_state(client, "ready")["worker"]["progress"] is None                # ready
    client.post("/api/model/unload")
    assert wait_for_worker_state(client, "unloaded")["worker"]["progress"] is None


def test_the_load_is_reported_as_the_time_it_takes_when_nothing_is_downloaded(client_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("STUDIO_FAKE_DOWNLOAD_MB", raising=False)
    monkeypatch.setenv("STUDIO_FAKE_LOAD_DELAY_MS", "700")
    monkeypatch.setenv("STUDIO_LOAD_PROGRESS_MS", "60")
    client = client_factory()
    seen = watch(client, lambda: client.post("/api/model/load"))
    reported = [w["progress"] for w in seen if w["progress"]["phase"] != "starting"]
    assert reported and all(p["phase"] == "loading" and p["done_bytes"] == 0 and p["total_bytes"] is None for p in reported)
    assert max(p["elapsed_s"] for p in reported) > 0.2


def test_it_says_which_model_was_unloaded_to_make_room(client_factory, slow_download):
    client = client_factory()
    watch(client, lambda: client.post("/api/model/load", json={"model": "image"}))
    seen = watch(client, lambda: client.post("/api/model/load", json={"model": "music"}))
    assert {w["progress"]["after"] for w in seen} == {"image"}
    ready = wait_for_worker_state(client, "ready")["worker"]
    assert ready["model"] == "music" and ready["progress"] is None                             # and it is not remembered


def test_for_a_speech_engine_it_names_the_engine_and_variant_that_was_unloaded(client_factory, slow_download):
    client = client_factory()
    watch(client, lambda: client.post("/api/model/load", json={"model": "speech", "engine": "fake"}))
    before = wait_for_worker_state(client, "ready")["worker"]
    first = f"speech:{before['engine']}:{before['variant']}"
    seen = watch(client, lambda: client.post("/api/model/load", json={"model": "speech", "engine": "fake-basic"}))
    assert {w["progress"]["after"] for w in seen} == {first}
    middle = wait_for_worker_state(client, "ready")["worker"]
    seen = watch(client, lambda: client.post("/api/model/load", json={"model": "image"}))
    assert {w["progress"]["after"] for w in seen} == {f"speech:{middle['engine']}:{middle['variant']}"}   # and the next swap names the one before it


def test_unloading_during_a_load_forgets_the_progress(client_factory, slow_download):
    client = client_factory()
    assert client.post("/api/model/load").status_code == 202
    wait_for_worker_state(client, "loading")
    time.sleep(0.2)
    assert client.get("/api/status").json()["worker"]["progress"]["phase"] in ("starting", "downloading", "loading")
    assert client.post("/api/model/unload").status_code == 200
    worker = wait_for_worker_state(client, "unloaded")["worker"]
    assert worker["progress"] is None
    assert client.get("/api/status").json()["worker"]["progress"] is None


def test_a_report_that_comes_after_the_load_is_over_is_ignored(client_factory, slow_download):
    client = client_factory()
    watch(client, lambda: client.post("/api/model/load"))
    client.portal.call(client.app.state.jobs._on_worker_event, {"event": "load_progress", "phase": "downloading", "done": 5, "total": 10, "elapsed": 1.0})
    assert client.get("/api/status").json()["worker"]["progress"] is None


@pytest.mark.parametrize("event, expected", [
    ({"phase": "downloading", "done": 5, "total": 10, "elapsed": 2.5}, {"phase": "downloading", "done_bytes": 5, "total_bytes": 10, "elapsed_s": 2.5}),
    ({"phase": "loading", "done": 5, "total": None, "elapsed": 2}, {"phase": "loading", "done_bytes": 5, "total_bytes": None, "elapsed_s": 2.0}),
    ({"phase": "weird", "done": -3, "total": 0, "elapsed": -1}, {"phase": "loading", "done_bytes": 0, "total_bytes": None, "elapsed_s": 0.0}),   # nothing a page would choke on
    ({}, {"phase": "loading", "done_bytes": 0, "total_bytes": None, "elapsed_s": 0.0}),
])
def test_what_the_worker_sends_is_made_safe_before_the_page_sees_it(client_factory, monkeypatch, event, expected):
    monkeypatch.setenv("STUDIO_FAKE_LOAD_DELAY_MS", "4000")
    monkeypatch.setenv("STUDIO_LOAD_PROGRESS_MS", "60000")  # the worker's own first report comes at once and its next a minute later: nothing of its own lands after the injection
    client = client_factory()
    assert client.post("/api/model/load").status_code == 202
    wait_for_worker_state(client, "loading")
    time.sleep(0.4)  # the worker's first report has come
    client.portal.call(client.app.state.jobs._on_worker_event, {"event": "load_progress", **event})
    shown = client.get("/api/status").json()["worker"]["progress"]
    assert {k: shown[k] for k in expected} == expected
