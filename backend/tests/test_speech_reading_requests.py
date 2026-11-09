"""The request for a reading, checked (DESIGN.md §38.6, §38.9; criteria 264, 267, 274): what is accepted, what is refused and with which words, and that a refusal
names the field at fault so the page can put the message beside the right box. Nothing is queued for a refused request.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import wait_for
from test_speech_readings import make_reading, part, reading_body
from test_speech_server import wait_for_engines


def post(client, parts=None, **fields) -> Any:
    wait_for_engines(client)
    return client.post("/api/readings", json=reading_body(parts, **fields))


def refused(client, status: int, code: str, field: str | None, parts=None, **fields) -> dict[str, Any]:
    """Post a request that must be refused, check the answer, and check nothing was queued."""
    response = post(client, parts, **fields)
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code and body.get("field") == field and body["detail"], body
    assert client.get("/api/runs", params={"kind": "speech"}).json()["runs"] == []
    return body


# ---------------------------------------------------------------- the engine and the voice
def test_an_engine_the_server_does_not_have_is_refused_at_the_engine_field(client):
    refused(client, 422, "unknown_engine", "engine", engine="no-such-engine")


def test_an_engine_that_cannot_run_is_refused_with_its_reason_and_hint(client_factory, monkeypatch):
    monkeypatch.setenv("STUDIO_FAKE_SPEECH_UNAVAILABLE", "fake-basic")  # read by the worker that checks the engine at start-up
    client = client_factory()
    body = refused(client, 409, "engine_unavailable", "engine", engine="fake-basic", voice="basic_one")
    assert "library is missing" in body["detail"] and body["hint"]
    assert post(client).status_code == 201  # and the other engine still works


def test_an_engine_still_being_checked_is_told_to_wait_not_called_unavailable(client):
    jobs = client.app.state.jobs
    from studio.speech.service import PartSpec, ParagraphSpec, ReadingRefused, ReadingSpec

    spec = ReadingSpec("t", {}, "fake", "anna", parts=[PartSpec("p", [ParagraphSpec("paragraph", "Some words that are long enough to be spoken aloud here.")])])
    with pytest.raises(ReadingRefused) as caught:
        jobs.speech.build(spec, {"fake": {"state": "checking", "title": "Fake speech"}})
    assert caught.value.code == "engine_checking" and caught.value.status == 409 and "still being checked" in caught.value.message


@pytest.mark.parametrize("voice", ["", "nobody", "ANNA", "anna "])
def test_a_voice_the_engine_does_not_have_is_refused_at_the_voice_field(client, voice):
    body = refused(client, 422, "unknown_voice", "voice", voice=voice)
    assert "Fake speech" in body["detail"]


def test_a_voice_belongs_to_the_engine_it_is_asked_with(client):
    refused(client, 422, "unknown_voice", "voice", engine="fake-basic", voice="anna")  # Anna is the other engine's


def test_a_saved_voice_that_does_not_exist_is_refused(client):
    refused(client, 422, "unknown_voice", "voice", voice="", saved_voice="0" * 32)


def test_a_saved_voice_cannot_be_used_with_an_engine_that_cannot_clone(client):
    db = client.app.state.jobs.db
    with db.tx() as c:
        c.execute("INSERT INTO voices (id, name, name_key, kind, engine, ref_text, ref_path, seconds, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  ("a" * 32, "Mine", "mine", "cloned", "fake-basic", "words said", "speech/voices/a/reference.wav", 20.0, "2026-11-08T10:00:00.000Z"))
    body = refused(client, 422, "voice_engine", "voice", engine="fake-basic", voice="", saved_voice="a" * 32)
    assert "another engine" in body["detail"]


# ---------------------------------------------------------------- the style note and the language
@pytest.mark.parametrize("engine,voice", [("fake-basic", "basic_one"), ("fake", "bea")])
def test_a_style_note_is_refused_where_the_voice_would_ignore_it(client, engine, voice):
    """An engine without styles, or a voice of an engine with them that does not take one: refused, not silently dropped (criterion 262)."""
    refused(client, 422, "no_style", "style", engine=engine, voice=voice, style="warm and slow")


def test_a_style_note_is_kept_on_the_voice_that_takes_it_and_tidied(client):
    run = make_reading(client, style="  warm   and\nslow  ")
    assert run["reading"]["style"] == "warm and slow"


def test_a_note_of_nothing_but_spaces_is_no_note_at_all(client):
    for engine, voice in (("fake-basic", "basic_one"), ("fake", "bea"), ("fake", "anna")):
        assert make_reading(client, engine=engine, voice=voice, style="   \n ")["reading"]["style"] is None


def test_a_style_note_has_a_length_limit(client):
    refused(client, 422, "style_too_long", "style", style="x" * 301)
    assert post(client, style="x" * 300).status_code == 201


def test_a_language_is_matched_without_regard_to_case_and_stored_as_the_engine_spells_it(client):
    assert make_reading(client, voice="dan", language=" GERMAN ")["reading"]["language"] == "German"


def test_a_language_the_engine_does_not_speak_is_refused_and_the_list_is_in_the_message(client):
    body = refused(client, 422, "unknown_language", "language", language="Klingon")
    assert "English, German" in body["detail"]


def test_an_engine_without_languages_takes_no_language(client):
    refused(client, 422, "no_language", "language", engine="fake-basic", voice="basic_one", language="English")


# ---------------------------------------------------------------- the parts
def test_a_reading_needs_at_least_one_part(client):
    refused(client, 422, "parts", "parts", parts=[])


def test_a_reading_with_too_many_parts_is_refused(client):
    refused(client, 422, "parts", "parts", parts=[part(str(n), n, 1) for n in range(1001)])


def test_a_reading_with_no_words_in_it_is_refused_with_a_hint(client):
    body = refused(client, 422, "empty", "parts", parts=[{"title": "Blank", "paragraphs": [{"kind": "paragraph", "text": "   \n  "}]}, {"title": "None", "paragraphs": []}])
    assert body["hint"]


def test_a_part_with_nothing_to_say_is_left_out_and_the_rest_are_numbered_without_a_gap(client):
    blank = {"title": "Blank", "paragraphs": [{"kind": "paragraph", "text": "  "}]}
    run = make_reading(client, [part("One", 1, 1), blank, part("Three", 3, 1)])
    assert [(p["idx"], p["title"]) for p in run["reading"]["parts"]] == [(0, "One"), (1, "Three")]
    assert wait_for(client, run["id"])["reading"]["parts_done"] == 2  # and both are made: nothing is waiting for a part that is not there


def test_a_part_with_no_title_is_given_a_number_and_a_long_one_is_cut(client):
    run = make_reading(client, [{**part("", 1, 1)}, {**part("t" * 400, 2, 1)}], title="  A   spaced \n title " + "z" * 300)  # 400 is the most a title may be
    titles = [p["title"] for p in run["reading"]["parts"]]
    assert titles[0] == "Part 1" and len(titles[1]) == 200
    assert run["prompt"].startswith("A spaced title zzz") and len(run["prompt"]) == 200  # one line, and not longer than the history can show
    wait_for_engines(client)
    assert client.post("/api/readings", json=reading_body([part("t" * 401, 1, 1)])).status_code == 422  # and more than 400 is refused outright


def test_a_reading_with_no_title_is_called_reading(client):
    assert make_reading(client, title="   ")["prompt"] == "Reading"


@pytest.mark.parametrize("body", [
    {"title": "t", "engine": "fake", "voice": "anna"},                                                                    # no parts at all
    {"title": "t", "engine": "fake", "voice": "anna", "parts": "text"},                                                   # parts that are not a list
    {"title": "t", "engine": "fake", "voice": "anna", "parts": [{"title": "p", "paragraphs": [{"kind": "poem", "text": "hello there everyone, this is long enough"}]}]},
    {"title": "t", "engine": "fake", "voice": "anna", "parts": [{"title": "p", "paragraphs": [{"kind": "paragraph", "text": 12}]}]},
    {"title": "t", "engine": "fake", "voice": "anna", "parts": [{"title": "p", "paragraphs": []}], "volume": 11},         # a field nobody asked for
    {"title": 5, "engine": "fake", "voice": "anna", "parts": []},
    {"engine": "fake", "voice": "anna", "parts": []},
])
def test_a_request_of_the_wrong_shape_is_refused_by_the_framework_and_queues_nothing(client, body):
    wait_for_engines(client)
    assert client.post("/api/readings", json=body).status_code == 422
    assert client.get("/api/runs", params={"kind": "speech"}).json()["runs"] == []


def test_a_request_that_is_not_json_is_refused(client):
    wait_for_engines(client)
    assert client.post("/api/readings", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 422


# ---------------------------------------------------------------- limits
def test_a_paragraph_longer_than_a_whole_reading_may_be_is_refused(client_factory):
    client = client_factory(speech_max_chars=500)
    body = refused(client, 413, "too_large", "parts", parts=[{"title": "Big", "paragraphs": [{"kind": "paragraph", "text": "w" * 501}]}])
    assert "longer than a whole reading" in body["detail"]


def test_a_reading_with_more_text_than_the_limit_is_refused_and_says_the_limit(client_factory):
    client = client_factory(speech_max_chars=500)
    body = refused(client, 413, "too_large", "parts", parts=[part("One", 1, 4), part("Two", 5, 4)])  # about 80 characters a paragraph
    assert "500 characters" in body["detail"]
    assert post(client, [part("One", 1, 4)]).status_code == 201  # and under the limit it is fine


def test_a_reading_that_would_not_fit_on_the_disk_is_refused_with_the_figures(client, monkeypatch):
    import collections
    import studio.speech.service as service

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(service.shutil, "disk_usage", lambda path: usage(10 * 1024 ** 3, 10 * 1024 ** 3 - 100, 100))
    body = refused(client, 507, "storage_full", None)
    assert "GB of disk" in body["detail"] and body["hint"]


def test_the_queue_has_a_limit_and_a_full_queue_says_so_and_queues_nothing_more(client_factory):
    client = client_factory(queue_cap=1)
    from test_speech_readings import SLOW, wait_until

    busy = make_reading(client, [part("Busy", 1, 1, {0: SLOW})], title="busy")
    wait_until(client, busy["id"], lambda r: r["status"] == "running", "running")
    make_reading(client, title="waits")  # the one place in the queue
    response = post(client, title="one too many")
    assert response.status_code == 429 and response.json()["code"] == "queue_full"
    assert len(client.get("/api/runs", params={"kind": "speech"}).json()["runs"]) == 2


# ---------------------------------------------------------------- what is stored for the request
def test_what_the_request_said_is_kept_with_the_reading_to_the_letter(client):
    run = make_reading(client, source={"kind": "url", "url": "https://example.org/essay", "title": "An essay"}, engine="fake-basic", voice="basic_two")
    reading = run["reading"]
    assert reading["source"] == {"kind": "url", "url": "https://example.org/essay", "title": "An essay"}
    assert reading["engine_title"] == "Fake basic speech" and reading["voice"] == {"kind": "preset", "id": "basic_two", "label": "Basic Two",
                                                                                 "gender": "male", "accent": None}
    assert reading["licence"]["noncommercial"] is True and reading["sample_rate"] == 22050  # the engine's own rate, not an assumed 24 kHz
    assert wait_for(client, run["id"])["reading"]["sample_rate"] == 22050


def test_the_pieces_each_have_a_seed_of_their_own_and_a_heading_is_its_own_piece(client):
    paragraphs = [{"kind": "heading", "text": "Chapter One"}] + part("x", 1, 3)["paragraphs"]
    run = make_reading(client, [{"title": "One", "paragraphs": paragraphs}])
    db = client.app.state.jobs.db
    rows = db.read_all("SELECT idx, kind, seed FROM speech_pieces WHERE run_id=? ORDER BY idx", (run["id"],))
    assert [r["kind"] for r in rows] == ["heading", "paragraph", "paragraph", "paragraph"]
    assert len({r["seed"] for r in rows}) == 4 and all(0 <= r["seed"] < 2 ** 31 for r in rows)
    options = json.loads(db.get_run(run["id"])["options_json"])
    assert options["pause_ms"] == 450 and options["variant"] == "default" and options["voice"]["id"] == "anna"


def test_a_heading_is_followed_by_a_longer_pause_than_a_paragraph(client):
    """DESIGN.md §38.6: the worker is told twice the pause for a heading, so the listener hears the chapter begin."""
    paragraphs = [{"kind": "heading", "text": "Chapter One of the long book"}, {"kind": "paragraph", "text": "Paragraph 1: the quick brown fox jumps over the lazy dog, again and again."}]
    run = wait_for(client, make_reading(client, [{"title": "One", "paragraphs": paragraphs}])["id"])
    pieces = client.get(f"/api/readings/{run['id']}/parts/0").json()["pieces"]
    spoken = [min(8.0, 0.3 + 0.012 * len(p["text"])) for p in pieces]
    pauses = [p["frames"] / 24000 - s for p, s in zip(pieces, spoken)]
    assert pauses == [pytest.approx(0.9, abs=0.01), pytest.approx(0.45, abs=0.01)]
