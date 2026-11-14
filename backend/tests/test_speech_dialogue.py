"""Several voices in one text, on the server (DESIGN.md §44.5, criteria 362 to 369): what the review finds, what a request may say about speakers, and what the engine is given.

Four layers, each tested with the tool that suits it:
  * the review (`build_review`, and the route with a real reader process): the names found, their lines, the lines kept apart from the narration, the switch that turns the
    search off, and what is never a speaker (headings, code, tables);
  * the pieces: the cutting of a part into pieces never joins two speakers' words, and every piece knows whose it is;
  * the service, with a real database and no server (`Env`, as test_speech_service.py does): the voices a request may name, every refusal in its words, the job that goes to the
    worker (each speaker's own voice on their own pieces and nothing extra on the others) and what a reading keeps about its cast;
  * a whole reading made by the real worker with the fake engine, whose tone depends on the voice: the audio of each piece is compared with what the same engine makes
    for the voice that should have spoken it, so that "the right voice spoke" is proved by the sound and not by a spy.
The rule that decides what is a speaker (the name's shape, the label stop-list, the counts) is in test_speech_speakers.py.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import wait_for
from speech_files import make_docx, make_pdf
from test_speech_pronunciation import seed_list
from test_speech_readings import make_reading, wait_until
from test_speech_service import READY, Env
from test_speech_voices import saved_cloned_voice, saved_designed_voice

from studio.speech.engine import EngineSettings, PieceRequest, VoiceRef
from studio.speech.engines.fake import FakeSpeech
from studio.speech.refused import ReadingRefused
from studio.speech.service import ParagraphSpec, PartSpec, SpeakerSpec
from studio.speech.text.model import Block, Chapter, Extracted, Paragraph
from studio.speech.text.pieces import make_pieces
from studio.speech.text.prepare import Limits, build_review

LIMITS = Limits(max_chars=100_000, part_max_chars=30_000, chars_per_second=14.0)

# Lines long enough (the fake engine's pieces are at least 40 characters) not to be joined to a neighbour for being short.
NARRATION = "The sea went on all night, and nobody on the quay spoke of it until the morning came."
ALICE_1 = "Did you hear the sea last night? It was loud enough to wake the whole house."
BOB_1 = "I did, and I did not sleep at all, not for a single minute of the night."
ALICE_2 = "Then we should go down to the quay and look at what it has left behind."
BOB_2 = "Yes."

SCRIPT = f"""# The Gate

{NARRATION}

Alice: {ALICE_1}
Bob: {BOB_1}
Alice: {ALICE_2}
Bob: {BOB_2}

She walked to the door.
"""


def review_route(client, text: str, **fields: Any) -> dict[str, Any]:
    response = client.post("/api/speech/prepare", json={"source": "text", "text": text, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def paragraphs_of(review: dict[str, Any]) -> list[dict[str, Any]]:
    return [q for p in review["parts"] for q in p["paragraphs"]]


def extracted(*blocks: Block) -> Extracted:
    return Extracted(title=None, chapters=[Chapter(None, list(blocks))])


# ---------------------------------------------------------------- the review (criteria 363, 364)
def test_a_script_is_reviewed_with_the_names_that_begin_its_lines_and_their_lines(client):
    review = review_route(client, SCRIPT)
    assert review["speakers"] == [{"name": "Alice", "lines": 2, "sample": ALICE_1}, {"name": "Bob", "lines": 2, "sample": BOB_1}]  # in the order they first speak
    assert review["options"] == {"clean": True, "read_extras": False, "speakers": True}
    shape = [(q["kind"], q["speaker"], q["label"], q["text"]) for q in paragraphs_of(review)]
    assert shape == [
        ("heading", None, None, "The Gate"),
        ("paragraph", None, None, NARRATION),
        ("paragraph", "Alice", "Alice:", ALICE_1),  # the words of the line, without the name that began it; the name is kept in `label`
        ("paragraph", "Bob", "Bob:", BOB_1),
        ("paragraph", "Alice", "Alice:", ALICE_2),
        ("paragraph", "Bob", "Bob:", BOB_2),
        ("paragraph", None, None, "She walked to the door."),  # narration after the dialogue is the narrator's again
    ]


def test_a_text_with_no_speakers_has_an_empty_list_and_lines_with_no_speaker(client):
    review = review_route(client, "A plain paragraph of prose that has no speakers in it at all, only words.\n\nAnd a second one, as plain as the first.\n")
    assert review["speakers"] == [] and all(q["speaker"] is None and q["label"] is None for q in paragraphs_of(review))


def test_the_search_can_be_switched_off_and_the_text_is_then_exactly_what_it_was_before_1_25(client):
    off = review_route(client, SCRIPT, speakers=False)
    assert off["speakers"] == [] and off["options"]["speakers"] is False
    assert all(q["speaker"] is None and q["label"] is None for q in paragraphs_of(off))
    # with the search off the lines are not split apart: the lines of a block of text are one wrapped paragraph, names and all
    joined = [q["text"] for q in paragraphs_of(off) if q["text"].startswith("Alice:")]
    assert len(joined) == 1 and "Bob: I did" in joined[0]


def test_the_speaker_lines_are_split_by_the_names_in_a_markdown_file_too(client):
    data = SCRIPT.encode()
    response = client.post("/api/speech/prepare/file", params={"name": "scene.md"}, content=data)
    assert response.status_code == 200, response.text
    review = response.json()
    assert [s["name"] for s in review["speakers"]] == ["Alice", "Bob"]
    assert [q["speaker"] for q in paragraphs_of(review)] == [None, None, "Alice", "Bob", "Alice", "Bob", None]


def test_a_word_processor_file_with_a_paragraph_for_each_line_has_the_same_speakers(client):
    # a .docx keeps its paragraphs as they were typed, so every line is a block of its own already
    paragraphs = [NARRATION, f"Alice: {ALICE_1}", f"Bob: {BOB_1}", f"Alice: {ALICE_2}", f"Bob: {BOB_2}", "She walked to the door."]
    response = client.post("/api/speech/prepare/file", params={"name": "scene.docx"}, content=make_docx(paragraphs))
    assert response.status_code == 200, response.text
    review = response.json()
    assert [(s["name"], s["lines"]) for s in review["speakers"]] == [("Alice", 2), ("Bob", 2)]


def test_two_spellings_of_a_name_are_one_speaker_in_the_review_and_the_first_spelling_is_kept(client):
    review = review_route(client, f"Alice: {ALICE_1}\nBob: {BOB_1}\nALICE: {ALICE_2}\nbob_is_not_a_name: x\nBob: {BOB_2}\n")
    assert [(s["name"], s["lines"]) for s in review["speakers"]] == [("Alice", 2), ("Bob", 2)]
    assert [q["speaker"] for q in paragraphs_of(review)][:3] == ["Alice", "Bob", "Alice"]  # the third line began with ALICE: and is Alice's


def test_a_script_in_plain_text_with_no_markup_is_split_by_its_names(client):
    """The pasted text above begins with a `#` heading, so it is read as Markdown; a script with no markup at all goes the plain-text way, which splits lines itself."""
    plain = SCRIPT.replace("# The Gate\n\n", "")
    review = review_route(client, plain)
    assert [(s["name"], s["lines"]) for s in review["speakers"]] == [("Alice", 2), ("Bob", 2)]
    assert [q["speaker"] for q in paragraphs_of(review)] == [None, "Alice", "Bob", "Alice", "Bob", None]  # (and the narration after the dialogue is the narrator's again)
    assert [q["text"] for q in paragraphs_of(review)][1] == ALICE_1


def test_script_lines_with_no_full_stop_are_still_one_paragraph_each_in_plain_text(client):
    """Plain text joins a line to the next one unless it ends a sentence, because a wrapped line is the commonest thing in plain text. A script's lines often end with no mark at
    all (`Bob: Yes`), so they are split by the names the text has; with the search off they are the one wrapped paragraph they always were."""
    lines = ["Alice: Did you hear the sea last night and how loud it was", "Bob: I did and I did not sleep at all", "Alice: Then we should go down and look", "Bob: Yes"]
    review = review_route(client, "\n".join(lines) + "\n")
    assert [(q["speaker"], q["text"]) for q in paragraphs_of(review)] == [("Alice", lines[0][7:]), ("Bob", lines[1][5:]), ("Alice", lines[2][7:]), ("Bob", "Yes")]
    off = review_route(client, "\n".join(lines) + "\n", speakers=False)
    assert len(paragraphs_of(off)) == 1 and paragraphs_of(off)[0]["speaker"] is None


def test_the_search_off_finds_no_speakers_even_where_every_line_is_a_paragraph_already(client):
    """A word-processor file keeps its paragraphs, so lines that begin with names are separate paragraphs whether or not the splitting by names is on: only the switch keeps
    them from being found."""
    paragraphs = [NARRATION, f"Alice: {ALICE_1}", f"Bob: {BOB_1}", f"Alice: {ALICE_2}", f"Bob: {BOB_2}"]
    on = client.post("/api/speech/prepare/file", params={"name": "scene.docx"}, content=make_docx(paragraphs)).json()
    off = client.post("/api/speech/prepare/file", params={"name": "scene.docx", "speakers": "false"}, content=make_docx(paragraphs)).json()
    assert [s["name"] for s in on["speakers"]] == ["Alice", "Bob"]
    assert off["speakers"] == [] and all(q["speaker"] is None and q["label"] is None for q in paragraphs_of(off))
    assert [q["text"] for q in paragraphs_of(off)][1].startswith("Alice: Did you hear")  # the line, name and all, is what will be read


def test_the_search_can_be_switched_off_for_a_file_too(client):
    response = client.post("/api/speech/prepare/file", params={"name": "scene.md", "speakers": "false"}, content=SCRIPT.encode())
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["speakers"] == [] and review["options"]["speakers"] is False and all(q["speaker"] is None for q in paragraphs_of(review))


def test_the_speaker_lines_of_a_pdf_are_split_by_the_names_too(client):
    # a PDF has no paragraphs, only lines, which a reader joins by their endings; the lines of a script start new paragraphs at a name, and the name is not in the words
    lines = [NARRATION, f"Alice: {ALICE_1}", f"Bob: {BOB_1}", f"Alice: {ALICE_2}", f"Bob: {BOB_2}"]
    response = client.post("/api/speech/prepare/file", params={"name": "scene.pdf"}, content=make_pdf([lines]))
    assert response.status_code == 200, response.text
    review = response.json()
    assert [(s["name"], s["lines"]) for s in review["speakers"]] == [("Alice", 2), ("Bob", 2)]
    assert [(q["speaker"], q["text"]) for q in paragraphs_of(review)] == [(None, NARRATION), ("Alice", ALICE_1), ("Bob", BOB_1), ("Alice", ALICE_2), ("Bob", BOB_2)]


def test_a_heading_is_never_a_speaker_and_nor_is_code_or_a_table():
    """Only the paragraphs that will be read aloud can be a speaker's line; a heading or a block left out keeps its words whatever they begin with."""
    blocks = (Block("heading", "Alice: a chapter heading that begins like a line", 1), Block("paragraph", f"Alice: {ALICE_1}"), Block("paragraph", f"Bob: {BOB_1}"),
              Block("code", "Alice: this is code, not a line"), Block("paragraph", f"Alice: {ALICE_2}"), Block("table", "Bob: a, b\nc, d"), Block("paragraph", f"Bob: {BOB_2}"))
    review = build_review(extracted(*blocks), LIMITS, {})
    assert [(s["name"], s["lines"]) for s in review["speakers"]] == [("Alice", 2), ("Bob", 2)]  # the heading, the code and the table were not counted
    by_kind = [(q["kind"], q["skipped"], q["speaker"]) for q in paragraphs_of(review)]
    assert by_kind == [("heading", None, None), ("paragraph", None, "Alice"), ("paragraph", None, "Bob"), ("paragraph", "code", None),
                       ("paragraph", None, "Alice"), ("paragraph", "table", None), ("paragraph", None, "Bob")]
    heading = paragraphs_of(review)[0]
    assert heading["text"] == "Alice: a chapter heading that begins like a line"  # untouched


def test_the_characters_of_the_review_are_the_words_that_will_be_spoken(client):
    review = review_route(client, SCRIPT)
    spoken = sum(len(q["text"]) for q in paragraphs_of(review))
    assert review["chars"] == spoken  # the names that began the lines are not counted: they will not be read


# ---------------------------------------------------------------- the pieces (criterion 365)
def test_pieces_are_never_joined_across_a_change_of_speaker_and_each_knows_whose_it_is():
    paragraphs = [Paragraph("paragraph", "Yes.", speaker="Bob"), Paragraph("paragraph", "No.", speaker="Alice"), Paragraph("paragraph", "Maybe.", speaker="Alice"),
                  Paragraph("paragraph", "Perhaps not.")]
    pieces = make_pieces(paragraphs, 400, 40)
    # "Yes." is Bob's alone; "No." and "Maybe." are one speaker's and are joined, as short paragraphs always were; the narrator's is alone
    assert [(p.speaker, p.text) for p in pieces] == [("Bob", "Yes."), ("Alice", "No. Maybe."), (None, "Perhaps not.")]


def test_a_short_line_before_a_heading_keeps_its_speaker_when_it_is_spoken_alone():
    pieces = make_pieces([Paragraph("paragraph", "Yes.", speaker="Bob"), Paragraph("heading", "Chapter Two"), Paragraph("paragraph", NARRATION)], 400, 40)
    assert [(p.kind, p.speaker, p.text) for p in pieces] == [("paragraph", "Bob", "Yes."), ("heading", None, "Chapter Two"), ("paragraph", None, NARRATION)]


def test_a_long_line_cut_into_pieces_keeps_its_speaker_on_every_piece():
    long_line = " ".join(f"Sentence number {n} of a speech that goes on for quite a while." for n in range(30))
    pieces = make_pieces([Paragraph("paragraph", long_line, speaker="Alice")], 400, 40)
    assert len(pieces) > 1 and {p.speaker for p in pieces} == {"Alice"} and [p.continues for p in pieces] == [False] + [True] * (len(pieces) - 1)


def test_a_part_with_no_speakers_is_cut_into_the_pieces_it_always_was():
    """Without speakers the joining of short paragraphs is as it was: they are joined to the next one, never across a heading (the exact rules are pinned in test_speech_text.py,
    which is unchanged)."""
    paragraphs = [Paragraph("heading", "Chapter One"), Paragraph("paragraph", "Short."), Paragraph("paragraph", "Also short."), Paragraph("paragraph", NARRATION)]
    pieces = make_pieces(paragraphs, 400, 40)
    assert [(p.kind, p.text, p.speaker) for p in pieces] == [("heading", "Chapter One", None), ("paragraph", f"Short. Also short. {NARRATION}", None)]


# ---------------------------------------------------------------- what the service makes of a request (criteria 366, 367)
@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.db.close()


def script_parts() -> list[PartSpec]:
    """The script as the page would send it: the lines without their names, each paragraph with the speaker it belongs to (None for the narrator)."""
    return [PartSpec("One", [ParagraphSpec("heading", "The Gate"), ParagraphSpec("paragraph", NARRATION), ParagraphSpec("paragraph", ALICE_1, "Alice"),
                             ParagraphSpec("paragraph", BOB_1, "Bob"), ParagraphSpec("paragraph", ALICE_2, "Alice"), ParagraphSpec("paragraph", BOB_2, "Bob")])]


CAST = [SpeakerSpec("Alice", voice="bea"), SpeakerSpec("Bob")]  # Alice has a voice of her own; Bob has none, so he is read by the main voice


def test_a_reading_keeps_its_cast_and_how_many_pieces_each_speaker_has(env):
    built, row = env.insert(env.spec(script_parts(), speakers=CAST))
    options = json.loads(row["options_json"])
    assert [(s["name"], s["voice"] and s["voice"]["id"]) for s in options["speakers"]] == [("Alice", "bea"), ("Bob", None)]
    assert options["speakers"][0]["voice"] == {"kind": "preset", "id": "bea", "label": "Bea", "gender": "female", "accent": None}
    pieces = env.store.pieces(built.run["id"], 0)
    assert [p["speaker"] for p in pieces] == [None, None, "Alice", "Bob", "Alice", "Bob"]  # the heading and the narration are nobody's
    described = env.service.readings([row])[built.run["id"]]["speakers"]
    assert described == [{"name": "Alice", "voice": options["speakers"][0]["voice"], "lines": 2}, {"name": "Bob", "voice": None, "lines": 2}]
    assert [p["speaker"] for p in env.service.part_detail(row, 0)["pieces"]] == [None, None, "Alice", "Bob", "Alice", "Bob"]


def test_a_reading_with_no_speakers_describes_none_and_its_pieces_have_no_speaker(env):
    built, row = env.insert(env.spec())
    assert json.loads(row["options_json"])["speakers"] == [] and env.service.readings([row])[built.run["id"]]["speakers"] == []
    assert {p["speaker"] for p in env.store.pieces(built.run["id"], 0)} == {None}


def test_each_speakers_pieces_carry_their_voice_and_nothing_else_carries_one(env):
    _, row = env.insert(env.spec(script_parts(), speakers=CAST))
    job = env.service.job_for(row)
    assert job["voice"] == "anna" and job["ref"] is None  # the main voice, as always
    pieces = job["pieces"]
    assert [p["text"] for p in pieces] == ["The Gate", NARRATION, ALICE_1, BOB_1, ALICE_2, BOB_2]
    assert [p.get("voice") for p in pieces] == [None, None, "bea", None, "bea", None]  # Alice's own; Bob has no voice chosen, so his pieces carry none
    assert all("ref" not in p and "style" not in p for p in (pieces[0], pieces[1], pieces[3], pieces[5]))  # the narrator's and Bob's pieces are exactly what they were before 1.25
    assert set(pieces[2]) == {"key", "text", "kind", "seed", "out", "pause_ms", "voice", "ref", "style"}  # Alice's: the three extra fields, and nothing of the page's own bookkeeping
    assert set(pieces[1]) == {"key", "text", "kind", "seed", "out", "pause_ms"}


def test_a_job_with_no_speakers_has_no_voice_on_any_piece(env):
    _, row = env.insert(env.spec())
    assert all(not ({"voice", "ref", "style"} & set(p)) for p in env.service.job_for(row)["pieces"])


def test_the_job_is_validated_by_the_worker_side_reader_and_a_speakers_voice_survives_it(env):
    """The job is read by the worker with `SpeechJob.from_dict`; what a speaker's pieces carry has to be accepted there and come out as the voice that speaks them."""
    from studio.speech.job import SpeechJob

    _, row = env.insert(env.spec(script_parts(), speakers=CAST))
    job = SpeechJob.from_dict(env.service.job_for(row), env.settings.data_dir)
    assert [(p.own_voice, p.voice) for p in job.pieces] == [(False, ""), (False, ""), (True, "bea"), (False, ""), (True, "bea"), (False, "")]


def test_the_worker_side_reader_takes_a_speakers_reference_clip_only_from_the_voices_folder(env):
    """The worker is told where a speaker's reference clip is, like the main voice's; a path that leaves the voices folder, or a file that is not there, is refused."""
    from studio import wavfile
    from studio.speech.job import SpeechJob

    main, other = env.add_voice("a" * 32, name="Main"), env.add_voice("b" * 32, name="Other")
    _, row = env.insert(env.spec(script_parts(), voice="", saved_voice=main, speakers=[SpeakerSpec("Alice", saved_voice=other), SpeakerSpec("Bob")]))
    for voice_id in (main, other):  # the clips themselves: the worker side looks at the files
        clip = env.settings.data_dir / "speech" / "voices" / voice_id / "reference.wav"
        clip.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write_wav(clip, bytes(2 * 2400), 24000, channels=1)
    job = env.service.job_for(row)
    accepted = SpeechJob.from_dict(job, env.settings.data_dir)
    assert accepted.pieces[2].own_voice and accepted.pieces[2].ref is not None and accepted.pieces[2].ref.path.endswith(f"voices/{other}/reference.wav")
    assert not accepted.pieces[3].own_voice and accepted.pieces[3].ref is None  # Bob is read by the main voice, whose clip is the job's
    for bad in (f"speech/readings/{row['id']}/0000/00000.wav", "../outside.wav", f"speech/voices/{other}/missing.wav"):
        broken = json.loads(json.dumps(job))
        broken["pieces"][2]["ref"]["path"] = bad
        with pytest.raises(ValueError):
            SpeechJob.from_dict(broken, env.settings.data_dir)


def test_a_style_note_is_given_only_to_the_voices_that_take_one(env):
    """`anna` takes a style note and `bea` does not: a speaker with a voice that takes none is spoken without it, and a speaker whose voice does take one has it."""
    cast = [SpeakerSpec("Alice", voice="bea"), SpeakerSpec("Bob", voice="anna")]
    _, row = env.insert(env.spec(script_parts(), speakers=cast, style="warm and slow"))
    pieces = env.service.job_for(row)["pieces"]
    assert env.service.job_for(row)["style"] == "warm and slow"
    assert [(p.get("voice"), p.get("style")) for p in pieces[2:]] == [("bea", None), ("anna", "warm and slow"), ("bea", None), ("anna", "warm and slow")]


def test_a_voice_you_made_can_speak_for_a_speaker_when_the_main_voice_is_one_you_made_too(env):
    main, other = env.add_voice("a" * 32, name="Main"), env.add_voice("b" * 32, name="Other")
    cast = [SpeakerSpec("Alice", saved_voice=other), SpeakerSpec("Bob")]
    built, row = env.insert(env.spec(script_parts(), voice="", saved_voice=main, speakers=cast))
    assert json.loads(row["options_json"])["variant"] == "clone"
    pieces = env.service.job_for(row)["pieces"]
    assert [p.get("voice") for p in pieces] == [None, None, "", None, "", None]  # a saved voice is spoken through its clip, not through a preset's id
    assert pieces[2]["ref"] == {"path": f"speech/voices/{other}/reference.wav", "text": "the words said", "kind": "cloned"}
    assert "ref" not in pieces[3]  # Bob is read by the main voice, whose clip is the job's


def test_the_two_kinds_of_voice_are_never_mixed_in_one_reading(env):
    """An engine holds one variant in memory; the fake engine's preset voices and its saved voices are two variants, as Qwen3-TTS's are two checkpoints."""
    saved = env.add_voice()
    with pytest.raises(ReadingRefused) as caught:
        env.service.build(env.spec(script_parts(), speakers=[SpeakerSpec("Alice", saved_voice=saved), SpeakerSpec("Bob")]), READY)  # main voice is a preset
    assert (caught.value.status, caught.value.code, caught.value.field) == (422, "speaker_variant", "speakers")
    assert "Alice" in caught.value.message and caught.value.hint  # it names the speaker and says what to do
    with pytest.raises(ReadingRefused) as caught:
        env.service.build(env.spec(script_parts(), voice="", saved_voice=saved, speakers=[SpeakerSpec("Alice", voice="bea"), SpeakerSpec("Bob")]), READY)  # and the other way
    assert caught.value.code == "speaker_variant"


@pytest.mark.parametrize("cast, code", [
    ([SpeakerSpec("Alice", voice="nobody"), SpeakerSpec("Bob")], "unknown_voice"),
    ([SpeakerSpec("Alice", saved_voice="f" * 32), SpeakerSpec("Bob")], "unknown_voice"),
    ([SpeakerSpec("Alice"), SpeakerSpec("alice")], "speaker_twice"),
    ([SpeakerSpec("Alice"), SpeakerSpec("  Bob  "), SpeakerSpec("bob")], "speaker_twice"),
    ([SpeakerSpec(""), SpeakerSpec("Bob")], "speaker_name"),
    ([SpeakerSpec("   "), SpeakerSpec("Bob")], "speaker_name"),
    ([SpeakerSpec("A" * 41), SpeakerSpec("Bob")], "speaker_name"),
    ([SpeakerSpec(f"Name{n}") for n in range(13)], "too_many_speakers"),
])
def test_a_cast_that_cannot_be_used_is_refused_in_words_naming_the_field(env, cast, code):
    with pytest.raises(ReadingRefused) as caught:
        env.service.build(env.spec(script_parts(), speakers=cast), READY)
    assert caught.value.status == 422 and caught.value.code == code and caught.value.field == "speakers" and caught.value.message


def test_twelve_speakers_and_a_name_of_forty_characters_are_allowed(env):
    cast = [SpeakerSpec("A" * 40), SpeakerSpec("Alice"), SpeakerSpec("Bob")] + [SpeakerSpec(f"Name{n}") for n in range(9)]
    built, row = env.insert(env.spec(script_parts(), speakers=cast))
    assert len(json.loads(row["options_json"])["speakers"]) == 12


def test_a_voice_of_another_engine_cannot_speak_for_a_speaker(env):
    other = env.add_voice(engine="fake-basic")
    with pytest.raises(ReadingRefused) as caught:
        env.service.build(env.spec(script_parts(), speakers=[SpeakerSpec("Alice", saved_voice=other), SpeakerSpec("Bob")]), READY)
    assert caught.value.code == "voice_engine" and caught.value.field == "speakers"


def test_an_engine_that_cannot_clone_takes_a_preset_for_a_speaker_and_refuses_a_saved_voice(env):
    cast = [SpeakerSpec("Alice", voice="basic_two"), SpeakerSpec("Bob")]
    _, row = env.insert(env.spec(script_parts(), engine="fake-basic", voice="basic_one", speakers=cast))
    assert [p.get("voice") for p in env.service.job_for(row)["pieces"]][2:] == ["basic_two", None, "basic_two", None]
    with pytest.raises(ReadingRefused) as caught:
        env.service.build(env.spec(script_parts(), engine="fake-basic", voice="basic_one", speakers=[SpeakerSpec("Alice", saved_voice=env.add_voice(engine="fake-basic"))]), READY)
    assert caught.value.code == "voice_engine"


@pytest.mark.parametrize("paragraph, code", [
    (ParagraphSpec("paragraph", ALICE_1, "Carol"), "unknown_speaker"),  # a speaker who is not in the cast of the request
    (ParagraphSpec("heading", "Alice's chapter", "Alice"), "unknown_speaker"),  # a heading has no speaker, even one who is in the cast
])
def test_a_paragraph_whose_speaker_is_not_in_the_cast_or_is_a_heading_is_refused(env, paragraph, code):
    with pytest.raises(ReadingRefused) as caught:
        env.service.build(env.spec([PartSpec("One", [paragraph, ParagraphSpec("paragraph", BOB_1, "Bob")])], speakers=CAST), READY)
    assert caught.value.status == 422 and caught.value.code == code and caught.value.field == "parts"


def test_a_speaker_with_no_lines_is_allowed_and_has_none(env):
    built, row = env.insert(env.spec(script_parts(), speakers=CAST + [SpeakerSpec("Carol", voice="carl")]))  # the page may offer a name whose lines were all left out
    assert env.service.readings([row])[built.run["id"]]["speakers"][-1] == {"name": "Carol", "voice": json.loads(row["options_json"])["speakers"][-1]["voice"], "lines": 0}


def test_a_speakers_name_may_be_spelled_with_extra_white_space_in_the_request(env):
    built, row = env.insert(env.spec([PartSpec("One", [ParagraphSpec("paragraph", ALICE_1, "Mary  Jane")])], speakers=[SpeakerSpec(" Mary   Jane ", voice="bea")]))
    assert [p["speaker"] for p in env.store.pieces(built.run["id"], 0)] == ["Mary Jane"]


def test_the_pronunciation_list_is_applied_to_a_speakers_words_as_to_any_others(env):
    seed_list(env, ("quay", "key"))
    _, row = env.insert(env.spec(script_parts(), speakers=CAST))
    pieces = env.service.job_for(row)["pieces"]
    assert pieces[4]["text"] == ALICE_2.replace("quay", "key") and pieces[4]["voice"] == "bea"  # spoken in Alice's voice, with the respelling


def test_a_reading_with_a_speaker_whose_voice_has_been_deleted_cannot_be_continued_and_says_whose(env):
    voice = env.add_voice("a" * 32, name="Alices")
    _, row = env.insert(env.spec(script_parts(), voice="", saved_voice=env.add_voice("c" * 32, name="Main"), speakers=[SpeakerSpec("Alice", saved_voice=voice), SpeakerSpec("Bob")]))
    with env.db.tx() as c:
        c.execute("DELETE FROM voices WHERE id=?", (voice,))
    with pytest.raises(ValueError) as caught:
        env.service.job_for(row)
    assert "Alice" in str(caught.value) and "deleted" in str(caught.value)


def test_a_voice_a_waiting_reading_gives_to_a_speaker_counts_as_in_use(env):
    voice = env.add_voice("a" * 32, name="Alices")
    assert env.store.runs_using_voice(voice) == 0
    env.insert(env.spec(script_parts(), voice="", saved_voice=env.add_voice("c" * 32, name="Main"), speakers=[SpeakerSpec("Alice", saved_voice=voice), SpeakerSpec("Bob")]))
    assert env.store.runs_using_voice(voice) == 1 and env.store.runs_using_voice("d" * 32) == 0


# ---------------------------------------------------------------- the routes (criteria 366, 367)
def body_with_speakers(**fields: Any) -> dict[str, Any]:
    paragraphs = [{"kind": "paragraph", "text": NARRATION}, {"kind": "paragraph", "text": ALICE_1, "speaker": "Alice"}, {"kind": "paragraph", "text": BOB_1, "speaker": "Bob"}]
    body = {"title": "A scene", "source": {"kind": "paste"}, "engine": "fake", "voice": "anna", "parts": [{"title": "One", "paragraphs": paragraphs}],
            "speakers": [{"name": "Alice", "voice": "bea"}, {"name": "Bob"}]}
    body.update(fields)
    return body


def post_reading(client, expect: int = 201, **fields: Any) -> dict[str, Any]:
    from test_speech_readings import wait_for_engines

    wait_for_engines(client)
    response = client.post("/api/readings", json=body_with_speakers(**fields))
    assert response.status_code == expect, response.text
    return response.json()


def test_the_route_takes_a_cast_and_the_reading_describes_it(client):
    made = post_reading(client)
    assert [(s["name"], s["lines"]) for s in made["reading"]["speakers"]] == [("Alice", 1), ("Bob", 1)]
    assert made["reading"]["speakers"][0]["voice"]["id"] == "bea" and made["reading"]["speakers"][1]["voice"] is None


@pytest.mark.parametrize("fields, status, code, field", [
    ({"speakers": [{"name": "Alice", "voice": "nobody"}, {"name": "Bob"}]}, 422, "unknown_voice", "speakers"),
    ({"speakers": [{"name": "Alice"}, {"name": "ALICE"}]}, 422, "speaker_twice", "speakers"),
    ({"speakers": [{"name": "Alice"}]}, 422, "unknown_speaker", "parts"),  # Bob has a line and is not in the cast
])
def test_the_route_refuses_what_the_service_refuses_with_the_same_words(client, fields, status, code, field):
    refused = post_reading(client, expect=status, **fields)
    assert refused["code"] == code and refused["field"] == field and refused["detail"]


def test_a_request_with_a_speaker_field_the_route_does_not_know_is_refused(client):
    response = client.post("/api/readings", json=body_with_speakers(speakers=[{"name": "Alice", "colour": "red"}, {"name": "Bob"}]))
    assert response.status_code == 422


def test_a_voice_that_a_waiting_reading_gives_to_a_speaker_cannot_be_deleted(client):
    voice = saved_cloned_voice(client)
    main = saved_designed_voice(client, "Main")
    post_reading(client, voice="", saved_voice=main["id"], speakers=[{"name": "Alice", "saved_voice": voice["id"]}, {"name": "Bob"}],
                 parts=[{"title": "One", "paragraphs": [{"kind": "paragraph", "text": "[fake:slow] " + ALICE_1, "speaker": "Alice"}, {"kind": "paragraph", "text": BOB_1, "speaker": "Bob"}]}])
    refused = client.delete(f"/api/speech/voices/{voice['id']}")
    assert refused.status_code == 409 and refused.json()["code"] == "voice_in_use"


# ---------------------------------------------------------------- a whole reading, made by the real worker (criterion 366)
def pcm_of(wav: bytes) -> bytes:
    """The sample bytes of a WAV: what follows the `data` tag and its length."""
    at = wav.index(b"data")
    return wav[at + 8:]


def engine_says(text: str, voice: str = "", ref: VoiceRef | None = None, seed: int = 0, variant: str = "default", style: str | None = None) -> bytes:
    """What the fake engine itself makes for these words in this voice: the sound a piece must begin with. (The piece's file continues with the pause after the words.)"""
    engine = FakeSpeech(EngineSettings(variant=variant))
    return engine.synthesize(PieceRequest(text=text, voice=voice, ref=ref, seed=seed, style=style), lambda: False).pcm


def made_pieces(client, run_id: str) -> list[tuple[dict[str, Any], bytes]]:
    store = client.app.state.jobs.speech.store
    return [(row, pcm_of(client.get(f"/api/speech/{run_id}/parts/0/pieces/{row['idx']}.wav").content)) for row in store.pieces(run_id, 0)]


def test_each_speaker_is_spoken_in_their_voice_by_the_real_worker(client):
    """The narrator is spoken by Anna, Alice by Bea, and Bob, who was given no voice, by Anna: the audio of every piece is what the engine makes for exactly that voice."""
    parts = [{"title": "One", "paragraphs": [{"kind": "paragraph", "text": NARRATION}, {"kind": "paragraph", "text": ALICE_1, "speaker": "Alice"},
                                              {"kind": "paragraph", "text": BOB_1, "speaker": "Bob"}, {"kind": "paragraph", "text": ALICE_2, "speaker": "Alice"}]}]
    run = wait_for(client, post_reading(client, parts=parts)["id"])
    assert run["status"] == "done", run
    pieces = made_pieces(client, run["id"])
    expected_voices = ["anna", "bea", "anna", "bea"]
    for (row, pcm), voice in zip(pieces, expected_voices):
        assert pcm.startswith(engine_says(row["text"], voice, seed=row["seed"])), f"piece {row['idx']} was not spoken in {voice}"
    # and the proof is not vacuous: the same words in the other voice are different sound
    row, pcm = pieces[1]
    assert not pcm.startswith(engine_says(row["text"], "anna", seed=row["seed"]))


def test_a_style_note_reaches_the_voices_that_take_one_and_not_the_others_in_the_sound(client):
    """Anna (the main voice) takes a style note, Bea does not: the sound of Bea's line is made with no style, the narrator's with the note."""
    parts = [{"title": "One", "paragraphs": [{"kind": "paragraph", "text": NARRATION}, {"kind": "paragraph", "text": ALICE_1, "speaker": "Alice"}]}]
    run = wait_for(client, post_reading(client, parts=parts, style="calm and slow")["id"])
    assert run["status"] == "done", run
    (narration, narrator_pcm), (alice, alice_pcm) = made_pieces(client, run["id"])
    assert narrator_pcm.startswith(engine_says(narration["text"], "anna", seed=narration["seed"], style="calm and slow"))
    assert alice_pcm.startswith(engine_says(alice["text"], "bea", seed=alice["seed"], style=None))


def test_a_reading_in_a_voice_you_made_with_a_speaker_in_another_is_spoken_by_each(client):
    main, other = saved_designed_voice(client, "Main"), saved_cloned_voice(client, "Other")
    store = client.app.state.jobs.speech.store
    refs = {v["id"]: store.voice(v["id"]) for v in (main, other)}
    parts = [{"title": "One", "paragraphs": [{"kind": "paragraph", "text": NARRATION}, {"kind": "paragraph", "text": ALICE_1, "speaker": "Alice"}]}]
    run = wait_for(client, post_reading(client, voice="", saved_voice=main["id"], parts=parts, speakers=[{"name": "Alice", "saved_voice": other["id"]}])["id"])
    assert run["status"] == "done" and run["reading"]["variant"] == "clone", run
    pieces = made_pieces(client, run["id"])
    for (row, pcm), voice in zip(pieces, (main, other)):
        saved = refs[voice["id"]]
        ref = VoiceRef(path=saved["ref_path"], text=saved["ref_text"], kind=saved["kind"])
        assert pcm.startswith(engine_says(row["text"], ref=ref, seed=row["seed"], variant="clone"))


def test_a_reading_made_with_no_speakers_is_spoken_as_it_always_was(client):
    parts = [{"title": "One", "paragraphs": [{"kind": "paragraph", "text": NARRATION}, {"kind": "paragraph", "text": ALICE_1}]}]
    run = wait_for(client, make_reading(client, parts)["id"])
    assert run["status"] == "done" and run["reading"]["speakers"] == []
    for row, pcm in made_pieces(client, run["id"]):
        assert pcm.startswith(engine_says(row["text"], "anna", seed=row["seed"]))


def test_the_name_that_began_a_line_is_not_in_the_words_a_piece_holds_so_the_engine_never_reads_it(client):
    """The speaker's name is not in the words a piece holds (the engine must not read it), so the pieces' text is the words alone."""
    run = wait_for(client, post_reading(client)["id"])
    assert run["status"] == "done"
    detail = client.get(f"/api/readings/{run['id']}/parts/0").json()
    assert [p["text"] for p in detail["pieces"]] == [NARRATION, ALICE_1, BOB_1] and [p["speaker"] for p in detail["pieces"]] == [None, "Alice", "Bob"]
    assert all("Alice:" not in p["text"] and "Bob:" not in p["text"] for p in detail["pieces"])


def test_a_corrected_piece_of_a_speaker_is_still_spoken_in_their_voice(client):
    """After a failure the person corrects the words of one piece and continues: the new words are Alice's, in Alice's voice, like the rest of her lines."""
    parts = [{"title": "One", "paragraphs": [{"kind": "paragraph", "text": NARRATION}, {"kind": "paragraph", "text": "[fake:error] " + ALICE_1, "speaker": "Alice"}]}]
    run = wait_for(client, post_reading(client, parts=parts)["id"])
    assert run["status"] == "failed", run
    corrected = ALICE_1 + " That is the corrected version of what she says."
    fixed = client.patch(f"/api/readings/{run['id']}/parts/0/pieces/1", json={"text": corrected})
    assert fixed.status_code == 200, fixed.text
    assert client.post(f"/api/readings/{run['id']}/continue").status_code in (200, 202)
    done = wait_for(client, run["id"])
    assert done["status"] == "done", done
    row, pcm = made_pieces(client, run["id"])[1]
    assert row["speaker"] == "Alice" and pcm.startswith(engine_says(corrected, "bea", seed=row["seed"]))
