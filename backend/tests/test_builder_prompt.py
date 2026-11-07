"""What the model is told and sent (DESIGN.md section 37.5; criteria 225, 232-234): the instruction files, and the conversation trimmed to fit."""

from __future__ import annotations

import re

import pytest

from studio.builder import CHIP_CHARS, CHIPS_MAX, CHIPS_MIN, QUESTIONS_MAX
from studio.builder.context import PICTURE_TOKENS, Turn, build_messages, estimate_tokens, render_assistant, render_user, reply_room
from studio.builder.instructions import DETAIL_TARGETS, DIRECTORY, FILES, INTENTS, Example, fill, pictures_note, render_draft, system_prompt, values
from studio.builder.schema import DETAILS, FOR_KINDS
from studio.config import Settings

SETTINGS = Settings.from_env({})


# ------------------------------------------------------------------ the files
def test_there_is_a_file_for_the_common_part_and_one_for_each_kind_and_nothing_else_in_the_folder():
    assert set(FILES) == {"common", "images", "vector", "music", "edit"}
    assert {p.name for p in DIRECTORY.iterdir()} == {"common.md", "images.md", "vector.md", "music.md", "edit.md"}


@pytest.mark.parametrize("name", sorted(FILES))
def test_each_file_says_it_is_a_first_draft_for_the_owner_and_is_not_empty(name):
    text = (DIRECTORY / FILES[name]).read_text(encoding="utf-8")
    assert text.startswith("<!-- FIRST DRAFT for the owner's review") and len(text) > 800


@pytest.mark.parametrize("name", sorted(FILES))
def test_every_word_in_double_braces_is_one_the_studio_fills_in(name):
    text = (DIRECTORY / FILES[name]).read_text(encoding="utf-8")
    used = set(re.findall(r"\{\{([a-z_]+)\}\}", text))
    assert used <= set(values(SETTINGS)), used - set(values(SETTINGS))
    assert "{{" not in fill(text, values(SETTINGS))


def test_a_word_the_studio_does_not_know_is_an_error_and_never_reaches_the_model():
    with pytest.raises(KeyError, match="shapse"):
        fill("one of {{shapse}}", values(SETTINGS))


def test_the_numbers_come_from_the_studios_settings():
    assert values(Settings.from_env({"STUDIO_MAX_PROMPT_CHARS": "1234", "STUDIO_MUSIC_MAX_SECONDS": "120"}))["prompt_chars"] == "1234"
    got = values(Settings.from_env({"STUDIO_MUSIC_MAX_SECONDS": "120"}))
    assert got["seconds_max"] == "120" and got["seconds_min"] == "10" and got["shapes"] == "1:1, 4:3, 3:4, 3:2, 2:3, 16:9, 9:16"
    assert (got["questions_max"], got["chips_min"], got["chips_max"], got["chip_chars"]) == (str(QUESTIONS_MAX), str(CHIPS_MIN), str(CHIPS_MAX), str(CHIP_CHARS))
    assert got["colours_min"] == "2" and got["colours_max"] == "8" and got["vector_kinds"] == "silhouette, flat, line" and got["guidance_min"] == "0.1" and got["guidance_max"] == "20"


# ------------------------------------------------------------------ what each file must say (the rules of section 37.5)
def read(kind: str) -> str:
    return system_prompt(kind, SETTINGS)


def test_the_common_part_has_the_rules_of_how_to_help():
    text = read("images")
    for needle in ("at most 3 questions", "3 to 5 chips", "at most 60 characters", "Write it now", "Surprise me", "Skip", "the whole current draft", "done to true",
                   "at most two standing preferences", "English unless", "no markdown", "JSON object and nothing else", 'write "reply" FIRST', "8000 characters"):
        assert needle in text, needle


def test_the_images_part_has_what_qwen_likes():
    text = read("images")
    for needle in ("long, descriptive, natural-language", "double quotes", "only has an effect when guidance is above 1", "1:1, 4:3, 3:4, 3:2, 2:3, 16:9, 9:16", "0.1 to 20", 'Leave "steps" out'):
        assert needle in text, needle


def test_the_vector_part_has_what_a_laser_needs():
    text = read("vector")
    for needle in ("flat colours, hard edges", "No gradients", "silhouette, flat, line", "white is the bare material and counts", "from 2 to 8", "at most one fewer than", "#RRGGBB",
                   "5 to 1200", "There is no negative prompt"):
        assert needle in text, needle


def test_the_music_part_has_the_fields_and_the_section_tags():
    text = read("music")
    for needle in ('"genre"', '"mood"', '"bpm"', '"key"', '"instruments"', '"voice"', "[Intro] [Verse] [Pre-Chorus] [Chorus] [Post-Chorus] [Bridge] [Solo] [Instrumental] [Outro]", "on its own line",
                   "from 10 to 300", "There is no negative prompt"):
        assert needle in text, needle


def test_the_edit_part_refers_to_pictures_by_number_and_knows_when_it_cannot_see():
    text = read("edit")
    for needle in ('"image 1"', '"image 2"', "keep everything else the same", "If you are told you cannot", '"auto"', "above 1"):
        assert needle in text, needle


def test_the_owners_notes_at_the_top_of_a_file_are_not_sent_to_the_model():
    assert "FIRST DRAFT" not in read("images") and "<!--" not in read("images")


# ------------------------------------------------------------------ the whole instruction
@pytest.mark.parametrize("kind", FOR_KINDS)
def test_the_instruction_for_a_kind_is_the_common_part_then_the_kind_then_this_conversation_then_the_draft(kind):
    text = system_prompt(kind, SETTINGS)
    positions = [text.index(marker) for marker in ("# The prompt builder: how to help", f"# This prompt is for", "## This conversation", "## The current draft")]
    assert positions == sorted(positions) and f"- This prompt is for: {kind}." in text and text.endswith("There is no draft yet.")
    assert len(text) < 9000  # about 3000 tokens: it leaves room in a 32768 window for a long chat


@pytest.mark.parametrize("detail", DETAILS)
def test_the_detail_level_says_how_long_the_finished_prompt_should_be(detail):
    assert f"- Detail level: {DETAIL_TARGETS[detail]}" in system_prompt("images", SETTINGS, detail=detail)
    assert DETAIL_TARGETS["short"].startswith("Short: about one or two sentences") and "paragraph" in DETAIL_TARGETS["normal"] and "several paragraphs" in DETAIL_TARGETS["rich"]


def test_a_kind_or_detail_the_studio_does_not_have_is_a_bug():
    with pytest.raises(ValueError):
        system_prompt("sculptures", SETTINGS)
    with pytest.raises(ValueError):
        system_prompt("images", SETTINGS, detail="huge")


def test_the_preferences_are_told_and_only_when_there_are_some():
    assert "## The person's standing preferences" not in system_prompt("images", SETTINGS)
    text = system_prompt("images", SETTINGS, preferences=["photographic, natural light", "no text in the picture"])
    assert "## The person's standing preferences" in text and "- photographic, natural light\n- no text in the picture" in text and "do not ask what they already answer" in text


def test_the_examples_are_shown_as_taste_not_to_copy_and_cut_short():
    assert "saved prompts" not in system_prompt("images", SETTINGS)
    text = system_prompt("images", SETTINGS, examples=[Example("Harbour", "images", "a harbour at dusk"), Example("Long", "vector", "w" * 700)])
    assert '### "Harbour" (for images)\n\na harbour at dusk' in text and "do not copy them" in text and ("w" * 600 + "…") in text and ("w" * 601) not in text


def test_the_draft_is_shown_with_its_negative_prompt_and_settings():
    assert render_draft(None) == "There is no draft yet." and render_draft({}) == "There is no draft yet." and render_draft({"prompt": "  "}) == "There is no draft yet."
    assert render_draft({"prompt": " a fox "}) == "Prompt:\na fox"
    assert render_draft({"prompt": "a fox", "negative": "blur", "settings": {"shape": "1:1", "guidance": 4}}) == 'Prompt:\na fox\nNegative prompt:\nblur\nSettings:\n{"guidance": 4, "shape": "1:1"}'
    assert system_prompt("images", SETTINGS, draft={"prompt": "a fox"}).endswith("## The current draft\n\nPrompt:\na fox")


def test_pictures_and_a_result_being_discussed_are_told_when_there_are_some():
    assert "attached" not in system_prompt("images", SETTINGS) and "## The result being discussed" not in system_prompt("images", SETTINGS)
    text = system_prompt("images", SETTINGS, pictures="The person attached 2 pictures.", run_note="The picture was made with: steps 40.")
    assert "- The person attached 2 pictures." in text and "## The result being discussed\n\nThe picture was made with: steps 40." in text


def test_what_the_model_is_told_about_pictures_depends_on_whether_it_can_see():
    assert pictures_note(0, [], True) == ""
    assert pictures_note(1, ["a.png"], True) == "The person attached 1 picture, numbered in the order given. You can see them: write from what is really in them."
    unseen = pictures_note(2, ["a.png", "b.png"], False)
    assert 'attached 2 pictures ("a.png", "b.png")' in unseen and "you cannot see pictures" in unseen and "say so in your reply" in unseen
    assert pictures_note(3, [], False).startswith("The person attached 3 pictures, but you cannot see")


def test_each_button_has_words_for_the_model():
    assert set(INTENTS) == {"message", "write_now", "variations", "flesh_out", "refine", "describe", "help_edit"} and INTENTS["message"] == ""
    assert "Ask nothing more" in INTENTS["write_now"] and "exactly three different takes" in INTENTS["variations"] and "Do not change the draft" in INTENTS["variations"]
    assert all(INTENTS[name] for name in INTENTS if name != "message")


# ------------------------------------------------------------------ the person's turn, the model's turn
def test_the_persons_turn_is_their_words_then_their_answers_in_words():
    turn = Turn("user", "  with a boat ", {"answers": [
        {"id": "q1", "question": "When?", "mode": "chips", "chips": ["Dusk", "Dawn"]},
        {"id": "q2", "question": "Style?", "mode": "surprise", "chips": []},
        {"id": "q3", "question": "Colours?", "mode": "skip", "chips": []},
        {"id": "q4", "question": "Empty?", "mode": "chips", "chips": []}]})
    assert render_user(turn) == ('with a boat\nMy answers to your questions:\n- "When?": Dusk; Dawn\n- "Style?": Surprise me (you choose, and say what you chose).\n'
                                 '- "Colours?": Skip (leave it out, and do not ask it again).')
    assert render_user(Turn("user", "just words")) == "just words" and render_user(Turn("user", "")) == "(no words)"
    assert render_user(Turn("user", "", {"answers": [{"id": "q1", "mode": "chips", "chips": ["Dusk"]}]})) == 'My answers to your questions:\n- "q1": Dusk'


def test_the_models_turn_is_sent_back_as_what_it_said_without_the_draft_or_the_notices():
    turn = Turn("assistant", "Lovely.", {"reply": "Lovely.", "questions": [{"id": "q1", "text": "When?", "chips": ["Dusk", "Dawn", "Noon"]}], "draft": {"prompt": "SECRET DRAFT"},
                                          "notices": ["dropped something"], "done": False, "tags": ["x"], "context": {"preferences": 2}})
    assert render_assistant(turn) == '{"reply": "Lovely.", "questions": [{"id": "q1", "text": "When?", "chips": ["Dusk", "Dawn", "Noon"]}]}'
    assert render_assistant(Turn("assistant", "Done.", {"reply": "Done.", "questions": [], "done": True})) == '{"reply": "Done.", "done": true}'
    assert render_assistant(Turn("assistant", "plain words", {"plain": True, "reply": "plain words"})) == "plain words"
    assert render_assistant(Turn("assistant", "old message with no structure", None)) == "old message with no structure"


# ------------------------------------------------------------------ fitting the window
def turns(n: int, size: int = 300) -> list[Turn]:
    out = []
    for i in range(n):
        out.append(Turn("user", f"user {i} " + "w" * size))
        out.append(Turn("assistant", f"assistant {i} " + "w" * size, {"reply": f"assistant {i} " + "w" * size}))
    return out


def test_a_chat_that_fits_is_sent_whole_with_the_instruction_first_and_the_newest_turn_last():
    built = build_messages("SYSTEM", [*turns(2), Turn("user", "newest")], intent_line="", num_ctx=32768)
    assert [m["role"] for m in built.messages] == ["system", "user", "assistant", "user", "assistant", "user"] and built.messages[0]["content"] == "SYSTEM"
    assert built.messages[-1]["content"] == "newest" and built.dropped == 0 and built.over is False and built.tokens > 0


def test_what_the_person_just_did_is_said_after_their_words_in_brackets():
    built = build_messages("S", [Turn("user", "idea")], intent_line="Do the thing.", num_ctx=32768)
    assert built.messages[-1]["content"] == "idea\n\n[From the studio: Do the thing.]"
    assert build_messages("S", [Turn("user", "idea")], intent_line="", num_ctx=32768).messages[-1]["content"] == "idea"


def test_a_reply_answers_the_persons_turn_so_the_last_turn_must_be_theirs():
    for bad in ([], [Turn("assistant", "x")]):
        with pytest.raises(ValueError):
            build_messages("S", bad, intent_line="", num_ctx=32768)


def test_when_it_does_not_fit_the_oldest_turns_go_first_and_the_newest_and_the_instruction_stay():
    history = turns(10)
    num_ctx = 4096
    built = build_messages("SYSTEM" * 100, [*history, Turn("user", "newest")], intent_line="", num_ctx=num_ctx)
    assert 0 < built.dropped < len(history) and built.messages[0]["content"] == "SYSTEM" * 100 and built.messages[-1]["content"] == "newest"
    kept = built.messages[1:-1]
    assert kept and kept[0]["role"] == "user" and [m["role"] for m in kept] == ["user", "assistant"] * (len(kept) // 2)
    # what is kept is the NEWEST run of turns, unbroken, so the model never sees a gap
    numbers = [int(re.search(r"(?:user|assistant) (\d+)", m["content"]).group(1)) for m in kept]
    assert numbers == sorted(numbers) and max(numbers) == 9 and min(numbers) > 0
    assert built.tokens <= num_ctx - reply_room(num_ctx)


def test_the_model_is_never_sent_a_chat_that_begins_with_its_own_words():
    history = turns(3)
    for num_ctx in range(1500, 4000, 100):
        built = build_messages("S" * 300, [*history, Turn("user", "newest")], intent_line="", num_ctx=num_ctx)
        if len(built.messages) > 2:
            assert built.messages[1]["role"] == "user", num_ctx


def test_the_newest_turn_and_the_instruction_are_sent_even_when_they_alone_do_not_fit():
    built = build_messages("S" * 20000, [Turn("user", "idea " * 500)], intent_line="", num_ctx=4096)
    assert built.over is True and built.dropped == 0 and len(built.messages) == 2
    assert build_messages("S", [Turn("user", "idea")], intent_line="", num_ctx=4096).over is False


def test_a_picture_costs_room_and_goes_with_its_turn():
    with_picture = build_messages("S", [Turn("user", "old", images=["AAAA"]), Turn("assistant", "ok", {"reply": "ok"}), Turn("user", "new", images=["BBBB"])], intent_line="", num_ctx=32768)
    assert with_picture.messages[1]["images"] == ["AAAA"] and with_picture.messages[-1]["images"] == ["BBBB"] and "images" not in with_picture.messages[2]
    plain = build_messages("S", [Turn("user", "old"), Turn("assistant", "ok", {"reply": "ok"}), Turn("user", "new")], intent_line="", num_ctx=32768)
    assert with_picture.tokens - plain.tokens == 2 * PICTURE_TOKENS
    squeezed = build_messages("S", [Turn("user", "old", images=["A"] * 5), Turn("assistant", "ok", {"reply": "ok"}), Turn("user", "new")], intent_line="", num_ctx=4096)
    assert squeezed.dropped == 2  # five pictures do not fit beside the instruction: the turn that carried them goes


def test_room_is_kept_for_the_reply_a_quarter_of_the_window_and_at_least_2048():
    assert reply_room(32768) == 8192 and reply_room(16384) == 4096 and reply_room(4096) == 2048 and reply_room(512) == 2048


def test_the_estimate_errs_on_the_side_of_dropping():
    assert estimate_tokens("") == 0 and estimate_tokens("a") == 1 and estimate_tokens("abc") == 1 and estimate_tokens("abcd") == 2 and estimate_tokens("x" * 300) == 100
