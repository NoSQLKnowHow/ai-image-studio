"""The Prompt builder's connection routes through the API (DESIGN.md §37.2, §37.4; criteria 222, 223, 249, 252, 254): status, Test connection and Unload from
memory, with a fake Ollama behind them, and that the address of Ollama is nowhere the page, the capabilities or the log can show it."""

from __future__ import annotations

import json
import logging

import pytest
from fake_ollama import FakeOllama

import studio.builder.service as service_module

MODEL = "qwen3.8:27b"


@pytest.fixture
def ollama():
    with FakeOllama(models=(MODEL,)) as server:
        yield server


@pytest.fixture
def builder_client(client_factory, ollama):
    return client_factory(ollama_url=ollama.url, ollama_model=MODEL)


def assert_no_address(ollama: FakeOllama, *texts: str) -> None:
    for text in texts:
        assert ollama.host not in text and str(ollama.port) not in text, text


# ------------------------------------------------------------------ not set up
def test_with_no_address_the_tab_says_it_is_not_set_up_and_how(client):
    body = client.get("/api/builder/status").json()
    assert body == {
        "configured": False, "reachable": False, "model": "", "vision": False, "hint": None,
        "message": "The Prompt builder needs an Ollama server. Set STUDIO_OLLAMA_URL and STUDIO_OLLAMA_MODEL in .env, then restart.",
    }
    for method, path in (("post", "/api/builder/test"), ("post", "/api/builder/unload")):
        response = getattr(client, method)(path)
        assert response.status_code == 409 and response.json()["code"] == "builder_not_configured"


def test_an_address_without_a_model_is_not_set_up_either(client_factory, ollama):
    client = client_factory(ollama_url=ollama.url)
    assert client.get("/api/builder/status").json()["configured"] is False
    assert ollama.requests == []  # and nothing was asked of the server


# ------------------------------------------------------------------ set up
def test_status_when_all_is_well(builder_client, ollama):
    body = builder_client.get("/api/builder/status").json()
    assert body == {"configured": True, "reachable": True, "model": MODEL, "vision": True, "message": None, "hint": None}  # vision from what Ollama says of the model


def test_a_model_that_cannot_look_at_pictures_says_so(client_factory):
    with FakeOllama(models=(MODEL,), capabilities=("completion", "thinking")) as plain:
        body = client_factory(ollama_url=plain.url, ollama_model=MODEL).get("/api/builder/status").json()
    assert body["vision"] is False and body["reachable"] is True and body["message"] is None


def test_a_vision_model_of_its_own_counts_when_ollama_has_it(client_factory):
    """`STUDIO_OLLAMA_VISION_MODEL` (criterion 254): the main model need not report vision when a model for pictures is set and present."""
    with FakeOllama(models=(MODEL, "llava:7b"), capabilities=("completion",)) as server:
        client = client_factory(ollama_url=server.url, ollama_model=MODEL, ollama_vision_model="llava:7b")
        assert client.get("/api/builder/status").json()["vision"] is True
        test = client.post("/api/builder/test").json()
        assert test["vision_model"] == "llava:7b" and test["vision_model_present"] is True
    with FakeOllama(models=(MODEL,), capabilities=("completion",)) as server:  # set, but Ollama does not have it
        client = client_factory(ollama_url=server.url, ollama_model=MODEL, ollama_vision_model="llava:7b")
        assert client.get("/api/builder/status").json()["vision"] is False
        assert client.post("/api/builder/test").json()["vision_model_present"] is False


def test_an_ollama_that_cannot_be_reached_says_so_without_naming_it(client_factory):
    with FakeOllama() as gone:
        url = gone.url
        host, port = gone.host, gone.port
    client = client_factory(ollama_url=url, ollama_model=MODEL)
    body = client.get("/api/builder/status").json()
    assert body["configured"] is True and body["reachable"] is False and body["vision"] is False
    assert body["message"] == "Can't reach the Ollama server." and "Test connection" in body["hint"]
    assert host not in json.dumps(body) and str(port) not in json.dumps(body)


def test_a_model_ollama_does_not_have_is_named_with_what_to_do(client_factory):
    with FakeOllama(models=("other:7b",)) as server:
        client = client_factory(ollama_url=server.url, ollama_model=MODEL)
        body = client.get("/api/builder/status").json()
        test = client.post("/api/builder/test").json()
    assert body["reachable"] is True and body["message"] == f"Ollama doesn't have the model {MODEL}." and body["hint"] == f"Pull it on the Ollama machine: ollama pull {MODEL}"
    assert test["model_present"] is False and test["models"] == ["other:7b"]


# ------------------------------------------------------------------ Test connection
def test_test_connection_shows_the_version_the_models_and_whether_yours_is_there(builder_client, ollama):
    ollama.models = [MODEL, "llama3:8b"]
    body = builder_client.post("/api/builder/test").json()
    assert body == {
        "reachable": True, "version": "0.99.0", "models": [MODEL, "llama3:8b"], "model": MODEL, "model_present": True, "vision": True, "vision_model": "",
        "vision_model_present": False, "context_length": 262144, "message": None, "hint": None,
    }


def test_the_address_appears_nowhere_the_page_can_see(builder_client, ollama, caplog):
    """Criterion 223 and §37.3 rule 12: not in the status, the test, the capabilities, an error, or any log line."""
    with caplog.at_level(logging.DEBUG):
        texts = [
            builder_client.get("/api/builder/status").text, builder_client.post("/api/builder/test").text, builder_client.get("/api/capabilities").text,
            builder_client.post("/api/builder/unload").text,
        ]
        ollama.stop()  # and now with the server gone, so that the failure texts are looked at too
        service_module.STATUS_TTL_S = 0
        texts += [builder_client.get("/api/builder/status").text, builder_client.post("/api/builder/test").text, builder_client.post("/api/builder/unload").text]
    assert_no_address(ollama, *texts, caplog.text)


def test_status_is_remembered_for_a_few_seconds_and_test_connection_always_asks(builder_client, ollama, monkeypatch):
    monkeypatch.setattr(service_module, "STATUS_TTL_S", 60.0)
    for _ in range(3):
        builder_client.get("/api/builder/status")
    assert len(ollama.calls("/api/version")) == 1  # the page can ask as often as it likes
    builder_client.post("/api/builder/test")
    assert len(ollama.calls("/api/version")) == 2  # a person pressing Test connection wants to know now


# ------------------------------------------------------------------ Unload from memory
def test_unload_asks_ollama_to_unload_its_model_and_nothing_else(builder_client, ollama):
    assert builder_client.post("/api/builder/unload").json() == {"ok": True, "unloaded": [MODEL]}
    assert ollama.unloaded == [MODEL]
    assert [c["path"] for c in ollama.requests] == ["/api/chat"]  # no other call: the studio never starts or stops Ollama (decision #19)


def test_unload_also_unloads_the_vision_model_if_it_is_another(client_factory):
    with FakeOllama(models=(MODEL, "llava:7b")) as server:
        client = client_factory(ollama_url=server.url, ollama_model=MODEL, ollama_vision_model="llava:7b")
        assert client.post("/api/builder/unload").json() == {"ok": True, "unloaded": [MODEL, "llava:7b"]}
        assert server.unloaded == [MODEL, "llava:7b"]


def test_unload_when_ollama_is_gone_is_a_failure_the_person_sees(client_factory):
    with FakeOllama() as gone:
        url = gone.url
    response = client_factory(ollama_url=url, ollama_model=MODEL).post("/api/builder/unload")
    assert response.status_code == 502 and response.json()["code"] == "ollama_unreachable" and response.json()["detail"] == "Can't reach the Ollama server."


def test_nothing_is_unloaded_by_the_studio_on_its_own(builder_client, ollama):
    """§37.6 choice 17: a button and a hint only. Looking at the status, testing the connection and starting up never unload."""
    builder_client.get("/api/builder/status")
    builder_client.post("/api/builder/test")
    builder_client.get("/api/capabilities")
    assert ollama.unloaded == []


# ------------------------------------------------------------------ the capabilities
def test_the_capabilities_say_whether_the_builder_is_set_up_and_what_is_known(client_factory, ollama):
    off = client_factory().get("/api/capabilities").json()
    assert off["builder"] == {"available": False, "configured": False, "reachable": None, "model": "", "vision": None}
    client = client_factory(ollama_url=ollama.url, ollama_model=MODEL)
    before = client.get("/api/capabilities").json()["builder"]
    assert before == {"available": True, "configured": True, "reachable": None, "model": MODEL, "vision": None}  # not asked yet: not guessed
    assert ollama.requests == []  # and the capabilities never wait on the network
    client.get("/api/builder/status")
    assert client.get("/api/capabilities").json()["builder"] == {"available": True, "configured": True, "reachable": True, "model": MODEL, "vision": True}


def test_the_limits_the_page_needs_are_in_the_capabilities(client):
    limits = client.get("/api/capabilities").json()["limits"]["builder"]
    assert limits["questions_max"] == 3 and limits["chips"] == {"min": 3, "max": 5} and limits["chip_chars"] == 60 and limits["reply_chars"] == 2000
    assert set(limits) == {"questions_max", "chips", "chip_chars", "reply_chars", "title_chars", "note_chars", "tags_max", "tag_chars", "preferences_max", "preference_chars"}


# ------------------------------------------------------------------ the scripted model of the tests
def test_the_scripted_model_is_always_there(client_factory):
    client = client_factory(builder_llm="fake")
    assert client.get("/api/builder/status").json() == {"configured": True, "reachable": True, "model": "fake-builder", "vision": False, "message": None, "hint": None}
    assert client.get("/api/capabilities").json()["builder"]["available"] is True
    assert client.post("/api/builder/unload").json() == {"ok": True, "unloaded": ["fake-builder"]}
    assert client.post("/api/builder/test").json()["model_present"] is True


# ------------------------------------------------------------------ Think
@pytest.mark.parametrize("setting,override,sent", [
    ("on", None, True), ("off", None, False), ("model", None, None),  # the setting (criterion 253)
    ("on", False, False), ("off", True, True), ("model", True, True), ("model", False, False),  # a message's own switch wins, for that message
])
def test_the_think_setting_and_a_messages_own_switch(client_factory, setting, override, sent):
    client = client_factory(builder_llm="fake", ollama_think=setting)
    assert client.app.state.builder.think(override) is sent
