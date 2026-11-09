"""The Speech tab's contract file (DESIGN.md §38.9): `speech_contract.json` is what the server sends, captured from a real run of the fake engines, and the page's
tests build their fixtures from it and compare its keys with the page's types (as `vector_contract.json` does for Vector, §31.12). This file is the server's half:
it makes the same captures again and fails if the keys or the kinds of value have moved, so the page cannot be tested against an answer the server no longer gives.

Only the shape is compared (the keys at every depth, and the kind of value where both sides have one), never the values: ids, times and sizes differ on every run.
To change the contract on purpose, run this file once with UPDATE_SPEECH_CONTRACT=1, look at what changed in the diff, and update the page's types to match.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from test_speech_readings import finished_reading, part
from test_speech_recording import tone, wav
from test_speech_server import wait_for_engines
from test_speech_voices import design, sample, saved_cloned_voice, saved_designed_voice, upload, wait_job

from studio.speech import routes, voice_routes

CONTRACT = Path(__file__).parent / "speech_contract.json"

# Pasted text with a heading and two paragraphs: enough to be a review with a part, a title and no warnings.
TEXT = ("A short test chapter\n\nThe quick brown fox jumps over the lazy dog, again and again, until the dog gives up.\n\n"
        "A second paragraph follows, long enough to stand as a piece of its own in the review.\n")


def key_paths(value: Any, prefix: str = "") -> set[str]:
    """Every key at every depth, written as `a.b[].c` (`[]` is "each item of the list"). Values, and lists with nothing in them, add nothing."""
    out: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            out.add(path)
            out |= key_paths(item, path)
    elif isinstance(value, list):
        for item in value:
            out |= key_paths(item, prefix + "[]")
    return out


def kinds(value: Any, prefix: str = "") -> dict[str, set[str]]:
    """The kinds of value (string, number, boolean, list, object) at each path, `null` left out because any field may be null."""
    out: dict[str, set[str]] = {}

    def add(path: str, item: Any) -> None:
        name = {str: "string", bool: "boolean", int: "number", float: "number", list: "list", dict: "object"}.get(type(item))
        if name:
            out.setdefault(path, set()).add(name)

    if prefix:
        add(prefix, value)
    if isinstance(value, dict):
        for key, item in value.items():
            for path, found in kinds(item, f"{prefix}.{key}" if prefix else key).items():
                out.setdefault(path, set()).update(found)
    elif isinstance(value, list):
        for item in value:
            for path, found in kinds(item, prefix + "[]").items():
                out.setdefault(path, set()).update(found)
    return out


# The bodies the page sends. Each is a model that refuses a key it does not know (`extra="forbid"`), so the names of its fields are the contract of the request: the
# page's tests check that what it builds has exactly these, no more and no fewer.
REQUEST_MODELS = {
    "prepare": routes.PrepareBody, "reading": routes.ReadingBody, "reading_part": routes.PartBody, "reading_paragraph": routes.ParagraphBody, "piece": routes.PieceBody,
    "position": routes.PositionBody, "sample": voice_routes.SampleBody, "design": voice_routes.DesignBody, "save_voice": voice_routes.SaveVoiceBody,
    "save_voice_candidate": voice_routes.CandidateRef, "rename_voice": voice_routes.RenameBody,
}


def build(client) -> dict[str, Any]:
    """Make each answer the page reads, in one scenario the same way every time, and return them by name."""
    caps = wait_for_engines(client)
    out: dict[str, Any] = {"capabilities_speech": caps["speech"], "limits_speech": caps["limits"]["speech"]}
    out["requests"] = {name: sorted(model.model_fields) for name, model in REQUEST_MODELS.items()}
    out["prepared"] = client.post("/api/speech/prepare", json={"source": "text", "text": TEXT}).json()

    reading = finished_reading(client, parts=[part("One", 1, 2), part("Two", 3, 1)], style="calm and slow", language="English")
    assert client.put(f"/api/readings/{reading['id']}/position", json={"part": 0, "piece": 1, "offset_ms": 500}).status_code == 204
    out["reading_run"] = client.get(f"/api/runs/{reading['id']}").json()
    out["part_detail"] = client.get(f"/api/readings/{reading['id']}/parts/0").json()

    out["sample_answer_queued"] = sample(client, expect=202, voice="anna")  # the first press: a job to wait for
    out["sample_job"] = wait_job(client, out["sample_answer_queued"]["job"]["id"])
    out["sample_answer_ready"] = sample(client, voice="anna")  # the second press: the clip is there
    out["design_job"] = wait_job(client, design(client)["id"])
    out["saved_voice_designed"] = saved_designed_voice(client)
    out["saved_voice_cloned"] = saved_cloned_voice(client)
    out["engine_voices"] = client.get("/api/speech/engines/fake/voices").json()
    out["passage"] = client.get("/api/speech/passage").json()
    out["staged_recording"] = upload(client, wav(tone(10)))
    return out


def test_the_contract_file_is_what_the_server_sends(client):
    live = build(client)
    if os.environ.get("UPDATE_SPEECH_CONTRACT") == "1":
        CONTRACT.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    stored = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert set(stored) == set(live), "the contract names other answers than the test makes"
    assert stored["requests"] == live["requests"], "a field of a request body has been added, removed or renamed (run with UPDATE_SPEECH_CONTRACT=1, then update the page)"
    for name in sorted(set(live) - {"requests"}):
        assert key_paths(stored[name]) == key_paths(live[name]), f"{name}: the keys have changed (run with UPDATE_SPEECH_CONTRACT=1, then update the page's types)"
        live_kinds, stored_kinds = kinds(live[name]), kinds(stored[name])
        for path in sorted(set(live_kinds) & set(stored_kinds)):
            assert live_kinds[path] == stored_kinds[path], f"{name}.{path}: the kind of value has changed"


def test_the_contract_covers_each_state_the_page_has_to_draw(client):
    """A fixture is only as good as what it holds: each list the page loops over has something in it, so the keys of an item are in the contract."""
    stored = json.loads(CONTRACT.read_text(encoding="utf-8"))
    run = stored["reading_run"]
    assert run["mode"] == "speech" and run["hidden"] is False and run["reading"]["parts"] and run["reading"]["position"] is not None
    assert run["reading"]["style"] and run["reading"]["language"]  # the optional keys have a value in this one, so a key that is missing from the page's type shows
    assert stored["part_detail"]["pieces"] and stored["part_detail"]["url"] and stored["part_detail"]["pieces"][0]["url"]
    assert stored["engine_voices"]["presets"] and stored["engine_voices"]["saved"]
    assert {v["kind"] for v in (stored["saved_voice_designed"], stored["saved_voice_cloned"])} == {"designed", "cloned"}
    assert stored["design_job"]["files"] and stored["design_job"]["task"] == "design" and stored["sample_job"]["task"] == "sample"
    assert stored["prepared"]["parts"] and stored["prepared"]["parts"][0]["paragraphs"]
    assert stored["capabilities_speech"]["engines"] and stored["sample_answer_queued"]["job"] and stored["sample_answer_ready"]["url"]
