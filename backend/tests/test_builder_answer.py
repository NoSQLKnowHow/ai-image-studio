"""What the model answers and what the studio keeps of it (DESIGN.md section 37.5; criteria 224, 227, 250, 255, 257)."""

from __future__ import annotations

import json

import pytest

from studio.builder import CHIP_CHARS, REPLY_CHARS, TITLE_CHARS, normalize_tag
from studio.builder.answer import BadAnswer, Limits, answer_schema, check_settings, clean, plain_answer, read_answer, reply_so_far

LIMITS = Limits(prompt_chars=500, draft_steps=12, music_max_seconds=300)


def question(n: int = 1, chips=("Dusk", "Dawn", "Noon")) -> dict:
    return {"id": f"q{n}", "text": f"Question {n}?", "chips": list(chips)}


def answer_of(data: dict, kind: str = "images", **kw):
    return read_answer(json.dumps(data), kind, LIMITS, **kw)


# ------------------------------------------------------------------ the schema the model is given
def test_the_reply_comes_first_in_the_schema_so_the_page_can_show_it_as_it_is_written():
    schema = answer_schema("images")
    assert list(schema["properties"])[0] == "reply" and schema["required"] == ["reply"] and schema["type"] == "object"


@pytest.mark.parametrize("kind", ["images", "vector", "music", "edit"])
def test_each_kind_has_its_own_settings_and_only_those(kind):
    settings = answer_schema(kind)["properties"]["draft"]["properties"]["settings"]["properties"]
    assert set(settings) == {"images": {"shape", "guidance", "steps"}, "edit": {"shape", "guidance", "steps"},
                             "vector": {"kind", "colours", "palette", "width_mm", "height_mm"}, "music": {"fields", "instrumental", "lyrics", "seconds"}}[kind]


def test_the_shapes_the_model_may_pick_are_the_studios_and_auto_only_for_an_edit():
    shapes = lambda kind: answer_schema(kind)["properties"]["draft"]["properties"]["settings"]["properties"]["shape"]["enum"]  # noqa: E731
    assert shapes("images") == ["1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16"] and shapes("edit") == shapes("images") + ["auto"]


def test_wordings_are_in_the_schema_only_when_they_are_asked_for():
    assert "variations" not in answer_schema("images")["properties"] and "variations" in answer_schema("images", variations=True)["properties"]


# ------------------------------------------------------------------ an answer that is all right
def test_a_whole_answer_is_kept_as_it_is():
    answer = answer_of({"reply": "Lovely. A few questions.", "title": "Lighthouse in a storm", "questions": [question(1), question(2, ("a", "b", "c", "d", "e"))],
                        "draft": {"prompt": "A lighthouse in a storm", "negative": "blurry", "settings": {"shape": "3:2", "guidance": 4, "steps": 8}, "note": "first take"},
                        "remember": ["photographic"], "tags": ["Sea", " storm "], "done": True})
    assert answer.notices == [] and not answer.plain and answer.done is True and answer.title == "Lighthouse in a storm"
    assert [(q.id, q.text, q.chips) for q in answer.questions] == [("q1", "Question 1?", ["Dusk", "Dawn", "Noon"]), ("q2", "Question 2?", ["a", "b", "c", "d", "e"])]
    assert (answer.draft.prompt, answer.draft.negative, answer.draft.note) == ("A lighthouse in a storm", "blurry", "first take")
    assert answer.draft.settings == {"shape": "3:2", "guidance": 4.0, "steps": 8} and answer.remember == ["photographic"] and answer.tags == ["sea", "storm"]
    content = answer.to_content()
    assert json.loads(json.dumps(content)) == content and set(content) == {"reply", "title", "questions", "draft", "variations", "remember", "tags", "done", "notices", "plain"}


def test_only_a_reply_is_needed():
    answer = answer_of({"reply": "Tell me more."})
    assert answer.reply == "Tell me more." and answer.questions == [] and answer.draft is None and answer.done is False and answer.title is None


@pytest.mark.parametrize("wrap", [lambda s: f"```json\n{s}\n```", lambda s: f"Here you go:\n{s}\nHope that helps.", lambda s: f"  \n{s}  "])
def test_a_fence_or_a_sentence_around_the_object_is_tolerated(wrap):
    assert read_answer(wrap(json.dumps({"reply": "ok"})), "images", LIMITS).reply == "ok"


# ------------------------------------------------------------------ an answer that cannot be read
@pytest.mark.parametrize("text", ["", "   ", "just some words", "{not json", "[1, 2]", '"a string"', "42", "null", '{"questions": []}', '{"reply": 5}', '{"reply": null}', '{"reply": ""}',
                                  '{"reply": "", "questions": [{"id": "q1", "text": "", "chips": []}]}'])
def test_what_is_not_an_answer_is_refused(text):
    with pytest.raises(BadAnswer):
        read_answer(text, "images", LIMITS)


def test_an_empty_reply_is_fine_when_there_are_questions_or_a_draft():
    assert read_answer(json.dumps({"reply": "", "questions": [question()]}), "images", LIMITS).questions
    assert read_answer(json.dumps({"reply": "", "draft": {"prompt": "x"}}), "images", LIMITS).draft is not None


def test_a_kind_the_studio_does_not_have_is_a_bug_not_an_answer():
    with pytest.raises(ValueError):
        read_answer('{"reply": "x"}', "sculptures", LIMITS)


def test_plain_text_is_kept_as_written_with_nothing_else():
    answer = plain_answer("  I would love to help!\n\nWhat do you want?  ")
    assert answer.plain is True and answer.reply == "I would love to help!\n\nWhat do you want?" and answer.questions == [] and answer.draft is None and answer.tags == []
    long = plain_answer("x" * (REPLY_CHARS + 50))
    assert len(long.reply) == REPLY_CHARS + 1 and long.reply.endswith("…") and long.notices == [f"The reply was shortened to {REPLY_CHARS} characters."]


# ------------------------------------------------------------------ text from outside is only ever text
def test_control_characters_are_removed_and_lines_are_kept_where_lines_belong():
    answer = answer_of({"reply": "a\x00b\x07c\nsecond‮line", "title": "ti\ntle\x1b", "questions": [{"id": "q\x001", "text": "Wh\x00en?", "chips": ["Du\x00sk", "Dawn", "Noon"]}],
                        "draft": {"prompt": "one\x00\ntwo", "note": "n\x00ote"}})
    assert answer.reply == "abc\nsecondline" and answer.title == "ti tle" and answer.questions[0].text == "When?" and answer.questions[0].chips[0] == "Dusk"
    assert answer.questions[0].id == "q1" and answer.draft.prompt == "one\ntwo" and answer.draft.note == "note"


def test_markup_is_not_interpreted_it_is_just_characters():
    answer = answer_of({"reply": "<script>alert(1)</script> **bold** [x](http://e.com)"})
    assert answer.reply == "<script>alert(1)</script> **bold** [x](http://e.com)"  # kept exactly; the page shows it as text (criterion 250)


def test_clean_leaves_non_text_alone():
    assert clean(None) == "" and clean(5) == "" and clean(["a"]) == "" and clean("  a   b \t c ") == "a b c" and clean("abcdef", 3) == "abc"


def test_a_long_reply_is_shortened_and_says_so():
    answer = answer_of({"reply": "x" * (REPLY_CHARS + 10)})
    assert answer.reply == "x" * REPLY_CHARS + "…" and answer.notices == [f"The reply was shortened to {REPLY_CHARS} characters."]
    assert answer_of({"reply": "x" * REPLY_CHARS}).notices == []  # exactly the limit is fine


def test_a_long_title_is_cut_to_the_limit():
    assert answer_of({"reply": "x", "title": "t" * (TITLE_CHARS + 30)}).title == "t" * TITLE_CHARS


# ------------------------------------------------------------------ the questions
def test_at_most_three_questions_and_the_others_are_said_to_be_left_out():
    answer = answer_of({"reply": "x", "questions": [question(n) for n in range(1, 6)]})
    assert [q.id for q in answer.questions] == ["q1", "q2", "q3"]
    assert answer.notices == ["The builder asked 5 questions; the studio shows 3 at a time, so the others were left out."]


def test_exactly_three_questions_say_nothing():
    assert answer_of({"reply": "x", "questions": [question(n) for n in range(1, 4)]}).notices == []


def test_two_questions_with_the_same_id_get_their_own():
    answer = answer_of({"reply": "x", "questions": [{**question(1), "id": "same"}, {**question(2), "id": "same"}, {"text": "No id?", "chips": ["a", "b", "c"]}]})
    ids = [q.id for q in answer.questions]
    assert len(set(ids)) == 3 and ids[0] == "same"


def test_chips_are_trimmed_different_from_each_other_and_at_most_five():
    answer = answer_of({"reply": "x", "questions": [{"id": "q1", "text": "When?", "chips": ["  Dusk ", "dusk", "DUSK", "Dawn", "Noon", "Night", "Storm", "Fog"]}]})
    assert answer.questions[0].chips == ["Dusk", "Dawn", "Noon", "Night", "Storm"] and answer.notices == []


def test_a_chip_over_sixty_characters_is_dropped():
    answer = answer_of({"reply": "x", "questions": [{"id": "q1", "text": "When?", "chips": ["a", "b", "c", "d" * (CHIP_CHARS + 1)]}]})
    assert answer.questions[0].chips == ["a", "b", "c"]
    assert answer_of({"reply": "x", "questions": [{"id": "q1", "text": "When?", "chips": ["a", "b", "d" * CHIP_CHARS]}]}).questions[0].chips[2] == "d" * CHIP_CHARS


@pytest.mark.parametrize("chips", [["a", "b"], ["a"], ["a", "a", "A"], "not a list", [1, 2, 3]])
def test_a_question_with_fewer_than_three_chips_keeps_the_question_and_loses_the_chips_and_says_so(chips):
    answer = answer_of({"reply": "x", "questions": [{"id": "q1", "text": "When?", "chips": chips}]})
    assert answer.questions[0].text == "When?" and answer.questions[0].chips == []
    assert answer.notices == ['A question ("When?") came with too few suggestions, so it has none; you can answer it in words.']


def test_a_question_with_no_chips_at_all_says_nothing():
    answer = answer_of({"reply": "x", "questions": [{"id": "q1", "text": "Anything else?"}]})
    assert answer.questions[0].chips == [] and answer.notices == []


def test_a_question_without_text_is_not_a_question():
    assert answer_of({"reply": "x", "questions": [{"id": "q1", "text": "  ", "chips": ["a", "b", "c"]}, "nonsense", 7, None]}).questions == []


# ------------------------------------------------------------------ the draft
def test_a_prompt_over_the_studios_limit_is_left_out_and_the_reply_says_so():
    answer = answer_of({"reply": "x", "draft": {"prompt": "p" * 501, "settings": {"shape": "16:9"}}})
    assert answer.draft is None and answer.notices == ["The builder wrote a prompt of 501 characters; the studio allows up to 500, so it was left out."]
    assert answer_of({"reply": "x", "draft": {"prompt": "p" * 500}}).draft.prompt == "p" * 500


def test_a_negative_prompt_over_the_limit_is_left_out_but_the_prompt_stays():
    answer = answer_of({"reply": "x", "draft": {"prompt": "fine", "negative": "n" * 501}})
    assert answer.draft.prompt == "fine" and answer.draft.negative is None and "negative prompt of 501" in answer.notices[0]


@pytest.mark.parametrize("kind,expected", [("images", "blurry"), ("edit", "blurry"), ("vector", None), ("music", None)])
def test_only_images_and_edits_have_a_negative_prompt(kind, expected):
    assert answer_of({"reply": "x", "draft": {"prompt": "fine", "negative": "blurry"}}, kind).draft.negative == expected


@pytest.mark.parametrize("draft", [None, "text", [], {}, {"prompt": ""}, {"prompt": "   "}, {"prompt": 5}, {"negative": "only a negative"}])
def test_a_draft_without_a_prompt_is_no_draft(draft):
    assert answer_of({"reply": "x", "draft": draft}).draft is None


# ------------------------------------------------------------------ settings, checked as a run is
def test_the_example_of_the_spec_guidance_that_is_out_of_range_is_left_out_with_the_words():
    settings, notices = check_settings("images", {"guidance": 40, "shape": "16:9"}, LIMITS)
    assert settings == {"shape": "16:9"} and notices == ["The builder suggested guidance 40; the studio allows 0.1 to 20, so it was left out."]


@pytest.mark.parametrize("value", [0.1, 1, 4.5, 20])
def test_guidance_inside_the_range_is_kept(value):
    assert check_settings("images", {"guidance": value}, LIMITS) == ({"guidance": float(value)}, [])


@pytest.mark.parametrize("value", [0, 0.05, 20.5, -1, "high", None, True, [4], float("nan")])
def test_guidance_outside_it_or_not_a_number_is_left_out(value):
    settings, notices = check_settings("images", {"guidance": value}, LIMITS)
    assert settings == {} and len(notices) == 1 and notices[0].startswith("The builder suggested guidance ") and notices[0].endswith("so it was left out.")


def test_steps_are_whole_numbers_within_the_studios_range():
    assert check_settings("images", {"steps": 12}, LIMITS) == ({"steps": 12}, [])
    assert check_settings("images", {"steps": 12.0}, LIMITS) == ({"steps": 12}, [])
    for bad in (0, 101, 12.5, "twelve"):
        assert check_settings("images", {"steps": bad}, LIMITS)[0] == {}


def test_a_shape_must_be_a_real_preset_and_auto_is_for_edits_only():
    assert check_settings("images", {"shape": "9:16"}, LIMITS)[0] == {"shape": "9:16"}
    settings, notices = check_settings("images", {"shape": "21:9"}, LIMITS)
    assert settings == {} and notices == ["The builder suggested shape 21:9; the studio's shapes are 1:1, 4:3, 3:4, 3:2, 2:3, 16:9, 9:16, so it was left out."]
    assert check_settings("images", {"shape": "auto"}, LIMITS)[0] == {} and check_settings("edit", {"shape": "auto"}, LIMITS)[0] == {"shape": "auto"}


def test_a_vector_kind_colours_and_sizes_are_checked_like_the_form():
    assert check_settings("vector", {"kind": "line", "colours": 4, "width_mm": 100, "height_mm": 80.5}, LIMITS) == ({"kind": "line", "colours": 4, "width_mm": 100.0, "height_mm": 80.5}, [])
    settings, notices = check_settings("vector", {"kind": "watercolour", "colours": 9, "width_mm": 4, "height_mm": 1201}, LIMITS)
    assert settings == {} and len(notices) == 4
    assert notices[0] == "The builder suggested kind watercolour; the kinds are silhouette, flat, line, so it was left out."
    assert notices[1] == "The builder suggested colours 9; the studio allows 2 to 8, so it was left out."
    assert check_settings("vector", {"colours": 3.5}, LIMITS)[0] == {}  # a whole number


def test_a_palette_is_hex_inks_and_one_fewer_than_the_colours_because_white_counts():
    assert check_settings("vector", {"colours": 4, "palette": ["#ff0000", "#00ff00", "#0000ff"]}, LIMITS) == ({"colours": 4, "palette": ["#FF0000", "#00FF00", "#0000FF"]}, [])
    settings, notices = check_settings("vector", {"colours": 3, "palette": ["#FF0000", "#00FF00", "#0000FF"]}, LIMITS)
    assert settings == {"colours": 3} and "3 inks for 3 colours" in notices[0] and "at most 2" in notices[0]
    assert check_settings("vector", {"colours": 4, "palette": ["red"]}, LIMITS)[0] == {"colours": 4}
    assert check_settings("vector", {"colours": 4, "palette": "#FF0000"}, LIMITS)[0] == {"colours": 4}
    assert check_settings("vector", {"palette": ["#FF0000"] * 7}, LIMITS)[0] == {"palette": ["#FF0000"] * 7}  # no colours said: the most the form allows is 8, so 7 inks


def test_music_fields_instrumental_lyrics_and_seconds():
    got, notices = check_settings("music", {"fields": {"genre": "jazz", "bpm": 92, "key": "  D minor ", "voice": "", "bogus": "x", "mood": ["a"]}, "instrumental": False,
                                           "lyrics": "[Verse]\nla la", "seconds": 90}, LIMITS)
    assert got == {"fields": {"genre": "jazz", "bpm": "92", "key": "D minor"}, "instrumental": False, "lyrics": "[Verse]\nla la", "seconds": 90} and notices == []
    for bad in (9, 301, 60.5, "long"):
        assert "seconds" not in check_settings("music", {"seconds": bad}, LIMITS)[0]
    assert "instrumental" not in check_settings("music", {"instrumental": "yes"}, LIMITS)[0]
    assert "fields" not in check_settings("music", {"fields": {"bogus": "x"}}, LIMITS)[0] and "fields" not in check_settings("music", {"fields": "jazz"}, LIMITS)[0]
    _, notices = check_settings("music", {"lyrics": "l" * 6001}, LIMITS)
    assert notices == ["The builder wrote lyrics of 6001 characters; the studio allows up to 6000, so they were left out."]


def test_the_longest_music_the_studio_allows_is_the_settings_not_a_fixed_number():
    assert check_settings("music", {"seconds": 200}, Limits(500, 12, 120))[0] == {} and check_settings("music", {"seconds": 120}, Limits(500, 12, 120))[0] == {"seconds": 120}


@pytest.mark.parametrize("kind,key", [("images", "colours"), ("vector", "guidance"), ("music", "shape"), ("edit", "palette")])
def test_a_setting_from_another_kind_is_ignored_without_a_word(kind, key):
    assert check_settings(kind, {key: 3}, LIMITS) == ({}, [])


@pytest.mark.parametrize("raw", [None, "x", [], 5])
def test_settings_that_are_not_an_object_are_nothing(raw):
    assert check_settings("images", raw, LIMITS) == ({}, [])


def test_the_draft_carries_only_the_settings_that_passed():
    answer = answer_of({"reply": "x", "draft": {"prompt": "p", "settings": {"shape": "16:9", "guidance": 40, "steps": 0}}})
    assert answer.draft.settings == {"shape": "16:9"} and len(answer.notices) == 2


# ------------------------------------------------------------------ the wordings
def test_wordings_are_read_only_when_they_were_asked_for():
    data = {"reply": "x", "variations": [{"label": "simpler", "prompt": "p1"}, {"label": "cinematic", "prompt": "p2", "negative": "n"}, {"label": "detailed", "prompt": "p3"}]}
    assert answer_of(data).variations == []
    answer = answer_of(data, want_variations=True)
    assert [(v.label, v.prompt, v.negative) for v in answer.variations] == [("simpler", "p1", None), ("cinematic", "p2", "n"), ("detailed", "p3", None)] and answer.notices == []


def test_fewer_than_three_usable_wordings_is_said():
    data = {"reply": "x", "variations": [{"label": "simpler", "prompt": "p1"}, {"label": "", "prompt": "p2"}, {"label": "no prompt"}, {"label": "long", "prompt": "q" * 501}]}
    answer = answer_of(data, want_variations=True)
    assert [v.label for v in answer.variations] == ["simpler"]
    assert "The builder gave 1 usable wording and not 3." in answer.notices and any("a wording of 501 characters" in n for n in answer.notices)


def test_more_than_three_wordings_keeps_three():
    data = {"reply": "x", "variations": [{"label": f"w{n}", "prompt": f"p{n}"} for n in range(5)]}
    assert [v.label for v in answer_of(data, want_variations=True).variations] == ["w0", "w1", "w2"]


# ------------------------------------------------------------------ things to remember, tags, done
def test_at_most_two_new_preferences_and_not_one_the_person_already_has():
    answer = answer_of({"reply": "x", "remember": ["always 16:9", "Natural light", "no text", "x" * 400, ""]}, known_preferences=("NATURAL LIGHT",))
    assert answer.remember == ["always 16:9", "no text"]
    assert answer_of({"reply": "x", "remember": ["a", "a", "A"]}).remember == ["a"]
    assert answer_of({"reply": "x", "remember": "not a list"}).remember == []


def test_tags_are_normalised_once_each_and_at_most_twelve():
    answer = answer_of({"reply": "x", "tags": ["Sea", "sea", " STORM ", "a,b", "", None, 5, *[f"t{n}" for n in range(20)]]})
    assert answer.tags[:3] == ["sea", "storm", "a b"] and len(answer.tags) == 12


@pytest.mark.parametrize("raw,expected", [("Sea", "sea"), ("  two   words ", "two words"), ("a,b", "a b"), ("", None), ("   ", None), (5, None), (None, None),
                                         ("x" * 40, "x" * 30), ("tab\there", "tab here"), ("ÉCOLE", "école")])
def test_a_tag_as_it_is_stored(raw, expected):
    assert normalize_tag(raw) == expected


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), ("true", False), (1, False), (None, False)])
def test_done_means_the_boolean_true_and_nothing_else(value, expected):
    assert answer_of({"reply": "x", "done": value}).done is expected


# ------------------------------------------------------------------ streaming the reply out of an answer still being written
@pytest.mark.parametrize("buffer,expected", [
    ('', None), ('{', None), ('{"rep', None), ('{"reply"', None), ('{"reply":', None), ('{"reply": "', ""), ('{"reply": "Hel', "Hel"),
    ('{"reply": "Hello world", "title": "x"}', "Hello world"), ('{"reply":"say \\"hi\\" now"', 'say "hi" now'), ('{"reply":"a\\nb"', "a\nb"),
    ('{"reply":"back\\\\slash"', "back\\slash"), ('{"reply":"half \\', "half "), ('{"reply":"caf\\u00e9"', "café"), ('{"reply":"caf\\u00', "caf"),
    ('{"reply":"x\\ud83d\\ude00y', "x😀y"), ('{"reply":"x\\ud83d', "x"), ('{"reply":"x\\ud83d\\u00', "x"), ('  {  "reply"  :  "spaced', "spaced"),
    ('{"title":"t","reply":"late', None), ('{"questions":[],"reply":"late', None), ('not json at all', None), ('{"reply":"a\x00b', "ab"),
])
def test_the_reply_as_far_as_it_has_arrived(buffer, expected):
    assert reply_so_far(buffer) == expected


def test_the_reply_only_ever_grows_as_the_answer_arrives():
    whole = json.dumps({"reply": "Café \"quoted\" \n line two 😀 end", "questions": [question()]})
    seen = ""
    for i in range(len(whole) + 1):
        now = reply_so_far(whole[:i])
        if now is None:
            continue
        assert now.startswith(seen), (i, seen, now)
        seen = now
    assert seen == 'Café "quoted" \n line two 😀 end'


# ------------------------------------------------------------------ what mutation checks showed the tests above did not pin down
def test_the_numbers_an_answer_is_checked_against_cannot_be_changed_by_what_reads_it():
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        LIMITS.prompt_chars = 1


def test_an_answer_that_says_nothing_about_being_done_is_not_done():
    from studio.builder.answer import Answer

    assert Answer().done is False and Answer().plain is False and Answer().draft is None
    assert Answer().to_content()["draft"] is None and Answer().to_content()["done"] is False


def test_the_message_as_stored_carries_the_draft_exactly():
    answer = answer_of({"reply": "x", "draft": {"prompt": "A pier", "negative": "blur", "settings": {"shape": "3:2"}, "note": "first"}})
    assert answer.to_content()["draft"] == {"prompt": "A pier", "negative": "blur", "settings": {"shape": "3:2"}, "note": "first"}
    assert answer_of({"reply": "x"}).to_content()["draft"] is None


def test_a_plain_text_reply_is_shortened_only_past_the_limit():
    assert plain_answer("y" * REPLY_CHARS).notices == [] and plain_answer("y" * REPLY_CHARS).reply == "y" * REPLY_CHARS
    longer = plain_answer("y" * (REPLY_CHARS + 1))
    assert longer.reply == "y" * REPLY_CHARS + "…" and longer.notices == [f"The reply was shortened to {REPLY_CHARS} characters."]


def test_text_is_cut_only_when_it_is_longer_than_the_limit():
    assert clean("abcde", 5) == "abcde" and clean("abcdef", 5) == "abcde" and clean("abcd ef", 5) == "abcd"


def test_the_object_is_found_even_when_it_is_the_first_thing_and_something_follows_it():
    assert read_answer('{"reply": "ok"} and thanks', "images", LIMITS).reply == "ok"
    assert read_answer('{"reply": "ok"}\n\nLet me know!', "images", LIMITS).reply == "ok"


def test_what_is_not_an_answer_is_said_in_words():
    with pytest.raises(BadAnswer, match="The answer is not JSON."):
        read_answer("just some words", "images", LIMITS)
    with pytest.raises(BadAnswer, match="The answer is not an object."):
        read_answer("[1, 2]", "images", LIMITS)


def test_questions_with_no_ids_are_numbered_from_one_in_order():
    answer = answer_of({"reply": "x", "questions": [{"text": "First?", "chips": ["a", "b", "c"]}, {"text": "Second?", "chips": ["a", "b", "c"]}]})
    assert [q.id for q in answer.questions] == ["q1", "q2"]


def test_inks_that_are_not_colours_or_are_too_many_are_left_out_with_the_reason():
    bad = answer_of({"reply": "x", "draft": {"prompt": "A fox", "settings": {"colours": 3, "palette": ["red", "#00AA00"]}}}, kind="vector")
    assert bad.notices == ["The builder suggested inks that are not #RRGGBB colours, so they were left out."]
    assert "palette" not in bad.draft.settings
    many = answer_of({"reply": "x", "draft": {"prompt": "A fox", "settings": {"colours": 3, "palette": ["#FF0000", "#00AA00", "#0000FF"]}}}, kind="vector")
    assert many.notices == ["The builder suggested 3 inks for 3 colours; white is the bare material and counts, so at most 2 are allowed and the inks were left out."]
    good = answer_of({"reply": "x", "draft": {"prompt": "A fox", "settings": {"colours": 3, "palette": ["#ff0000", " #00aa00 "]}}}, kind="vector")
    assert good.notices == [] and good.draft.settings["palette"] == ["#FF0000", "#00AA00"]


def test_lyrics_are_kept_up_to_exactly_the_studios_limit():
    from studio import presets as P

    at_limit = answer_of({"reply": "x", "draft": {"prompt": "A song", "settings": {"lyrics": "l" * P.MUSIC_LYRICS_MAX}}}, kind="music")
    assert at_limit.notices == [] and len(at_limit.draft.settings["lyrics"]) == P.MUSIC_LYRICS_MAX
    over = answer_of({"reply": "x", "draft": {"prompt": "A song", "settings": {"lyrics": "l" * (P.MUSIC_LYRICS_MAX + 1)}}}, kind="music")
    assert "lyrics" not in over.draft.settings and len(over.notices) == 1 and "lyrics" in over.notices[0]


# ------------------------------------------------------------------ the reply as it is being written
def test_an_escape_that_has_arrived_whole_is_shown_and_one_that_has_not_waits():
    assert reply_so_far('{"reply": "caf\\u00e9') == "café"       # the last six characters are the whole escape: nothing to wait for
    assert reply_so_far('{"reply": "caf\\u00e') == "caf"         # one digit short: wait for it
    assert reply_so_far('{"reply": "caf\\u00') == "caf" and reply_so_far('{"reply": "caf\\') == "caf"
    assert reply_so_far('{"reply": "a\\nb') == "a\nb" and reply_so_far('{"reply": "q\\"x') == 'q"x'


def test_a_pair_of_escapes_is_one_character_and_a_lone_half_is_not_shown_as_one():
    assert reply_so_far('{"reply": "\\ud83d\\ude00') == "😀"       # the pair is complete exactly at the end
    assert reply_so_far('{"reply": "\\ud83d\\ude0') == ""          # the second half has not all arrived: wait, show nothing of the pair
    assert reply_so_far('{"reply": "\\ud83d') == ""
    assert reply_so_far('{"reply": "\\ud800\\udc00') == "\U00010000" and reply_so_far('{"reply": "\\udbff\\udfff') == "\U0010ffff"   # the ends of the two ranges
    assert reply_so_far('{"reply": "\\udc00 x') == "� x"          # a lone second half is not a character
    assert reply_so_far('{"reply": "\\ud800 and more') == "� and more"   # nor is a first half with nothing to pair with
    assert reply_so_far('{"reply": "\\ud800 x') == ""                    # (but with too little after it to tell, it waits)
    assert reply_so_far('{"reply": "\\ue000x') == "x"        # the character after the surrogates is an ordinary one
    assert reply_so_far('{"reply": "\\ud7ffx') == "퟿x"        # and so is the one before them
