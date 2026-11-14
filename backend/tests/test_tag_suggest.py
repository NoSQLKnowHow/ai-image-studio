"""Suggest: tags from the Prompt builder's model, through the API (DESIGN.md §45.6; criteria 426 to 430).

The real Ollama client talks to the test suite's fake Ollama (`fake_ollama.py`), so the tests can say exactly what the studio SENT (the model, the JSON format, `think`, the words, never a
picture) and what it does with each answer a model can give: tags, nothing new, tags the run has, text that is not JSON, an Ollama that is gone, out of memory, or without the model.
No real model is ever called, and nothing here says the tags are GOOD (that is for the Spark)."""

from __future__ import annotations

import json

import pytest
from conftest import create_run, wait_for
from fake_ollama import FakeOllama
from test_music_runs import create_music

MODEL = "tag-model"


@pytest.fixture
def ollama():
    with FakeOllama(models=(MODEL,)) as server:
        yield server


def says(ollama: FakeOllama, *tags: str) -> None:
    """The fake model answers with these tags, as Ollama streams a reply."""
    ollama.script = [("content", json.dumps({"tags": list(tags)})), ("done",)]


@pytest.fixture
def client(client_factory, ollama):
    return client_factory(ollama_url=ollama.url, ollama_model=MODEL)


def made(client, prompt="a lighthouse at dusk", **options):
    return wait_for(client, create_run(client, prompt, **options)["id"])


def suggest(client, run_id):
    return client.post(f"/api/runs/{run_id}/tags/suggest")


def test_the_suggestions_come_back_cleaned_and_nothing_is_applied(client, ollama):
    run = made(client)
    says(ollama, "Lighthouse", "  DUSK ", "dusk", "Red Sky,", "")
    answer = suggest(client, run["id"])
    assert answer.status_code == 200
    assert answer.json() == {"suggestions": ["lighthouse", "dusk", "red sky"]}  # cleaned as a typed tag is, each once, the empty one dropped
    # suggested, never applied: the run has no tags, no note, and nothing was written
    after = client.get(f"/api/runs/{run['id']}").json()
    assert after["tags"] == [] and after["notes"] == ""
    assert client.get("/api/tags").json() == {"tags": []}


def test_it_sends_words_only_to_the_model_with_the_json_format_and_think_off(client, ollama):
    run = made(client, "a lighthouse at dusk")
    client.put(f"/api/runs/{run['id']}/notes", json={"notes": "chosen for the poster"})
    client.put(f"/api/runs/{run['id']}/tags", json={"tags": ["sea"]})
    other = made(client, "another picture")
    client.put(f"/api/runs/{other['id']}/tags", json={"tags": ["harbour", "sea"]})
    says(ollama, "lighthouse")
    assert suggest(client, run["id"]).status_code == 200
    sent = ollama.calls("/api/chat")[-1]["body"]
    assert sent["model"] == MODEL
    assert sent["think"] is False  # a quick answer, whatever STUDIO_OLLAMA_THINK says
    assert sent["format"]["properties"]["tags"]["maxItems"] == 5 and sent["format"]["required"] == ["tags"]  # the shape asked for, and the setting's number
    assert all("images" not in message for message in sent["messages"])  # words only: never a picture, though the model could look at one
    user = sent["messages"][-1]["content"]
    assert "Words: a lighthouse at dusk" in user and "The person's note: chosen for the poster" in user
    assert "Tags it already has: sea" in user
    assert "Tags the person uses elsewhere: sea, harbour" in user  # the person's own spelling, most used first
    assert "Suggest up to 5 tags." in user


def test_a_music_run_sends_its_lyrics_and_a_picture_run_never_a_picture(client, ollama):
    track = wait_for(client, create_music(client, "slow piano", lyrics="the moon is high")["id"])
    says(ollama, "piano")
    assert suggest(client, track["id"]).status_code == 200
    user = ollama.calls("/api/chat")[-1]["body"]["messages"][-1]["content"]
    assert "Item: music track." in user and "Lyrics: the moon is high" in user


def test_a_tag_the_run_has_is_never_offered_and_the_number_asked_is_the_most_given(client, ollama):
    run = made(client)
    client.put(f"/api/runs/{run['id']}/tags", json={"tags": ["dusk"]})
    says(ollama, "dusk", "a", "b", "c", "d", "e", "f", "g")
    assert suggest(client, run["id"]).json() == {"suggestions": ["a", "b", "c", "d", "e"]}  # not "dusk"; five, the setting's number


def test_the_number_asked_follows_the_setting(client_factory, ollama):
    client = client_factory(ollama_url=ollama.url, ollama_model=MODEL, tag_suggestions=2)
    run = made(client)
    says(ollama, "a", "b", "c")
    assert suggest(client, run["id"]).json() == {"suggestions": ["a", "b"]}
    assert ollama.calls("/api/chat")[-1]["body"]["format"]["properties"]["tags"]["maxItems"] == 2
    assert client.get("/api/capabilities").json()["limits"]["annotations"]["suggest_max"] == 2


def test_nothing_new_is_an_empty_list_not_an_error(client, ollama):
    run = made(client)
    says(ollama)
    assert suggest(client, run["id"]).json() == {"suggestions": []}


@pytest.mark.parametrize("text", ["not json at all", "[1, 2]", '{"other": []}', '{"tags": "dusk"}', ""])
def test_an_answer_that_is_not_the_asked_shape_is_a_503_in_words(client, ollama, text):
    run = made(client)
    ollama.script = [("content", text), ("done",)]
    answer = suggest(client, run["id"])
    assert answer.status_code == 503 and answer.json()["code"] == "bad_answer" and answer.json()["detail"]


def test_an_ollama_that_cannot_be_reached_says_so_with_the_clients_own_words_and_hint(client_factory):
    gone = FakeOllama(models=(MODEL,))
    gone.start()
    url = gone.url
    gone.stop()
    client = client_factory(ollama_url=url, ollama_model=MODEL)
    run = made(client)
    answer = suggest(client, run["id"])
    body = answer.json()
    assert answer.status_code == 503 and body["code"] == "unreachable" and body["detail"] == "Can't reach the Ollama server." and "STUDIO_OLLAMA_URL" in body["hint"]
    assert url not in json.dumps(body)  # the address is private (§37.3 rule 12)


def test_a_missing_model_and_out_of_memory_are_said(client, ollama):
    run = made(client)
    ollama.chat_status = (404, "model 'tag-model' not found")
    assert suggest(client, run["id"]).json()["code"] == "model_missing"
    ollama.chat_status = (500, "model requires more system memory")
    assert suggest(client, run["id"]).json()["code"] == "out_of_memory"


def test_it_is_refused_in_words_when_it_is_off_or_not_set_up(client_factory, ollama):
    off = client_factory(ollama_url=ollama.url, ollama_model=MODEL, tag_suggestions=0)
    run = made(off)
    refused = suggest(off, run["id"])
    assert refused.status_code == 409 and refused.json()["code"] == "suggest_off" and "STUDIO_TAG_SUGGESTIONS" in refused.json()["detail"]
    assert ollama.calls("/api/chat") == []  # the model was not asked
    assert off.get("/api/capabilities").json()["annotations"]["suggest"] == {"available": False, "reason": "Suggest is switched off (STUDIO_TAG_SUGGESTIONS is 0)."}
    none = client_factory()
    run = made(none)
    unset = suggest(none, run["id"])
    assert unset.status_code == 409 and unset.json()["code"] == "builder_not_set_up" and "STUDIO_OLLAMA_URL" in unset.json()["detail"]
    caps = none.get("/api/capabilities").json()["annotations"]["suggest"]
    assert caps["available"] is False and "Ollama" in caps["reason"]


def test_the_capabilities_say_it_is_available_when_it_is_set_up_without_asking_the_network(client, ollama):
    assert client.get("/api/capabilities").json()["annotations"]["suggest"] == {"available": True, "reason": None}
    assert ollama.requests == []  # the capabilities never wait on Ollama


def test_an_unknown_run_is_404(client):
    assert suggest(client, "f" * 32).status_code == 404
    assert suggest(client, "not an id").status_code == 404


def test_it_works_for_a_run_in_the_bin_since_it_changes_nothing(client, ollama):
    run = made(client)
    client.post(f"/api/runs/{run['id']}/bin")
    says(ollama, "kept")
    assert suggest(client, run["id"]).json() == {"suggestions": ["kept"]}  # only adding a suggested tag is refused there (409)


def test_one_suggestion_at_a_time_shares_the_builders_slot(client, ollama):
    """A second press waits for the first, as a second chat reply does (§37.3 rule 9): two requests never reach Ollama at once."""
    import threading
    import time

    run = made(client)
    ollama.script = [("sleep", 0.4), ("content", json.dumps({"tags": ["slow"]})), ("done",)]
    results: list[int] = []
    in_flight: list[int] = []

    def press():
        results.append(suggest(client, run["id"]).status_code)

    first = threading.Thread(target=press)
    first.start()
    time.sleep(0.1)
    in_flight.append(len(ollama.calls("/api/chat")))  # the first has reached Ollama
    second = threading.Thread(target=press)
    second.start()
    time.sleep(0.15)
    in_flight.append(len(ollama.calls("/api/chat")))  # the second is still waiting for the slot
    first.join(5)
    second.join(5)
    assert results == [200, 200] and in_flight == [1, 1]
    assert len(ollama.calls("/api/chat")) == 2


def test_it_asks_the_main_model_even_when_a_vision_model_is_set(client_factory):
    """Suggest sends words only, so the model that can look at pictures is never the one asked (DESIGN.md §45.6)."""
    with FakeOllama(models=(MODEL, "looks-at-pictures")) as server:
        client = client_factory(ollama_url=server.url, ollama_model=MODEL, ollama_vision_model="looks-at-pictures")
        run = made(client)
        says(server, "a")
        assert suggest(client, run["id"]).status_code == 200
        assert server.calls("/api/chat")[-1]["body"]["model"] == MODEL
