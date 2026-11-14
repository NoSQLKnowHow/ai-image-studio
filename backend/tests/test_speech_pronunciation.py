"""The pronunciation list on the server (DESIGN.md §44.3, criteria 347 to 352): the routes that keep it, Try it, and what a reading does with it.

Three layers are tested, each with the tool that suits it:
  * the routes, with a real app (the `client` fixture): adding, changing, deleting, the refusals and their words, the limits the page reads;
  * the service, with a real database and no server (`Env`, as test_speech_service.py does): the job sent to the worker holds the SPOKEN words, a reading keeps the list it
    began with, a corrected piece is spoken with the same entries, and a voice sample never sees the list;
  * a whole reading made by the real worker with the fake engine, whose audio length depends on the words it was given, so that "the engine was given the respelling" is
    proved by the audio and not by a spy.
The rule of the matching itself (whole words, case, phrases, one pass) is in test_speech_pronounce.py.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import wait_for
from test_speech_readings import make_reading, part, wait_until
from test_speech_service import Env, text

from studio.speech import pronunciation_routes
from studio.speech.pronounce import ENTRIES_MAX, SAY_MAX, WORD_MAX
from studio.speech.service import ParagraphSpec, PartSpec


def add(client, word: str, say: str = "win", match_case: bool = False, expect: int = 201) -> dict[str, Any]:
    response = client.post("/api/speech/pronunciations", json={"word": word, "say": say, "match_case": match_case})
    assert response.status_code == expect, response.text
    return response.json()


def entries(client) -> list[dict[str, Any]]:
    return client.get("/api/speech/pronunciations").json()["entries"]


# ---------------------------------------------------------------- the routes (criterion 347)
def test_the_list_starts_empty_and_an_entry_can_be_added_changed_and_deleted(client):
    assert entries(client) == []
    made = add(client, " Nguyen ", " win ", match_case=True)
    assert made["word"] == "Nguyen" and made["say"] == "win" and made["match_case"] is True and len(made["id"]) == 32  # tidied, and given an id
    assert entries(client) == [made]

    changed = client.patch(f"/api/speech/pronunciations/{made['id']}", json={"say": "wynn"})
    assert changed.status_code == 200 and changed.json()["say"] == "wynn" and changed.json()["word"] == "Nguyen" and changed.json()["match_case"] is True  # what was left out stays
    assert changed.json()["updated_at"] >= made["updated_at"] and changed.json()["created_at"] == made["created_at"]
    assert client.patch(f"/api/speech/pronunciations/{made['id']}", json={"match_case": False}).json()["match_case"] is False

    assert client.delete(f"/api/speech/pronunciations/{made['id']}").status_code == 204
    assert entries(client) == []


def test_the_list_is_listed_by_word_ignoring_case(client):
    for word in ("zebra", "Apple", "mango"):
        add(client, word)
    assert [e["word"] for e in entries(client)] == ["Apple", "mango", "zebra"]


def test_the_list_is_kept_across_a_restart(client_factory, tmp_path):
    first = client_factory()
    add(first, "Nguyen", "win")
    first.__exit__(None, None, None)  # the app is stopped; a new one over the same data folder is the same studio after a restart
    second = client_factory()
    assert [(e["word"], e["say"]) for e in entries(second)] == [("Nguyen", "win")]


def test_a_word_that_is_already_on_the_list_is_refused_ignoring_case(client):
    first = add(client, "Nguyen")
    refused = client.post("/api/speech/pronunciations", json={"word": "NGUYEN", "say": "wynn"})
    assert refused.status_code == 409 and refused.json()["code"] == "word_taken" and refused.json()["field"] == "word"
    assert "NGUYEN" in refused.json()["detail"]  # the refusal names the word that was asked for, so the person can see which entry it means
    other = add(client, "Hermes", "her meez")
    # changing an entry to another entry's word is the same refusal; changing only the capitals of its own word is allowed
    assert client.patch(f"/api/speech/pronunciations/{other['id']}", json={"word": "nguyen"}).status_code == 409
    assert client.patch(f"/api/speech/pronunciations/{first['id']}", json={"word": "NGUYEN"}).status_code == 200
    assert [e["word"] for e in entries(client)] == ["Hermes", "NGUYEN"]


@pytest.mark.parametrize("body,field,code", [
    ({"word": "", "say": "x"}, "word", "empty"),
    ({"word": "w" * (WORD_MAX + 1), "say": "x"}, "word", "too_long"),
    ({"word": "$5", "say": "x"}, "word", "start"),
    ({"word": "Nguyen", "say": ""}, "say", "empty"),
    ({"word": "Nguyen", "say": "s" * (SAY_MAX + 1)}, "say", "too_long"),
    ({"word": "Nguyen", "say": "Nguyen"}, "say", "same"),
])
def test_a_bad_entry_is_refused_naming_the_field_and_the_reason(client, body, field, code):
    refused = client.post("/api/speech/pronunciations", json=body)
    assert refused.status_code == 422 and refused.json()["field"] == field and refused.json()["code"] == code and refused.json()["detail"]
    assert entries(client) == []  # nothing was kept


def test_a_change_is_checked_as_a_whole_entry(client):
    made = add(client, "Nguyen", "win")
    # the new word, with the respelling the entry already has: the respelling may not equal the word it is now for
    refused = client.patch(f"/api/speech/pronunciations/{made['id']}", json={"word": "win"})
    assert refused.status_code == 422 and refused.json()["code"] == "same"


def test_an_unknown_entry_is_not_found_whatever_is_done_to_it(client):
    ghost = "0" * 32
    assert client.patch(f"/api/speech/pronunciations/{ghost}", json={"say": "x"}).status_code == 404
    assert client.delete(f"/api/speech/pronunciations/{ghost}").status_code == 404
    assert client.patch("/api/speech/pronunciations/not-an-id", json={"say": "x"}).status_code == 404  # an id that is not even the right shape never reaches the database
    assert client.delete("/api/speech/pronunciations/not-an-id").status_code == 404


def test_a_request_with_a_field_the_route_does_not_know_is_refused(client):
    assert client.post("/api/speech/pronunciations", json={"word": "a", "say": "b", "engine": "kokoro"}).status_code == 422  # one list for every engine: there is no per-engine field


def test_a_full_list_refuses_another_entry_in_words(client, monkeypatch):
    monkeypatch.setattr(pronunciation_routes, "ENTRIES_MAX", 2)
    add(client, "one")
    add(client, "two")
    refused = client.post("/api/speech/pronunciations", json={"word": "three", "say": "x"})
    assert refused.status_code == 409 and refused.json()["code"] == "full" and "full" in refused.json()["detail"]
    assert [e["word"] for e in entries(client)] == ["one", "two"]


def test_the_limits_the_page_reads_are_the_ones_the_server_enforces(client):
    limits = client.get("/api/capabilities").json()["limits"]["speech"]["pronunciation"]
    assert limits == {"word_max": WORD_MAX, "words_max": 6, "say_max": SAY_MAX, "entries_max": ENTRIES_MAX, "try_chars": 5000}


# ---------------------------------------------------------------- Try it (criterion 352)
def test_try_it_shows_the_spoken_words_and_the_entries_that_changed_them(client):
    add(client, "Nguyen", "win")
    add(client, "US", "you ess", match_case=True)
    answer = client.post("/api/speech/pronunciations/try", json={"text": "Nguyen's winter, and nguyen: tell us about the US."}).json()
    assert answer["spoken"] == "win's winter, and win: tell us about the you ess." and answer["changed"] is True
    # longest entry first (the matcher's own order), each with how many words it changed
    assert answer["used"] == [{"word": "Nguyen", "say": "win", "count": 2}, {"word": "US", "say": "you ess", "count": 1}]


def test_try_it_with_nothing_to_change_says_so_and_changes_no_word(client):
    answer = client.post("/api/speech/pronunciations/try", json={"text": "Nothing here."}).json()
    assert answer == {"spoken": "Nothing here.", "changed": False, "used": []}


def test_try_it_uses_the_list_as_it_is_now_and_keeps_nothing(client):
    made = add(client, "Nguyen", "win")
    client.patch(f"/api/speech/pronunciations/{made['id']}", json={"say": "wynn"})
    assert client.post("/api/speech/pronunciations/try", json={"text": "Nguyen"}).json()["spoken"] == "wynn"
    client.delete(f"/api/speech/pronunciations/{made['id']}")
    assert client.post("/api/speech/pronunciations/try", json={"text": "Nguyen"}).json()["spoken"] == "Nguyen"
    assert client.post("/api/speech/pronunciations/try", json={"text": "x" * 5001}).status_code == 422  # a few paragraphs, not a book


# ---------------------------------------------------------------- what a reading does with the list (criteria 349 and 350)
def seed_list(env: Env, *pairs: tuple[str, str], match_case: bool = False) -> None:
    for n, (word, say) in enumerate(pairs, 1):
        row = {"id": f"{len(env.store.pronunciations()) + 1:032x}", "word": word, "word_key": word.casefold(), "say": say, "match_case": int(match_case),
               "created_at": "2026-11-14T00:00:00Z", "updated_at": "2026-11-14T00:00:00Z"}
        assert env.store.insert_pronunciation(row, 1000) == "ok"


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


def one_part(*paragraphs: str) -> list[PartSpec]:
    return [PartSpec("One", [ParagraphSpec("paragraph", p) for p in paragraphs])]


LONG = "Nguyen walked all the way home from the harbour, and Nguyen's dog, Hermes, ran ahead of him."  # over the fake engine's 40-character minimum: a piece of its own


def test_the_engine_is_given_the_respelled_words_and_the_piece_keeps_its_own(env):
    seed_list(env, ("Nguyen", "win"), ("Hermes", "her meez"))
    built, row = env.insert(env.spec(one_part(LONG)))
    spoken = "win walked all the way home from the harbour, and win's dog, her meez, ran ahead of him."
    assert env.service.job_for(row)["pieces"][0]["text"] == spoken  # what the worker is given
    assert [p["text"] for p in env.store.pieces(built.run["id"], 0)] == [LONG]  # what the review, the read-along and the ZIP carry: the words as they were
    assert env.service.part_detail(row, 0)["pieces"][0]["text"] == LONG


def test_with_an_empty_list_the_job_is_the_job_it_always_was(env):
    built, row = env.insert(env.spec(one_part(LONG)))
    assert env.service.job_for(row)["pieces"][0]["text"] == LONG
    assert json.loads(row["options_json"])["pronunciations"] == [] and env.service.readings([row])[built.run["id"]]["pronunciations"] == {"entries": 0, "uses": 0}


def test_the_pieces_are_the_same_with_the_list_as_without_it(env):
    """Criterion 349: the respelling changes only the words given to the engine, so the cutting of the text into pieces cannot depend on it."""
    parts = one_part(LONG, LONG + " " + LONG + " " + LONG, "Hermes.")
    plain, _ = env.insert(env.spec(parts))
    seed_list(env, ("Nguyen", "win"), ("Hermes", "her meez"))
    listed, _ = env.insert(env.spec(parts))
    shape = lambda built: [(p["part_idx"], p["idx"], p["kind"], p["text"], p["chars"], p["continues"]) for p in built.pieces]  # noqa: E731
    assert shape(plain) == shape(listed) and [p["chars"] for p in plain.parts] == [p["chars"] for p in listed.parts]


def test_a_reading_keeps_the_entries_its_words_use_and_how_many_words_they_changed(env):
    seed_list(env, ("Nguyen", "win"), ("Hermes", "her meez"), ("Unused", "x"))
    built, row = env.insert(env.spec(one_part(LONG)))
    options = json.loads(row["options_json"])
    assert [e["word"] for e in options["pronunciations"]] == ["Hermes", "Nguyen"]  # the list's own order (by word), and not the entry the text never uses
    assert env.service.readings([row])[built.run["id"]]["pronunciations"] == {"entries": 2, "uses": 3}  # Nguyen twice, Hermes once


def test_changing_the_list_afterwards_changes_nothing_in_the_reading(env):
    """Criterion 350: a reading goes on with the list it began with, however the list changes while it waits for its turn or between its parts."""
    seed_list(env, ("Nguyen", "win"))
    built, row = env.insert(env.spec(one_part(LONG)))
    first = env.service.job_for(row)["pieces"][0]["text"]
    stored = env.store.pronunciations()[0]
    env.store.update_pronunciation(stored["id"], "Nguyen", "nguyen", "wynn", False, "2026-11-14T01:00:00Z")  # changed
    seed_list(env, ("Hermes", "her meez"))  # and added to
    assert env.service.job_for(row)["pieces"][0]["text"] == first and "wynn" not in first and "her meez" not in first
    env.store.delete_pronunciation(stored["id"])  # and the entry deleted
    assert env.service.job_for(row)["pieces"][0]["text"] == first


def test_making_the_same_text_again_uses_the_list_as_it_now_is(env):
    seed_list(env, ("Nguyen", "win"))
    _, before = env.insert(env.spec(one_part(LONG)))
    stored = env.store.pronunciations()[0]
    env.store.update_pronunciation(stored["id"], "Nguyen", "nguyen", "wynn", False, "2026-11-14T01:00:00Z")
    _, again = env.insert(env.spec(one_part(LONG)))
    assert env.service.job_for(before)["pieces"][0]["text"].startswith("win walked")
    assert env.service.job_for(again)["pieces"][0]["text"].startswith("wynn walked")  # "remaking is your choice": the new reading has the new respelling


def test_a_piece_corrected_after_a_failure_is_spoken_with_the_entries_the_reading_kept(env):
    seed_list(env, ("Nguyen", "win"))
    built, row = env.insert(env.spec(one_part(LONG)))
    assert env.store.edit_piece(built.run["id"], 0, 0, "Nguyen said something else entirely, and it is long enough to be a piece.") == "ok"
    assert env.service.job_for(row)["pieces"][0]["text"].startswith("win said something else")
    # the entries that were kept are the ones the words USED when the reading was made: a word that only appears in a correction is not respelled
    seed_list(env, ("Hermes", "her meez"))
    assert env.store.edit_piece(built.run["id"], 0, 0, "Hermes is the dog, and this correction is long enough to be a piece.") == "ok"
    assert env.service.job_for(row)["pieces"][0]["text"].startswith("Hermes is the dog")


def test_a_voice_sample_never_sees_the_list(env):
    """Criterion 351: a sample is the same sentence for every voice, so that voices can be compared like for like (§38.12 choice 20)."""
    from studio.speech.passage import SAMPLE_SENTENCE
    from test_speech_service import READY

    word = SAMPLE_SENTENCE.split()[0].strip(".,")  # "Good"
    seed_list(env, (word, "gooood"))
    voices = env.service.voices
    run = voices.sample_run(voices.sample_target(READY, "fake", voice="anna"))
    assert env.db.insert_run(run, 100) == "ok"
    job = env.service.job_for(env.db.get_run(run["id"]))
    assert [p["text"] for p in job["pieces"]] == [SAMPLE_SENTENCE] and "gooood" not in json.dumps(job)


# ---------------------------------------------------------------- a whole reading, made by the real worker
def frames_of(client, run_id: str, part_idx: int = 0) -> list[int]:
    return [p["frames"] for p in client.get(f"/api/readings/{run_id}/parts/{part_idx}").json()["pieces"]]


def expected_frames(client, spoken: str) -> int:
    """How many frames the FAKE engine makes for a piece whose spoken words are `spoken`: its tone lasts 0.3 s and 0.012 s a character, and the pause follows (engines/fake.py)."""
    pause_ms = client.app.state.settings.speech_pause_ms
    return int((0.3 + 0.012 * len(spoken)) * 24000) + 24000 * pause_ms // 1000


def test_the_fake_engine_speaks_the_respelling_and_the_part_still_shows_the_original(client):
    """The proof that the engine was GIVEN the respelling: its audio is as long as the respelled words are, not as the shown ones are, while the part's text (what the read-along
    highlights) is exactly what was sent."""
    spoken_word = "wiiiiiiiiiiiiiiiiiiiiiiiiiiin"  # much longer than "Nguyen", so the audio is measurably longer
    add(client, "Nguyen", spoken_word)
    shown = "Nguyen walked all the way home from the harbour, and Nguyen's dog ran ahead of him."
    made = make_reading(client, [{"title": "One", "paragraphs": [{"kind": "paragraph", "text": shown}]}])
    assert made["reading"]["pronunciations"] == {"entries": 1, "uses": 2}
    run = wait_for(client, made["id"])
    assert run["status"] == "done"
    detail = client.get(f"/api/readings/{run['id']}/parts/0").json()
    assert [p["text"] for p in detail["pieces"]] == [shown]
    spoken = shown.replace("Nguyen", spoken_word)
    assert frames_of(client, run["id"]) == [expected_frames(client, spoken)] != [expected_frames(client, shown)]


def test_a_list_changed_while_the_reading_waits_does_not_change_what_it_says(client_factory):
    """The reading is behind a slow one in the queue; the list is changed while it waits; when its turn comes it still says what it was made to say."""
    client = client_factory(fake_step_delay_ms=20)
    add(client, "Nguyen", "wiiiiiiiiiin")
    slow = make_reading(client, [part("Slow", 1, 1, {0: "[fake:slow]"})])  # holds the worker for two seconds
    waiting = make_reading(client, [{"title": "One", "paragraphs": [{"kind": "paragraph", "text": LONG}]}])
    stored = entries(client)[0]
    client.patch(f"/api/speech/pronunciations/{stored['id']}", json={"say": "w"})  # changed while "waiting" is still queued
    wait_until(client, slow["id"], lambda r: r["status"] == "done", "the slow reading is done")
    run = wait_for(client, waiting["id"])
    assert run["status"] == "done"
    assert frames_of(client, run["id"]) == [expected_frames(client, LONG.replace("Nguyen", "wiiiiiiiiiin"))]  # the respelling of the day it was made, not the new one
