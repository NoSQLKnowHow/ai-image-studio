"""The memory pre-flight message and the Prompt builder's Unload (DESIGN.md §37.4, criterion 249): when the builder is set up, the advice for too little free memory
says to press Unload from memory on its tab; when it is not, the advice is the same as it always was."""

from __future__ import annotations

import studio.sysinfo
from conftest import create_run, wait_for
from studio import sysinfo
from test_m2 import meminfo

UNLOAD = "Unload from memory"


def test_the_advice_mentions_unload_only_when_the_builder_is_set_up():
    plain = sysinfo.memory_hint(False)
    assert plain == sysinfo.MEMORY_HINT and UNLOAD not in plain
    with_builder = sysinfo.memory_hint(True)
    assert with_builder.startswith(sysinfo.MEMORY_HINT)  # the old advice is all still there
    assert UNLOAD in with_builder and "Ollama" in with_builder


def test_a_run_refused_for_memory_says_so_when_the_builder_is_set_up(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=12.5))
    client = client_factory(min_free_gb=40, builder_llm="fake")
    run = wait_for(client, create_run(client)["id"])
    assert run["status"] == "failed"
    assert "drop_caches" in run["error"]["hint"] and UNLOAD in run["error"]["hint"]
    # the message itself (which figures were short) is the same as without the builder
    assert run["error"]["message"] == "Not enough free memory to load the model: 12.5 GB available, 40 GB required (STUDIO_MIN_FREE_GB)."


def test_a_run_refused_for_memory_does_not_mention_it_when_the_builder_is_not_set_up(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=12.5))
    client = client_factory(min_free_gb=40)
    run = wait_for(client, create_run(client)["id"])
    assert run["status"] == "failed"
    assert "drop_caches" in run["error"]["hint"] and UNLOAD not in run["error"]["hint"]


def test_a_model_load_refused_for_memory_says_so_too(client_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(studio.sysinfo, "MEMINFO", meminfo(tmp_path, available_gb=12.5))
    client = client_factory(min_free_gb=40, builder_llm="fake")
    response = client.post("/api/model/load", json={"model": "image"})
    assert response.status_code == 409
    assert UNLOAD in response.json()["hint"]
