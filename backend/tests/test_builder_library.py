"""The prompt library (DESIGN.md section 37.3 rules 1-5, 7; criteria 229, 230, 245-247): versions that are only added, tags, search, the pictures made from a prompt,
comparing, and the standing preferences."""

from __future__ import annotations

import json

import pytest
from test_db import run_row

import studio.db as db_module
from studio.builder import PREFERENCES_MAX
from studio.builder.diff import changed, settings_diff, word_diff
from studio.builder.library import UNSET, Library, search_terms
from studio.builder.store import BuilderStore, StoreError
from studio.db import Database

T0, T1, T2, T3 = ("2026-11-0%dT10:00:00.000Z" % n for n in (1, 2, 3, 4))
PROJECT, OTHER = "a" * 32, "b" * 32


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


@pytest.fixture
def store(db):
    return BuilderStore(db)


@pytest.fixture
def lib(store):
    return Library(store)


@pytest.fixture
def plain_lib(tmp_path, monkeypatch):
    """A library on a SQLite that has no FTS5, so that search falls back to a plain match."""
    monkeypatch.setattr(db_module, "PROMPT_SEARCH_SQL", "CREATE VIRTUAL TABLE IF NOT EXISTS prompt_search USING no_such_module(prompt_id)")
    database = Database(tmp_path / "plain.sqlite")
    assert database.has_fts5 is False
    yield Library(BuilderStore(database))
    database.close()


# ------------------------------------------------------------------ the pure comparison
def test_the_words_that_changed_are_marked_removed_then_added_in_reading_order():
    assert word_diff("a lighthouse at dusk", "a lighthouse at dawn in fog") == [{"op": "same", "text": "a lighthouse at"}, {"op": "del", "text": "dusk"}, {"op": "add", "text": "dawn in fog"}]
    assert word_diff("one two three", "one three") == [{"op": "same", "text": "one"}, {"op": "del", "text": "two"}, {"op": "same", "text": "three"}]
    assert word_diff("one three", "one two three") == [{"op": "same", "text": "one"}, {"op": "add", "text": "two"}, {"op": "same", "text": "three"}]


def test_identical_words_are_one_unchanged_run_and_white_space_is_not_a_change():
    assert word_diff("same words", "same   words") == [{"op": "same", "text": "same words"}] and word_diff("a\nb", "a b") == [{"op": "same", "text": "a b"}]
    assert word_diff("", "") == [] and word_diff(None, None) == [] and word_diff(None, "new") == [{"op": "add", "text": "new"}] and word_diff("old", None) == [{"op": "del", "text": "old"}]
    assert changed(word_diff("a", "a")) is False and changed(word_diff("a", "b")) is True and changed([]) is False


def test_each_setting_is_the_same_changed_added_or_removed():
    rows = settings_diff({"shape": "16:9", "guidance": 4, "steps": 8}, {"shape": "1:1", "guidance": 4.0, "seed_note": "x"})
    assert rows == [{"key": "guidance", "before": 4, "after": 4.0, "status": "same"}, {"key": "seed_note", "before": None, "after": "x", "status": "added"},
                    {"key": "shape", "before": "16:9", "after": "1:1", "status": "changed"}, {"key": "steps", "before": 8, "after": None, "status": "removed"}]
    assert settings_diff(None, None) == [] and settings_diff({"a": {"x": 1, "y": 2}}, {"a": {"y": 2, "x": 1}})[0]["status"] == "same"
    # the order of an object's keys is not a change, whichever version has them out of order
    assert settings_diff({"fields": {"mood": "warm", "genre": "jazz"}}, {"fields": {"genre": "jazz", "mood": "warm"}})[0]["status"] == "same"
    assert settings_diff({"fields": {"genre": "jazz", "mood": "warm"}}, {"fields": {"mood": "warm", "genre": "jazz"}})[0]["status"] == "same"


def test_a_long_text_keeps_its_unchanged_words_unchanged():
    """The comparison must not give up on words that are common in a long prompt: 'the' is on every other line, and a long prompt is the one worth comparing."""
    common = " ".join(["the"] * 150 + ["of"] * 150)
    runs = word_diff(common + " dusk", common + " dawn")
    assert runs == [{"op": "same", "text": common}, {"op": "del", "text": "dusk"}, {"op": "add", "text": "dawn"}]


# ------------------------------------------------------------------ versions are only ever added (rule 2, criterion 229)
def test_a_new_version_gets_the_next_number_and_the_older_ones_are_untouched(store, lib):
    prompt = store.create_prompt(now=T0, title="t", text="first", negative="blur", settings={"shape": "16:9"})
    before = [dict(v) for v in store.versions(prompt)]
    assert lib.add_version(prompt, now=T1, text="second", negative="blur", settings={"shape": "16:9"}, note="reworded", source="builder", chat_id="c" * 32, message_seq=4) == ("added", 2)
    assert lib.add_version(prompt, now=T2, text="third", source="wording") == ("added", 3)
    versions = store.versions(prompt)
    assert [v["n"] for v in versions] == [1, 2, 3] and [dict(v) for v in versions[:1]] == before  # the first is exactly as it was
    assert (versions[1]["note"], versions[1]["source"], versions[1]["chat_id"], versions[1]["message_seq"], versions[1]["created_at"]) == ("reworded", "builder", "c" * 32, 4, T1)
    assert store.get_prompt(prompt)["updated_at"] == T2


def test_saving_what_is_already_the_newest_makes_no_version(store, lib):
    prompt = store.create_prompt(now=T0, title="t", text="same", negative="blur", settings={"shape": "16:9", "guidance": 4})
    for args in ({"text": "same", "negative": "blur", "settings": {"guidance": 4, "shape": "16:9"}},  # the settings in another order
                 {"text": "same", "negative": " blur ", "settings": {"shape": "16:9", "guidance": 4}}):  # and a negative prompt with spaces round it
        assert lib.add_version(prompt, now=T1, **args) == ("unchanged", 1)
    assert len(store.versions(prompt)) == 1 and store.get_prompt(prompt)["updated_at"] == T0  # nothing made, nothing touched


@pytest.mark.parametrize("change", [{"text": "other"}, {"negative": "different"}, {"negative": None}, {"settings": {"shape": "1:1", "guidance": 4}}, {"settings": {"shape": "16:9"}}])
def test_a_change_to_the_words_the_negative_prompt_or_the_settings_is_a_new_version(store, lib, change):
    prompt = store.create_prompt(now=T0, title="t", text="same", negative="blur", settings={"shape": "16:9", "guidance": 4})
    args = {"text": "same", "negative": "blur", "settings": {"shape": "16:9", "guidance": 4}, **change}
    assert lib.add_version(prompt, now=T1, **args) == ("added", 2)


def test_an_empty_negative_prompt_is_none(store, lib):
    prompt = store.create_prompt(now=T0, title="t", text="x")
    assert lib.add_version(prompt, now=T1, text="y", negative="   ") == ("added", 2) and store.versions(prompt)[1]["negative"] is None
    assert lib.add_version(prompt, now=T2, text="y", negative="") == ("unchanged", 2)


def test_a_version_of_a_prompt_that_is_not_there_or_with_a_source_the_schema_does_not_have(store, lib):
    assert lib.add_version("9" * 32, now=T0, text="x") == ("not_found", None)
    prompt = store.create_prompt(now=T0, title="t", text="x")
    with pytest.raises(StoreError):
        lib.add_version(prompt, now=T1, text="y", source="magic")
    assert len(store.versions(prompt)) == 1


def test_make_this_the_current_one_copies_an_old_version_as_the_newest(store, lib):
    prompt = store.create_prompt(now=T0, title="t", text="one", negative="n1", settings={"shape": "1:1"})
    lib.add_version(prompt, now=T1, text="two", negative="n2", settings={"shape": "3:2"})
    assert lib.make_current(prompt, 1, now=T2) == ("added", 3)
    versions = store.versions(prompt)
    assert [v["n"] for v in versions] == [1, 2, 3] and (versions[2]["text"], versions[2]["negative"], json.loads(versions[2]["settings"])) == ("one", "n1", {"shape": "1:1"})
    assert versions[2]["note"] == "Made current again from version 1" and versions[0]["text"] == "one" and versions[1]["text"] == "two"  # history is not rewritten
    assert lib.make_current(prompt, 3, now=T3) == ("unchanged", 3)  # it is the newest already
    assert lib.make_current(prompt, 9, now=T3) == ("no_version", None) and lib.make_current("9" * 32, 1, now=T3) == ("not_found", None)


def test_the_index_follows_a_new_version(store, lib, db):
    prompt = store.create_prompt(now=T0, title="t", text="a heron on a pier")
    lib.add_version(prompt, now=T1, text="an owl in a barn")
    found = lambda w: [r["prompt_id"] for r in db.read_all("SELECT prompt_id FROM prompt_search WHERE prompt_search MATCH ?", (w,))]  # noqa: E731
    assert found("owl") == [prompt] and found("heron") == []


# ------------------------------------------------------------------ the prompt itself
def test_a_prompts_title_kind_star_and_tags_can_change_and_the_index_follows(store, lib, db):
    prompt = store.create_prompt(now=T0, title="Old", text="a fox", tags=["a", "b"])
    out = lib.update_prompt(prompt, now=T1, title="New title", for_kind="vector", favourite=True, tags=["fox", "snow", "fox"])
    row = store.get_prompt(prompt)
    assert out == {"project_changed": False, "unfiled_runs": 0} and (row["title"], row["for_kind"], row["favourite"], row["updated_at"]) == ("New title", "vector", 1, T1)
    assert store.tags(prompt) == ["fox", "snow"]
    assert [r["prompt_id"] for r in db.read_all("SELECT prompt_id FROM prompt_search WHERE prompt_search MATCH 'snow'")] == [prompt]
    assert db.read_all("SELECT prompt_id FROM prompt_search WHERE prompt_search MATCH 'new'")[0]["prompt_id"] == prompt
    lib.update_prompt(prompt, now=T2, favourite=False, tags=[])
    assert store.get_prompt(prompt)["favourite"] == 0 and store.tags(prompt) == [] and store.get_prompt(prompt)["title"] == "New title"  # only what was given


def test_only_what_is_given_changes_and_none_for_a_project_takes_it_out(store, lib):
    prompt = store.create_prompt(now=T0, title="t", text="x", project_id=PROJECT, tags=["keep"])
    lib.update_prompt(prompt, now=T1, title="u")
    row = store.get_prompt(prompt)
    assert (row["project_id"], store.tags(prompt)) == (PROJECT, ["keep"])
    assert lib.update_prompt(prompt, now=T2, project_id=None)["project_changed"] is True and store.get_prompt(prompt)["project_id"] is None
    assert lib.update_prompt("9" * 32, now=T2, title="x") is None
    with pytest.raises(StoreError):
        lib.update_prompt(prompt, now=T2, for_kind="sculptures")


def seed_run(db, n: int, prompt_id: str, version: int = 1, *, project=None, draft=False, binned=False, status="done"):
    row = run_row(n, status)
    row["options_json"] = json.dumps({"draft": draft})
    assert db.insert_run_if_capacity(row, cap=1000)
    with db.tx() as c:
        c.execute("UPDATE runs SET prompt_id=?, prompt_version=?, project_id=?, deleted_at=? WHERE id=?", (prompt_id, version, project, T1 if binned else None, row["id"]))
        c.execute("INSERT INTO images (id, run_id, kind, idx, seed, width, height, has_alpha, bytes, path, created_at) VALUES (?, ?, 'output', 0, 1, 8, 8, 0, 1, 'x.png', ?)",
                  (f"{n:032x}"[::-1], row["id"], T0))
    return row["id"]


def test_the_runs_filing_would_move_are_those_in_no_project_that_are_not_quick_tries_or_in_the_bin(store, lib, db):
    prompt = store.create_prompt(now=T0, title="t", text="x")
    plain = seed_run(db, 1, prompt)
    seed_run(db, 2, prompt, draft=True)  # a quick try
    seed_run(db, 3, prompt, binned=True)
    seed_run(db, 4, prompt, project=OTHER)  # already in a project
    other_prompt = store.create_prompt(now=T0, title="o", text="y")
    seed_run(db, 5, other_prompt)
    assert lib.unfiled_runs(prompt) == [plain]
    assert lib.update_prompt(prompt, now=T1, project_id=PROJECT) == {"project_changed": True, "unfiled_runs": 1}
    assert lib.update_prompt(prompt, now=T2, project_id=PROJECT)["project_changed"] is False  # the same project again: nothing to offer
    assert lib.update_prompt(prompt, now=T3, project_id=None) == {"project_changed": True, "unfiled_runs": 0}  # out of a project: nothing to file


# ------------------------------------------------------------------ the list: search, filters, sorts (rule 4, criterion 245)
def make_library(store, lib):
    ids = {}
    ids["fox"] = store.create_prompt(now=T0, title="Red fox", text="A red fox in deep snow at dawn", tags=["animals", "snow"], favourite=True)
    ids["light"] = store.create_prompt(now=T1, title="Lighthouse", text="A lighthouse in a storm, waves breaking", tags=["sea"], project_id=PROJECT)
    ids["owl"] = store.create_prompt(now=T2, title="alpha owl", text="An owl in a barn, warm light", tags=["animals"], project_id=PROJECT, favourite=True)
    ids["gone"] = store.create_prompt(now=T3, title="Deleted one", text="a lighthouse again", tags=["sea"])
    store.bin_item("prompt", ids["gone"], T3)
    return ids


def titles(rows) -> list[str]:
    return [r["title"] for r in rows]


@pytest.mark.parametrize("which", ["fts", "plain"])
def test_search_looks_in_the_title_the_newest_text_and_the_tags_and_each_word_starts_a_word(which, store, lib, plain_lib):
    use = lib if which == "fts" else plain_lib
    if which == "plain":
        store = use.store
    make_library(store, use)
    assert titles(use.list_prompts(q="lighthouse")) == ["Lighthouse"]  # the one in the bin is not shown
    assert titles(use.list_prompts(q="snow")) == ["Red fox"] and titles(use.list_prompts(q="ANIMALS", sort="title")) == ["alpha owl", "Red fox"]
    assert titles(use.list_prompts(q="light")) == ["alpha owl", "Lighthouse"]  # "light" starts a word in the owl's text and in the lighthouse's title
    assert titles(use.list_prompts(q="red snow")) == ["Red fox"] and use.list_prompts(q="red barn") == []  # every word must be there
    assert use.list_prompts(q="zebra") == [] and len(use.list_prompts(q="   ")) == 3 and len(use.list_prompts(q="%")) == 3  # nothing to look for is no search


def test_search_does_not_find_a_word_only_an_older_version_had(store, lib):
    prompt = store.create_prompt(now=T0, title="t", text="a heron on a pier")
    lib.add_version(prompt, now=T1, text="an owl in a barn")
    assert titles(lib.list_prompts(q="owl")) == ["t"] and lib.list_prompts(q="heron") == []


def test_search_text_cannot_break_the_query(lib, store):
    make_library(store, lib)
    for nasty in ('"', "a OR b", "*", "NEAR(", "'; DROP TABLE prompts; --", "title:fox", "(", "^fox", "fox*"):
        lib.list_prompts(q=nasty)  # no error
    assert titles(lib.list_prompts(q="title:fox")) == []  # it is words, not the index's own syntax
    assert titles(lib.list_prompts(q="fox*")) == ["Red fox"] and len(lib.list_prompts()) == 3


@pytest.mark.parametrize("kwargs,expected", [
    ({"favourite": True}, ["Red fox", "alpha owl"]), ({"tag": "animals"}, ["Red fox", "alpha owl"]), ({"tag": "sea"}, ["Lighthouse"]), ({"tag": "nothing"}, []),
    ({"project": PROJECT}, ["Lighthouse", "alpha owl"]), ({"project": "none"}, ["Red fox"]), ({"project": OTHER}, []),
    ({"deleted": True}, ["Deleted one"]), ({"tag": "animals", "favourite": True, "project": PROJECT}, ["alpha owl"]),
])
def test_the_filters_each_narrow_the_list(store, lib, kwargs, expected):
    make_library(store, lib)
    assert sorted(titles(lib.list_prompts(**kwargs))) == sorted(expected)


def test_the_list_is_sorted_by_what_changed_last_by_when_it_was_made_or_by_title(store, lib):
    ids = make_library(store, lib)
    lib.update_prompt(ids["fox"], now="2026-11-09T00:00:00.000Z", title="Red fox")  # touched last
    assert titles(lib.list_prompts(sort="updated")) == ["Red fox", "alpha owl", "Lighthouse"]
    assert titles(lib.list_prompts(sort="newest")) == ["alpha owl", "Lighthouse", "Red fox"]
    assert titles(lib.list_prompts(sort="title")) == ["alpha owl", "Lighthouse", "Red fox"]  # ignoring case
    with pytest.raises(StoreError):
        lib.list_prompts(sort="random")
    assert len(lib.list_prompts(limit=2)) == 2


def test_a_card_has_the_first_words_the_tags_the_version_and_the_latest_pictures(store, lib, db):
    prompt = store.create_prompt(now=T0, title="Fox", text="word " * 100, tags=["b", "a"], project_id=PROJECT, favourite=True)
    lib.add_version(prompt, now=T1, text="w" * 500)
    for n in range(1, 6):
        seed_run(db, n, prompt, 1 if n < 3 else 2)
    card = lib.list_prompts()[0]
    assert (card["id"], card["title"], card["for"], card["favourite"], card["tags"], card["project_id"], card["version"], card["versions"]) == (prompt, "Fox", "images", True, ["a", "b"], PROJECT, 2, 2)
    assert card["text"] == "w" * 240 and card["pictures"] == 5 and card["deleted_at"] is None
    assert [t["id"] for t in card["thumbs"]] == [f"{n:032x}"[::-1] for n in (5, 4, 3)]  # the latest three
    assert card["thumbs"][0]["url"] == f"/api/images/{card['thumbs'][0]['id']}/thumb"


def test_pictures_in_the_bin_are_not_shown_on_the_card(store, lib, db):
    prompt = store.create_prompt(now=T0, title="Fox", text="x")
    seed_run(db, 1, prompt, binned=True)
    visible = seed_run(db, 2, prompt)
    card = lib.list_prompts()[0]
    assert card["pictures"] == 1 and [t["id"] for t in card["thumbs"]] == [f"{2:032x}"[::-1]] and visible


def test_the_detail_lists_versions_newest_first_with_the_runs_made_from_each(store, lib, db):
    prompt = store.create_prompt(now=T0, title="Fox", text="one", settings={"shape": "16:9"})
    lib.add_version(prompt, now=T1, text="two", note="reworded")
    seed_run(db, 1, prompt, 1)
    seed_run(db, 2, prompt, 2, draft=True)
    seed_run(db, 3, prompt, 2)
    chat = store.create_chat(now=T0, title="about it", prompt_id=prompt)
    binned_chat = store.create_chat(now=T0, title="gone", prompt_id=prompt)
    store.bin_item("chat", binned_chat, T1)
    detail = lib.detail(prompt)
    assert [v["n"] for v in detail["versions"]] == [2, 1] and detail["text"] == "two" and detail["settings"] == {}
    two, one = detail["versions"]
    assert [r["run_id"] for r in two["runs"]] == [f"{3:032x}", f"{2:032x}"] and [r["draft"] for r in two["runs"]] == [False, True]
    assert [r["run_id"] for r in one["runs"]] == [f"{1:032x}"] and one["runs"][0]["images"][0]["thumb_url"].endswith("/thumb") and two["note"] == "reworded"
    assert one["settings"] == {"shape": "16:9"} and [c["id"] for c in detail["chats"]] == [chat]
    assert lib.detail("9" * 32) is None


# ------------------------------------------------------------------ comparing (criterion 230)
def test_two_versions_are_compared_in_text_negative_prompt_and_settings(store, lib):
    prompt = store.create_prompt(now=T0, title="t", text="a lighthouse at dusk", negative="blurry", settings={"shape": "16:9", "guidance": 4})
    lib.add_version(prompt, now=T1, text="a lighthouse at dawn", negative="blurry, dark", settings={"shape": "1:1", "guidance": 4})
    both = lib.compare(prompt, 1, 2)
    assert both["changed"] is True and both["a"]["n"] == 1 and both["b"]["n"] == 2
    assert both["text"] == [{"op": "same", "text": "a lighthouse at"}, {"op": "del", "text": "dusk"}, {"op": "add", "text": "dawn"}]
    assert both["negative"] == [{"op": "del", "text": "blurry"}, {"op": "add", "text": "blurry, dark"}]
    assert [(r["key"], r["status"]) for r in both["settings"]] == [("guidance", "same"), ("shape", "changed")]
    assert lib.compare(prompt, 1, 1)["changed"] is False and lib.compare(prompt, 1, 7) is None and lib.compare("9" * 32, 1, 2) is None


# ------------------------------------------------------------------ preferences (rule 7, criterion 233)
def test_a_note_is_added_in_order_and_not_twice(lib):
    kinds = [lib.add_preference(now=T0, text=text) for text in ("natural light", "no text in the picture", "NATURAL LIGHT")]
    assert [k[0] for k in kinds] == ["added", "added", "duplicate"] and kinds[2][1] == kinds[0][1]
    listed = lib.preferences()
    assert [p["text"] for p in listed] == ["natural light", "no text in the picture"] and [p["position"] for p in listed] == [1, 2]
    assert all(p["enabled"] and not p["orphaned"] and p["source"] == "hand" and p["project_id"] is None for p in listed)
    assert lib.add_preference(now=T0, text="natural light", project_id=PROJECT)[0] == "added"  # the same words for a project are another note


def test_there_is_a_most_the_studio_keeps(lib):
    for n in range(PREFERENCES_MAX):
        assert lib.add_preference(now=T0, text=f"note {n}")[0] == "added"
    assert lib.add_preference(now=T0, text="one more") == ("full", None)


def test_a_note_can_be_edited_switched_off_and_moved(lib):
    note = lib.add_preference(now=T0, text="natural light")[1]
    other = lib.add_preference(now=T0, text="flat style")[1]
    assert lib.update_preference(note, text="soft natural light") == "updated" and lib.update_preference(note, enabled=False) == "updated"
    assert lib.update_preference(note, project_id=PROJECT) == "updated"
    mine = next(p for p in lib.preferences() if p["id"] == note)
    assert (mine["text"], mine["enabled"], mine["project_id"]) == ("soft natural light", False, PROJECT)
    assert lib.update_preference(note, project_id=None) == "updated" and next(p for p in lib.preferences() if p["id"] == note)["project_id"] is None
    assert lib.update_preference(other, text="SOFT natural light") == "duplicate" and lib.update_preference("9" * 32, text="x") == "not_found"
    assert lib.update_preference(note, text="soft natural light") == "updated"  # its own words are not a clash with itself


def test_a_note_of_a_project_that_is_gone_is_listed_apart_and_cannot_be_switched_on_until_it_has_a_place(lib, db):
    assert db.create_project(PROJECT, "Logo", T0) == "created"
    note = lib.add_preference(now=T0, text="flat style for the logo", project_id=PROJECT)[1]
    assert next(p for p in lib.preferences() if p["id"] == note)["orphaned"] is False
    db.delete_project(PROJECT)
    gone = next(p for p in lib.preferences() if p["id"] == note)
    assert gone["orphaned"] is True and gone["enabled"] is False and gone["project_id"] == PROJECT  # off, and kept where it was
    assert lib.update_preference(note, enabled=True) == "no_project"
    assert lib.update_preference(note, enabled=True, project_id=None) == "updated"  # given a place again (everywhere)
    revived = next(p for p in lib.preferences() if p["id"] == note)
    assert revived["orphaned"] is False and revived["enabled"] is True and revived["project_id"] is None


def test_a_note_can_be_deleted(lib):
    note = lib.add_preference(now=T0, text="x")[1]
    assert lib.delete_preference(note) is True and lib.delete_preference(note) is False and lib.preferences() == []


def test_the_terms_of_a_search_are_the_words_typed_each_once(lib):
    assert search_terms("  A red, RED fox! ") == ["a", "red", "fox"] and search_terms("") == [] and search_terms("a b c d e f g h i j", limit=3) == ["a", "b", "c"]
    assert search_terms("café Zürich 42") == ["café", "zürich", "42"] and UNSET is not None
