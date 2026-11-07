"""The Prompt builder's store (DESIGN.md §37.3, §37.4; criteria 229, 238, 245-247, 251): chats and prompts, the word index, the bin, the counts, and what
deleting a project or a prompt does and does not take with it."""

from __future__ import annotations

import json
import sqlite3

import pytest
from test_db import run_row

from studio.builder.store import BuilderStore, StoreError
from studio.db import Database

T0 = "2026-11-01T10:00:00.000Z"
T1 = "2026-11-02T10:00:00.000Z"
T2 = "2026-11-03T10:00:00.000Z"
T3 = "2026-11-04T10:00:00.000Z"
PROJECT = "a" * 32
OTHER = "b" * 32


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "studio.sqlite")
    yield database
    database.close()


@pytest.fixture
def store(db):
    return BuilderStore(db)


def find(db: Database, text: str) -> list[str]:
    """The prompts the word index finds for a word, as the search will ask it."""
    return sorted(r["prompt_id"] for r in db.read_all("SELECT prompt_id FROM prompt_search WHERE prompt_search MATCH ?", (text,)))


def count(db: Database, table: str, where: str = "1", args: tuple = ()) -> int:
    return db.read_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", args)["n"]


# ------------------------------------------------------------------ chats
def test_messages_are_numbered_from_one_in_order_and_the_chat_notes_the_time(store):
    chat = store.create_chat(now=T0, title="A lighthouse")
    assert [store.add_message(chat, now=T1, role="user", text="hi"), store.add_message(chat, now=T2, role="assistant", text="hello", content={"questions": []}, model="m")] == [1, 2]
    messages = store.messages(chat)
    assert [(m["seq"], m["role"], m["text"], m["model"]) for m in messages] == [(1, "user", "hi", None), (2, "assistant", "hello", "m")]
    assert json.loads(messages[1]["content"]) == {"questions": []} and messages[0]["content"] is None
    store.add_message(chat, now=T2, role="assistant", text="again", content={"b": 1, "a": 2})
    assert store.messages(chat)[2]["content"] == '{"a": 2, "b": 1}'  # one way of writing it, whatever order it was built in
    assert store.get_chat(chat)["created_at"] == T0 and store.get_chat(chat)["updated_at"] == T2


def test_a_message_for_a_chat_that_is_not_there_is_none_and_numbers_do_not_cross_chats(store):
    assert store.add_message("f" * 32, now=T0, role="user", text="x") is None
    one, two = store.create_chat(now=T0, title="one"), store.create_chat(now=T0, title="two")
    assert [store.add_message(one, now=T0, role="user", text="a"), store.add_message(two, now=T0, role="user", text="b"), store.add_message(one, now=T0, role="user", text="c")] == [1, 1, 2]


def test_values_the_schema_does_not_hold_are_refused_not_stored(store, db):
    with pytest.raises(StoreError, match="for 'paintings' is not one of images, vector, music, edit"):
        store.create_chat(now=T0, title="x", for_kind="paintings")
    with pytest.raises(StoreError):
        store.create_chat(now=T0, title="x", detail="huge")
    chat = store.create_chat(now=T0, title="x")
    with pytest.raises(StoreError):
        store.add_message(chat, now=T0, role="system", text="x")
    with pytest.raises(StoreError):
        store.create_prompt(now=T0, title="x", text="y", source="magic")
    # and the tables themselves say no, whoever asks
    with pytest.raises(sqlite3.IntegrityError), db.tx() as c:
        c.execute("INSERT INTO prompts (id, title, for_kind, created_at, updated_at) VALUES ('p', 't', 'sculptures', 'x', 'x')")
    assert count(db, "chat_messages") == 0 and count(db, "prompts") == 0


# ------------------------------------------------------------------ prompts and their versions
def test_a_prompt_starts_with_version_one_and_its_tags_once_each(store):
    prompt = store.create_prompt(now=T0, title="Lighthouse", text="a lighthouse at dusk", negative="blurry", settings={"steps": 30, "aspect": "3:2"}, note="first",
                                 source="builder", for_kind="images", project_id=PROJECT, tags=["sea", "dusk", "sea"], favourite=True)
    row = store.get_prompt(prompt)
    assert (row["title"], row["for_kind"], row["project_id"], row["favourite"], row["deleted_at"], row["restored_at"]) == ("Lighthouse", "images", PROJECT, 1, None, None)
    (version,) = store.versions(prompt)
    assert (version["n"], version["text"], version["negative"], version["note"], version["source"]) == (1, "a lighthouse at dusk", "blurry", "first", "builder")
    assert json.loads(version["settings"]) == {"steps": 30, "aspect": "3:2"} and version["settings"] == '{"aspect": "3:2", "steps": 30}'  # one way of writing it
    assert store.tags(prompt) == ["dusk", "sea"]


def test_a_version_number_is_used_once_so_a_version_cannot_be_written_over(store, db):
    prompt = store.create_prompt(now=T0, title="t", text="first")
    with pytest.raises(sqlite3.IntegrityError), db.tx() as c:
        c.execute("INSERT INTO prompt_versions (id, prompt_id, n, text, settings, source, created_at) VALUES ('v', ?, 1, 'second', '{}', 'hand', ?)", (prompt, T1))
    assert [v["text"] for v in store.versions(prompt)] == ["first"]
    with pytest.raises(sqlite3.IntegrityError), db.tx() as c:  # nor one for a prompt that is not there
        c.execute("INSERT INTO prompt_versions (id, prompt_id, n, text, settings, source, created_at) VALUES ('v', ?, 1, 'x', '{}', 'hand', ?)", ("e" * 32, T1))


def test_the_versions_of_a_prompt_are_listed_oldest_first(store, db):
    prompt = store.create_prompt(now=T0, title="t", text="one")
    with db.tx() as c:
        for n, text in ((3, "three"), (2, "two")):  # written out of order, to be read back in order
            c.execute("INSERT INTO prompt_versions (id, prompt_id, n, text, settings, source, created_at) VALUES (?, ?, ?, ?, '{}', 'hand', ?)", (f"v{n}", prompt, n, text, T1))
    assert [(v["n"], v["text"]) for v in store.versions(prompt)] == [(1, "one"), (2, "two"), (3, "three")]


# ------------------------------------------------------------------ the word index
def test_the_word_index_holds_the_title_the_newest_text_and_the_tags(store, db):
    fox = store.create_prompt(now=T0, title="Winter fox", text="a red fox in deep snow", tags=["animals", "snow"])
    store.create_prompt(now=T0, title="Lighthouse", text="a lighthouse at dusk", tags=["sea"])
    assert find(db, "fox") == [fox] and find(db, "snow") == [fox] and find(db, "animals") == [fox] and find(db, "sea") != [fox]
    assert find(db, "winter") == [fox] and find(db, "fo*") == [fox]  # a title word, and a prefix as someone typing would have it
    assert find(db, "ZEBRA") == []
    cafe = store.create_prompt(now=T0, title="Night market", text="a café at midnight, Zürich")
    assert find(db, "cafe") == [cafe] and find(db, "café") == [cafe] and find(db, "zurich") == [cafe]  # accents do not matter, in the words or in the search
    assert find(db, "CAFÉ") == [cafe]  # nor does case


def test_the_index_follows_the_newest_version_and_not_an_older_one(store, db):
    prompt = store.create_prompt(now=T0, title="t", text="a heron on a pier")
    with db.tx() as c:
        c.execute("INSERT INTO prompt_versions (id, prompt_id, n, text, settings, source, created_at) VALUES ('v2', ?, 2, 'an owl in a barn', '{}', 'hand', ?)", (prompt, T1))
        store.reindex(c, prompt)
    assert find(db, "owl") == [prompt] and find(db, "heron") == []
    assert count(db, "prompt_search") == 1  # still one row for the prompt


def test_the_index_is_rebuilt_at_start_up_only_when_it_is_not_in_step(store, db):
    ids = [store.create_prompt(now=T0, title=f"p{n}", text=f"word{n}") for n in range(3)]
    assert store.ensure_search_in_step() is False
    with db.tx() as c:
        c.execute("DELETE FROM prompt_search WHERE prompt_id=?", (ids[1],))
    assert find(db, "word1") == [] and store.ensure_search_in_step() is True
    assert find(db, "word1") == [ids[1]] and count(db, "prompt_search") == 3 and store.ensure_search_in_step() is False
    assert store.reindex_all() == 3


def test_an_index_with_a_row_too_many_or_for_a_prompt_that_is_gone_is_rebuilt_too(store, db):
    ids = [store.create_prompt(now=T0, title=f"p{n}", text=f"word{n}") for n in range(2)]
    with db.tx() as c:  # a row for a prompt that does not exist: the counts now differ the other way round
        c.execute("INSERT INTO prompt_search (prompt_id, title, text, tags) VALUES ('ghost', 'ghost', 'phantom', '')")
    assert find(db, "phantom") == ["ghost"] and store.ensure_search_in_step() is True
    assert find(db, "phantom") == [] and count(db, "prompt_search") == 2 and store.ensure_search_in_step() is False
    with db.tx() as c:  # same number of rows, but one is for a ghost and one prompt is missing: only a full rebuild puts it right
        c.execute("DELETE FROM prompt_search WHERE prompt_id=?", (ids[0],))
        c.execute("INSERT INTO prompt_search (prompt_id, title, text, tags) VALUES ('ghost', 'ghost', 'phantom', '')")
    assert store.reindex_all() == 2
    assert find(db, "phantom") == [] and find(db, "word0") == [ids[0]] and count(db, "prompt_search") == 2  # the rebuild starts from nothing


# ------------------------------------------------------------------ the bin
def test_a_deleted_prompt_goes_to_the_bin_with_its_versions_and_comes_back_with_a_fresh_clock(store, db):
    prompt = store.create_prompt(now=T0, title="Fox", text="a fox", tags=["animals"])
    chat = store.create_chat(now=T0, title="about the fox", prompt_id=prompt)
    assert store.bin_item("prompt", prompt, T1) == "binned"
    assert store.bin_item("prompt", prompt, T2) == "binned"  # again: the first date counts
    row = store.get_prompt(prompt)
    assert row["deleted_at"] == T1 and len(store.versions(prompt)) == 1 and store.tags(prompt) == ["animals"]  # nothing taken from it
    assert store.get_chat(chat)["deleted_at"] is None and store.get_chat(chat)["prompt_id"] == prompt  # its chat is its own thing
    assert store.restore_item("prompt", prompt, T3) == "restored"
    row = store.get_prompt(prompt)
    assert row["deleted_at"] is None and row["restored_at"] == T3
    assert store.restore_item("prompt", prompt, T3) == "not_in_bin"


def test_a_chat_goes_to_the_bin_and_comes_back_the_same_way(store):
    chat = store.create_chat(now=T0, title="c")
    store.add_message(chat, now=T0, role="user", text="hi")
    assert store.bin_item("chat", chat, T1) == "binned" and store.get_chat(chat)["deleted_at"] == T1 and len(store.messages(chat)) == 1
    assert store.restore_item("chat", chat, T2) == "restored" and store.get_chat(chat)["restored_at"] == T2
    assert store.restore_item("chat", chat, T2) == "not_in_bin"


@pytest.mark.parametrize("kind", ["prompt", "chat"])
def test_what_is_not_there_is_not_found(store, kind):
    for action in (lambda: store.bin_item(kind, "9" * 32, T0), lambda: store.restore_item(kind, "9" * 32, T0), lambda: store.delete_item(kind, "9" * 32)):
        assert action() == "not_found"


def test_delete_forever_takes_the_prompt_and_what_hangs_off_it_and_nothing_else(store, db):
    prompt = store.create_prompt(now=T0, title="Fox", text="a fox", tags=["a", "b"])
    other = store.create_prompt(now=T0, title="Owl", text="an owl", tags=["a"])
    chat = store.create_chat(now=T0, title="about it", prompt_id=prompt)
    store.add_message(chat, now=T0, role="user", text="hello")
    assert db.insert_run_if_capacity(run_row(1), cap=10)
    with db.tx() as c:
        c.execute("UPDATE runs SET prompt_id=?, prompt_version=1 WHERE id=?", (prompt, run_row(1)["id"]))
        c.execute("UPDATE prompt_versions SET chat_id=?, message_seq=1 WHERE prompt_id=?", (chat, prompt))
    assert store.delete_item("prompt", prompt) == "deleted"
    assert store.get_prompt(prompt) is None and store.versions(prompt) == [] and store.tags(prompt) == [] and find(db, "fox") == []
    assert store.get_prompt(other) is not None and store.tags(other) == ["a"] and find(db, "owl") == [other]
    assert store.get_chat(chat)["prompt_id"] is None and len(store.messages(chat)) == 1  # the chat keeps its messages and is no longer linked (rule 1)
    run = db.get_run(run_row(1)["id"])
    assert run["prompt_id"] == prompt and run["prompt_version"] == 1  # the run keeps saying where it came from; the card says "deleted"


def test_delete_forever_of_a_chat_takes_its_messages_and_attachments_but_not_the_versions_that_came_from_it(store, db):
    prompt = store.create_prompt(now=T0, title="t", text="x", chat_id="c" * 32, message_seq=2)
    chat = store.create_chat(now=T0, title="c")
    store.add_message(chat, now=T0, role="user", text="hi")
    with db.tx() as c:
        c.execute("INSERT INTO chat_attachments (id, chat_id, message_seq, path, width, height, name) VALUES ('a', ?, 1, 'builder/x/1.png', 10, 10, 'n.png')", (chat,))
    assert store.delete_item("chat", chat) == "deleted"
    assert (count(db, "chat_messages"), count(db, "chat_attachments"), count(db, "chats")) == (0, 0, 0)
    (version,) = store.versions(prompt)
    assert version["chat_id"] == "c" * 32 and version["message_seq"] == 2  # a version remembers where it came from, whether or not that chat is still there


def test_the_clean_up_deletes_what_went_in_before_the_cutoff_and_the_rest_stays(store, db):
    old_prompt, new_prompt, kept_prompt = (store.create_prompt(now=T0, title=n, text=n) for n in ("old", "new", "kept"))
    old_chat, new_chat, kept_chat = (store.create_chat(now=T0, title=n) for n in ("old", "new", "kept"))
    store.bin_item("prompt", old_prompt, T0), store.bin_item("prompt", new_prompt, T2), store.bin_item("chat", old_chat, T0), store.bin_item("chat", new_chat, T2)
    assert store.purge(T1, 100) == {"prompt": [old_prompt], "chat": [old_chat]}
    assert store.get_prompt(old_prompt) is None and store.get_chat(old_chat) is None and find(db, "old") == []
    assert store.get_prompt(new_prompt)["deleted_at"] == T2 and store.get_chat(new_chat)["deleted_at"] == T2  # still in the bin, days left
    assert store.get_prompt(kept_prompt) is not None and store.get_chat(kept_chat) is not None  # never in the bin: never touched
    assert store.purge(T2, 100) == {"prompt": [], "chat": []}  # the cutoff is "before", not "at"


def test_empty_bin_takes_all_of_it_and_the_limit_is_for_each_kind(store):
    prompts = [store.create_prompt(now=T0, title=f"p{n}", text="x") for n in range(3)]
    chats = [store.create_chat(now=T0, title=f"c{n}") for n in range(3)]
    keep = store.create_prompt(now=T0, title="keep", text="x")
    for n, (prompt, chat) in enumerate(zip(prompts, chats)):
        store.bin_item("prompt", prompt, f"2026-11-0{n + 1}T00:00:00.000Z"), store.bin_item("chat", chat, f"2026-11-0{n + 1}T00:00:00.000Z")
    first = store.purge(None, 2)
    assert first == {"prompt": prompts[:2], "chat": chats[:2]}  # the oldest first
    assert store.purge(None, 100) == {"prompt": prompts[2:], "chat": chats[2:]}
    assert store.get_prompt(keep) is not None and store.purge(None, 100) == {"prompt": [], "chat": []}


# ------------------------------------------------------------------ counts
def test_the_counts_for_the_tab_and_its_filters(store):
    a = store.create_prompt(now=T0, title="a", text="x", tags=["sea", "dusk"], project_id=PROJECT, favourite=True)
    b = store.create_prompt(now=T0, title="b", text="x", tags=["sea"], project_id=PROJECT)
    c = store.create_prompt(now=T0, title="c", text="x", tags=["sea"], project_id=OTHER, favourite=True)
    d = store.create_prompt(now=T0, title="d", text="x")
    gone = store.create_prompt(now=T0, title="gone", text="x", tags=["sea", "rare"], project_id=PROJECT, favourite=True)
    store.bin_item("prompt", gone, T1)
    store.create_chat(now=T0, title="1", project_id=PROJECT), store.create_chat(now=T0, title="2")
    binned = store.create_chat(now=T0, title="3", project_id=PROJECT)
    store.bin_item("chat", binned, T1)
    store.bin_item("chat", store.create_chat(now=T0, title="4"), T1)  # in no project, and in the bin: counted only under deleted
    assert (a, b, c, d) and store.counts() == {
        "prompts": {"all": 4, "favourites": 2, "deleted": 1, "no_project": 1, "projects": {PROJECT: 2, OTHER: 1}, "tags": {"dusk": 1, "sea": 3}},  # `rare` and the bin: not counted
        "chats": {"all": 2, "deleted": 2, "no_project": 1, "projects": {PROJECT: 1}},
    }


def test_the_counts_of_an_empty_studio_are_zeros_and_empty_maps(store):
    assert store.counts() == {"prompts": {"all": 0, "favourites": 0, "deleted": 0, "no_project": 0, "projects": {}, "tags": {}},
                              "chats": {"all": 0, "deleted": 0, "no_project": 0, "projects": {}}}


# ------------------------------------------------------------------ projects (DESIGN.md §37.3 rule 5; criterion 247)
def test_the_projects_list_counts_prompts_and_chats_that_are_on_show(store, db):
    assert db.create_project(PROJECT, "Logo", T0) == "created" and db.create_project(OTHER, "Pitch", T0) == "created"
    store.create_prompt(now=T0, title="a", text="x", project_id=PROJECT), store.create_prompt(now=T0, title="b", text="x", project_id=PROJECT)
    gone = store.create_prompt(now=T0, title="c", text="x", project_id=PROJECT)
    store.bin_item("prompt", gone, T1)
    store.create_chat(now=T0, title="1", project_id=PROJECT)
    counts = {p["name"]: p["counts"] for p in db.list_projects()}
    assert counts["Logo"] == {"image": 0, "music": 0, "vector": 0, "prompts": 2, "chats": 1}  # the one in the bin is not on show
    assert counts["Pitch"] == {"image": 0, "music": 0, "vector": 0, "prompts": 0, "chats": 0}


def test_deleting_a_project_deletes_no_prompt_or_chat_and_turns_its_preferences_off_but_keeps_them(store, db):
    assert db.create_project(PROJECT, "Logo", T0) == "created" and db.create_project(OTHER, "Pitch", T0) == "created"
    prompt = store.create_prompt(now=T0, title="in", text="x", project_id=PROJECT)
    binned = store.create_prompt(now=T0, title="binned", text="x", project_id=PROJECT)
    store.bin_item("prompt", binned, T1)
    elsewhere = store.create_prompt(now=T0, title="elsewhere", text="x", project_id=OTHER)
    chat, chat_elsewhere = store.create_chat(now=T0, title="c", project_id=PROJECT), store.create_chat(now=T0, title="d", project_id=OTHER)
    assert db.insert_run_if_capacity(run_row(1), cap=10) and db.file_run(run_row(1)["id"], PROJECT) == "filed"
    with db.tx() as c:
        for pid, text, project, enabled in (("p1", "flat style", PROJECT, 1), ("p2", "pastel", OTHER, 1), ("p3", "always British spelling", None, 1)):
            c.execute("INSERT INTO preferences (id, text, project_id, enabled, source, position, created_at) VALUES (?, ?, ?, ?, 'hand', 0, ?)", (pid, text, project, enabled, T0))
    assert db.delete_project(PROJECT) == [run_row(1)["id"]]
    assert [store.get_prompt(p)["project_id"] for p in (prompt, binned)] == [None, None]  # in the bin or not, none is left pointing at a project that is gone
    assert store.get_prompt(binned)["deleted_at"] == T1 and store.get_prompt(elsewhere)["project_id"] == OTHER
    assert store.get_chat(chat)["project_id"] is None and store.get_chat(chat_elsewhere)["project_id"] == OTHER
    prefs = {r["id"]: (r["enabled"], r["project_id"]) for r in db.read_all("SELECT id, enabled, project_id FROM preferences")}
    assert prefs == {"p1": (0, PROJECT), "p2": (1, OTHER), "p3": (1, None)}  # off and still the project's: it never starts applying everywhere
    assert db.get_run(run_row(1)["id"])["project_id"] is None  # and the runs as before


# ------------------------------------------------------------------ start-up
def test_the_app_has_the_store_and_rebuilds_an_index_that_is_behind_when_it_starts(client_factory):
    from conftest import close

    first = client_factory()
    store = first.app.state.builder_store
    assert isinstance(store, BuilderStore) and store.db is first.app.state.db
    prompt = store.create_prompt(now=T0, title="Heron", text="a heron on a pier")
    with first.app.state.db.tx() as c:  # as if the index had been made after the prompt, or lost
        c.execute("DELETE FROM prompt_search")
    assert find(first.app.state.db, "heron") == []
    close(first)
    second = client_factory()  # the same data folder
    assert find(second.app.state.db, "heron") == [prompt]
