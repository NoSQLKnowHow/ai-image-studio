"""Where a model's files come from (DESIGN.md §26.11): the cache first, the network only for what is missing."""

from __future__ import annotations

import pytest

from studio.pipelines.base import Canceled
from studio.pipelines.hub import is_cache_miss, load_with_hub_mode


def named(name: str, base=Exception, text: str = "boom"):
    return type(name, (base,), {})(text)


class Loader:
    """Records the `local_files_only` it was called with, and fails the way each test says."""

    def __init__(self, *failures):
        self.failures = list(failures)  # one per call, None = succeed
        self.calls: list[bool] = []

    def __call__(self, offline: bool):
        self.calls.append(offline)
        failure = self.failures.pop(0) if self.failures else None
        if failure is not None:
            raise failure
        return f"model loaded {'offline' if offline else 'online'}"


# ------------------------------------------------------------------ what a cache miss looks like
@pytest.mark.parametrize("exc", [
    named("LocalEntryNotFoundError"),
    named("EntryNotFoundError", text="no such file"),
    named("OfflineModeIsEnabled"),
    FileNotFoundError("model.safetensors"),
    OSError("We couldn't connect to 'https://huggingface.co' to load the files, and couldn't find them in the cached files."),
    ValueError("Unable to load transformer using load method: Cannot find the requested files in the disk cache and outgoing traffic has been disabled (local_files_only=True)"),
    OSError("language_model does not appear to have a file named model.safetensors"),
])
def test_files_missing_from_the_cache_are_recognised(exc):
    assert is_cache_miss(exc)


def test_a_miss_wrapped_inside_another_error_is_found():
    try:
        try:
            raise named("LocalEntryNotFoundError")
        except Exception as inner:
            raise ValueError("Unable to load vocoder using load method") from inner
    except ValueError as outer:
        assert is_cache_miss(outer)
    try:
        try:
            raise FileNotFoundError("x")
        except Exception:
            raise RuntimeError("implicit context, no `from`")
    except RuntimeError as outer:
        assert is_cache_miss(outer)


@pytest.mark.parametrize("exc", [
    RuntimeError("CUDA out of memory. Tried to allocate 20 GiB"),
    MemoryError(),
    ValueError("the weights are corrupt"),
    ImportError("accelerate is required"),
    PermissionError(13, "Permission denied", "/models/hub"),
    ConnectionError("Name or service not known"),
    named("GatedRepoError"),
])
def test_other_failures_are_not_mistaken_for_a_cache_miss(exc):
    assert not is_cache_miss(exc)


# ------------------------------------------------------------------ the three modes
def test_auto_loads_from_the_cache_alone_when_it_can():
    load = Loader()
    assert load_with_hub_mode(load, "auto") == "model loaded offline"
    assert load.calls == [True]  # the network was never asked


def test_auto_goes_online_only_when_files_are_missing():
    load = Loader(named("LocalEntryNotFoundError"))
    assert load_with_hub_mode(load, "auto", "the music model") == "model loaded online"
    assert load.calls == [True, False]  # offline first, then online


def test_auto_does_not_go_online_for_any_other_failure():
    boom = RuntimeError("CUDA out of memory")
    load = Loader(boom, None)
    with pytest.raises(RuntimeError, match="out of memory"):
        load_with_hub_mode(load, "auto")
    assert load.calls == [True]  # no second try, and certainly none over the network


def test_auto_raises_the_online_failure_if_the_second_try_fails_too():
    load = Loader(named("LocalEntryNotFoundError"), ConnectionError("no network"))
    with pytest.raises(ConnectionError, match="no network"):
        load_with_hub_mode(load, "auto")
    assert load.calls == [True, False]


def test_offline_never_goes_online_even_when_files_are_missing():
    miss = named("LocalEntryNotFoundError")
    load = Loader(miss)
    with pytest.raises(type(miss)):
        load_with_hub_mode(load, "offline")
    assert load.calls == [True]


def test_online_is_the_old_behaviour():
    load = Loader()
    assert load_with_hub_mode(load, "online") == "model loaded online"
    assert load.calls == [False]


def test_a_cancel_is_never_swallowed():
    load = Loader(Canceled())
    with pytest.raises(Canceled):
        load_with_hub_mode(load, "auto")
    assert load.calls == [True]
