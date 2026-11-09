"""The library through the API, and runs made from a prompt (DESIGN.md section 37; criteria 229, 236-238, 245-247, 251)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from builder_support import builder, new_chat, png_bytes, send  # noqa: F401 (fixtures)
from conftest import create_run, wait_for

from studio.serialize import format_ts

BASE = "/api/builder/prompts"


def make(client, **body) -> dict:
    payload = {"title": "Lighthouse", "text": "A lighthouse at dusk", **body}
    response = client.post(BASE, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def ago(days: float) -> str:
    return format_ts(datetime.now(timezone.utc) - timedelta(days=days))


def from_prompt(client, prompt: dict, version: int = 1, expect: int = 201, **options):
    body = {"mode": "generate", "prompt": prompt["text"], "options": {"width": 256, "height": 256, "steps": 3, **options}, "prompt_ref": {"prompt_id": prompt["id"], "version": version}}
    response = client.post("/api/runs", json=body)
    assert response.status_code == expect, response.text
    return response.json()


# ------------------------------------------------------------------ making one
def test_a_prompt_is_made_with_version_one_tags_and_a_project(builder):
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    prompt = make(builder, title="  A   lighthouse ", negative="blurry", settings={"shape": "3:2", "guidance": 4}, note="first", source="builder", tags=["Sea", " dusk ", "sea"],
                  project_id=project, favourite=True, **{"for": "images"})
    assert prompt["title"] == "A lighthouse" and prompt["for"] == "images" and prompt["tags"] == ["dusk", "sea"] and prompt["project_id"] == project and prompt["favourite"] is True
    assert prompt["version"] == 1 and prompt["versions"][0]["n"] == 1 and prompt["text"] == "A lighthouse at dusk" and prompt["negative"] == "blurry" and prompt["settings"] == {"shape": "3:2", "guidance": 4}
    assert prompt["versions"][0]["source"] == "builder" and prompt["versions"][0]["note"] == "first" and prompt["deleted_at"] is None and prompt["purge_at"] is None
    assert len(prompt["id"]) == 32


@pytest.mark.parametrize("body,code", [
    ({"text": "   "}, "empty_prompt"), ({"text": "x" * 8001}, "too_long"), ({"text": "x", "negative": "n" * 8001}, "too_long"),
    ({"settings": {"guidance": 40}}, "bad_settings"), ({"settings": {"shape": "21:9"}}, "bad_settings"), ({"settings": {"colours": 4}}, "bad_settings"),
    ({"title": "   "}, "bad_title"), ({"tags": [f"t{n}" for n in range(13)]}, "too_many_tags"), ({"project_id": "c" * 32}, "no_project"), ({"chat_id": "c" * 32}, "not_found"),
])
def test_a_prompt_that_breaks_a_rule_is_refused_in_words(builder, body, code):
    response = builder.post(BASE, json={"title": "t", "text": "a fox", **body})
    assert response.status_code in (404, 422) and response.json()["code"] == code


@pytest.mark.parametrize("body", [{"title": ""}, {"title": "t" * 121}, {"for": "sculptures"}, {"source": "magic"}, {"note": "n" * 301}, {"surprise": 1}, {"project_id": "nope"}, {"message_seq": 0}])
def test_a_malformed_prompt_is_a_422(builder, body):
    assert builder.post(BASE, json={"title": "t", "text": "a fox", **body}).status_code == 422


def test_a_prompt_for_vector_is_checked_by_the_vector_rules(builder):
    ok = make(builder, **{"for": "vector"}, settings={"kind": "flat", "colours": 3, "palette": ["#FF0000", "#00FF00"], "width_mm": 100, "height_mm": 80})
    assert ok["for"] == "vector" and ok["settings"]["palette"] == ["#FF0000", "#00FF00"]
    bad = builder.post(BASE, json={"title": "t", "text": "x", "for": "vector", "settings": {"colours": 3, "palette": ["#FF0000", "#00FF00", "#0000FF"]}})
    assert bad.status_code == 422 and "white is the bare material" in bad.json()["notices"][0]


def test_a_prompt_from_a_chat_remembers_the_chat_and_the_message(builder):
    chat = new_chat(builder)
    send(builder, chat, text="a lighthouse")
    prompt = make(builder, source="builder", chat_id=chat, message_seq=2)
    assert prompt["versions"][0]["chat_id"] == chat and prompt["versions"][0]["message_seq"] == 2


# ------------------------------------------------------------------ versions (criterion 229)
def test_save_as_a_new_version_adds_the_next_and_an_identical_one_makes_none(builder):
    prompt = make(builder, settings={"shape": "16:9"})
    url = f"{BASE}/{prompt['id']}/versions"
    same = builder.post(url, json={"text": "A lighthouse at dusk", "settings": {"shape": "16:9"}})
    assert same.status_code == 200 and same.json()["created"] is False and same.json()["message"] == "Nothing changed." and same.json()["version"] == 1
    new = builder.post(url, json={"text": "A lighthouse at dawn", "settings": {"shape": "16:9"}, "note": "moved the light", "source": "builder"})
    body = new.json()
    assert new.status_code == 201 and body["created"] is True and body["version"] == 2 and body["message"] == "Saved as version 2."
    assert [v["n"] for v in body["prompt"]["versions"]] == [2, 1] and body["prompt"]["text"] == "A lighthouse at dawn" and body["prompt"]["versions"][1]["text"] == "A lighthouse at dusk"
    assert builder.get(f"{BASE}/{prompt['id']}").json()["version"] == 2


def test_a_version_is_checked_like_a_prompt_and_by_the_kind_the_prompt_is_for(builder):
    prompt = make(builder, **{"for": "music"})
    url = f"{BASE}/{prompt['id']}/versions"
    assert builder.post(url, json={"text": " "}).json()["code"] == "empty_prompt"
    assert builder.post(url, json={"text": "x", "settings": {"shape": "16:9"}}).json()["code"] == "bad_settings"  # a music prompt has no shape
    assert builder.post(url, json={"text": "x", "settings": {"seconds": 60}}).status_code == 201
    assert builder.post(f"{BASE}/{'f' * 32}/versions", json={"text": "x"}).status_code == 404


def test_make_this_the_current_one_copies_it_as_a_new_latest(builder):
    prompt = make(builder)
    builder.post(f"{BASE}/{prompt['id']}/versions", json={"text": "second"})
    again = builder.post(f"{BASE}/{prompt['id']}/versions/1/restore")
    assert again.status_code == 201 and again.json()["version"] == 3 and again.json()["prompt"]["text"] == "A lighthouse at dusk"
    assert builder.post(f"{BASE}/{prompt['id']}/versions/3/restore").json()["message"] == "Nothing changed."
    assert builder.post(f"{BASE}/{prompt['id']}/versions/9/restore").json()["code"] == "no_version"
    assert builder.post(f"{BASE}/{'f' * 32}/versions/1/restore").status_code == 404


def test_two_versions_are_compared_with_the_words_that_changed(builder):
    prompt = make(builder, negative="blurry", settings={"shape": "16:9"})
    builder.post(f"{BASE}/{prompt['id']}/versions", json={"text": "A lighthouse at dawn", "negative": "blurry, dark", "settings": {"shape": "1:1"}})
    both = builder.get(f"{BASE}/{prompt['id']}/compare", params={"a": 1, "b": 2}).json()
    assert both["changed"] is True and {"op": "del", "text": "dusk"} in both["text"] and {"op": "add", "text": "dawn"} in both["text"]
    assert both["settings"] == [{"key": "shape", "before": "16:9", "after": "1:1", "status": "changed"}]
    assert builder.get(f"{BASE}/{prompt['id']}/compare", params={"a": 1, "b": 1}).json()["changed"] is False
    assert builder.get(f"{BASE}/{prompt['id']}/compare", params={"a": 1, "b": 7}).status_code == 404
    assert builder.get(f"{BASE}/{prompt['id']}/compare", params={"a": 0, "b": 1}).status_code == 422 and builder.get(f"{BASE}/{prompt['id']}/compare").status_code == 422


# ------------------------------------------------------------------ reading and changing
def test_the_list_filters_by_search_tag_star_project_and_bin(builder):
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    fox = make(builder, title="Red fox", text="a red fox in snow", tags=["animals"], favourite=True)
    light = make(builder, title="Lighthouse", text="a lighthouse in a storm", tags=["sea"], project_id=project)
    gone = make(builder, title="Deleted", text="a lighthouse again")
    builder.post(f"{BASE}/{gone['id']}/bin")
    ids = lambda **params: sorted(p["title"] for p in builder.get(BASE, params=params).json()["prompts"])  # noqa: E731
    assert ids() == ["Lighthouse", "Red fox"] and ids(q="light") == ["Lighthouse"] and ids(tag="Animals") == ["Red fox"] and ids(favourite="true") == ["Red fox"]
    assert ids(project=project) == ["Lighthouse"] and ids(project="none") == ["Red fox"] and ids(deleted="true") == ["Deleted"] and ids(q="fox", tag="sea") == []
    assert [p["title"] for p in builder.get(BASE, params={"sort": "title"}).json()["prompts"]] == ["Lighthouse", "Red fox"]
    assert builder.get(BASE, params={"sort": "random"}).status_code == 422 and builder.get(BASE, params={"project": "nope"}).status_code == 422
    assert fox and light


def test_a_prompt_can_be_renamed_starred_retagged_and_refiled(builder):
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    prompt = make(builder, tags=["a"])
    url = f"{BASE}/{prompt['id']}"
    changed = builder.patch(url, json={"title": " New  title ", "for": "edit", "favourite": True, "tags": ["B", "b", " c "], "project_id": project}).json()
    assert (changed["title"], changed["for"], changed["favourite"], changed["tags"], changed["project_id"]) == ("New title", "edit", True, ["b", "c"], project)
    assert changed["unfiled_runs"] == 0 and builder.patch(url, json={"project_id": None}).json()["project_id"] is None
    assert builder.patch(url, json={"title": "   "}).json()["code"] == "bad_title" and builder.patch(url, json={"project_id": "d" * 32}).json()["code"] == "no_project"
    assert builder.patch(url, json={"tags": [f"t{n}" for n in range(13)]}).json()["code"] == "too_many_tags"
    assert builder.patch(url, json={"title": ""}).status_code == 422 and builder.patch(f"{BASE}/{'f' * 32}", json={"title": "x"}).status_code == 404
    assert builder.get(url).json()["title"] == "New title"  # the refused changes changed nothing


def test_filing_a_prompt_later_offers_its_earlier_runs_and_moves_none_until_asked(builder):
    prompt = make(builder)
    runs = [from_prompt(builder, prompt) for _ in range(2)]
    quick = from_prompt(builder, prompt, draft=True)
    for run in (*runs, quick):
        wait_for(builder, run["id"])
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    filing = builder.patch(f"{BASE}/{prompt['id']}", json={"project_id": project}).json()
    assert filing["unfiled_runs"] == 2  # the quick try is not offered
    assert all(builder.get(f"/api/runs/{r['id']}").json()["project_id"] is None for r in runs)  # nothing was moved
    moved = builder.post(f"{BASE}/{prompt['id']}/file-runs", json={}).json()
    assert sorted(moved["filed"]) == sorted(r["id"] for r in runs) and moved["project_id"] == project
    filed = [builder.get(f"/api/runs/{r['id']}").json() for r in runs]
    assert all(r["project_id"] == project and r["pinned"] is True for r in filed)
    assert builder.get(f"/api/runs/{quick['id']}").json()["project_id"] is None
    assert builder.post(f"{BASE}/{prompt['id']}/file-runs", json={}).json()["filed"] == []


def test_filing_runs_needs_a_project(builder):
    prompt = make(builder)
    assert builder.post(f"{BASE}/{prompt['id']}/file-runs", json={}).json()["code"] == "no_project"
    assert builder.post(f"{BASE}/{prompt['id']}/file-runs", json={"project_id": "d" * 32}).json()["code"] == "no_project"


# ------------------------------------------------------------------ the bin (rule 1, criterion 246)
def test_delete_moves_a_prompt_to_the_bin_for_the_days_it_keeps_and_restore_brings_it_back(builder):
    prompt = make(builder)
    url = f"{BASE}/{prompt['id']}"
    binned = builder.post(f"{url}/bin").json()
    assert binned["deleted_at"] is not None and binned["purge_at"] is not None and builder.get(BASE).json()["prompts"] == []
    assert [p["id"] for p in builder.get(BASE, params={"deleted": "true"}).json()["prompts"]] == [prompt["id"]]
    assert builder.patch(url, json={"title": "x"}).json()["code"] == "prompt_in_bin" and builder.post(f"{url}/versions", json={"text": "y"}).json()["code"] == "prompt_in_bin"
    assert builder.get(url).json()["versions"][0]["text"] == "A lighthouse at dusk"  # a prompt in the bin can be read
    restored = builder.post(f"{url}/restore").json()
    assert restored["deleted_at"] is None and restored["purge_at"] is None and builder.post(f"{url}/restore").json()["code"] == "not_in_bin"
    assert builder.delete(url).json() == {"deleted": prompt["id"]} and builder.get(url).status_code == 404 and builder.delete(url).status_code == 404


def test_with_no_bin_delete_is_for_good_at_once(client_factory):
    client = client_factory(builder_llm="fake", bin_days=0)
    prompt, chat = make(client), new_chat(client)
    assert client.post(f"{BASE}/{prompt['id']}/bin").json() == {"deleted": prompt["id"]} and client.get(f"{BASE}/{prompt['id']}").status_code == 404
    client.post(f"/api/builder/chats/{chat}/attachments", content=png_bytes())
    assert client.post(f"/api/builder/chats/{chat}/bin").json() == {"deleted": chat}
    assert client.get(f"/api/builder/chats/{chat}").status_code == 404 and not (client.app.state.storage.root / "builder" / chat).exists()


def test_deleting_a_prompt_leaves_its_pictures_and_they_say_it_is_deleted(builder):
    prompt = make(builder, title="Lighthouse")
    run = from_prompt(builder, prompt)
    wait_for(builder, run["id"])
    assert builder.get(f"/api/runs/{run['id']}").json()["prompt_ref"] == {"id": prompt["id"], "title": "Lighthouse", "version": 1, "deleted": False, "edited": False}
    builder.post(f"{BASE}/{prompt['id']}/bin")
    after = builder.get(f"/api/runs/{run['id']}").json()
    assert after["prompt_ref"] == {"id": prompt["id"], "title": "Lighthouse", "version": 1, "deleted": True, "edited": False} and after["images"]  # untouched
    builder.delete(f"{BASE}/{prompt['id']}")
    gone = builder.get(f"/api/runs/{run['id']}").json()
    assert gone["prompt_ref"] == {"id": prompt["id"], "title": None, "version": 1, "deleted": True, "edited": False} and gone["images"]


def test_empty_bin_takes_the_prompts_and_chats_in_it_and_the_chats_pictures(builder):
    keep, binned = make(builder, title="keep"), make(builder, title="binned")
    chat, other = new_chat(builder), new_chat(builder)
    builder.post(f"/api/builder/chats/{chat}/attachments", content=png_bytes())
    builder.post(f"{BASE}/{binned['id']}/bin"), builder.post(f"/api/builder/chats/{chat}/bin")
    assert builder.post("/api/builder/bin/empty").json() == {"deleted": 2}
    assert [p["id"] for p in builder.get(BASE).json()["prompts"]] == [keep["id"]] and builder.get(f"/api/builder/chats/{chat}").status_code == 404
    assert builder.get(f"/api/builder/chats/{other}").status_code == 200 and not (builder.app.state.storage.root / "builder" / chat).exists()
    assert builder.post("/api/builder/bin/empty").json() == {"deleted": 0}


def test_the_daily_clean_up_takes_what_has_been_in_the_bin_long_enough(client_factory):
    client = client_factory(builder_llm="fake", quiet=True, bin_days=30)
    store = client.app.state.builder_store
    old, recent, never = make(client, title="old"), make(client, title="recent"), make(client, title="never")
    old_chat, recent_chat = new_chat(client), new_chat(client)
    client.post(f"/api/builder/chats/{old_chat}/attachments", content=png_bytes())
    store.bin_item("prompt", old["id"], ago(31)), store.bin_item("prompt", recent["id"], ago(5))
    store.bin_item("chat", old_chat, ago(40)), store.bin_item("chat", recent_chat, ago(1))
    client.portal.call(client.app.state.jobs.sweep_expired)
    assert client.get(f"{BASE}/{old['id']}").status_code == 404 and client.get(f"{BASE}/{recent['id']}").status_code == 200 and client.get(f"{BASE}/{never['id']}").status_code == 200
    assert client.get(f"/api/builder/chats/{old_chat}").status_code == 404 and client.get(f"/api/builder/chats/{recent_chat}").status_code == 200
    assert not (client.app.state.storage.root / "builder" / old_chat).exists()


# ------------------------------------------------------------------ preferences (criterion 233)
def test_notes_are_added_edited_switched_and_deleted(builder):
    note = builder.post("/api/builder/preferences", json={"text": "  natural   light "})
    assert note.status_code == 201 and note.json()["text"] == "natural light" and note.json()["enabled"] is True and note.json()["orphaned"] is False
    pid = note.json()["id"]
    assert builder.post("/api/builder/preferences", json={"text": "NATURAL LIGHT"}).json()["code"] == "duplicate"
    edited = builder.patch(f"/api/builder/preferences/{pid}", json={"text": "soft natural light", "enabled": False}).json()
    assert edited["text"] == "soft natural light" and edited["enabled"] is False
    assert [p["id"] for p in builder.get("/api/builder/preferences").json()["preferences"]] == [pid]
    assert builder.delete(f"/api/builder/preferences/{pid}").json() == {"deleted": pid} and builder.delete(f"/api/builder/preferences/{pid}").status_code == 404
    assert builder.patch(f"/api/builder/preferences/{pid}", json={"enabled": True}).status_code == 404


@pytest.mark.parametrize("body,status,code", [({"text": ""}, 422, None), ({"text": "x" * 601}, 422, None), ({"text": "x" * 301}, 422, "too_long"), ({"text": "ok", "project_id": "e" * 32}, 404, "no_project"),
                                              ({"text": "ok", "source": "magic"}, 422, None), ({"text": "ok", "surprise": 1}, 422, None)])
def test_a_note_that_breaks_a_rule_is_refused(builder, body, status, code):
    response = builder.post("/api/builder/preferences", json=body)
    assert response.status_code == status and (code is None or response.json()["code"] == code)


def test_a_note_for_a_project_goes_off_when_the_project_is_deleted_and_needs_a_place_to_come_back(builder):
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    note = builder.post("/api/builder/preferences", json={"text": "flat style", "project_id": project}).json()["id"]
    builder.delete(f"/api/projects/{project}")
    gone = next(p for p in builder.get("/api/builder/preferences").json()["preferences"] if p["id"] == note)
    assert gone["orphaned"] is True and gone["enabled"] is False
    assert builder.patch(f"/api/builder/preferences/{note}", json={"enabled": True}).json()["code"] == "no_project"
    assert builder.patch(f"/api/builder/preferences/{note}", json={"enabled": True, "project_id": None}).json()["orphaned"] is False


def test_a_suggestion_is_added_only_by_a_tap_and_remembers_it_came_from_one(builder):
    chat = new_chat(builder)
    send(builder, chat, text="idea")
    reply = send(builder, chat, text="more")
    assert reply[-1][1]["message"]["content"]["remember"] == ["photographic, natural light"]
    assert builder.get("/api/builder/preferences").json()["preferences"] == []  # nothing was added by the reply
    added = builder.post("/api/builder/preferences", json={"text": "photographic, natural light", "source": "suggestion"}).json()
    assert added["source"] == "suggestion"


# ------------------------------------------------------------------ counts and projects (criterion 247)
def test_the_counts_for_the_tab_and_the_filters(builder):
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    make(builder, tags=["sea"], project_id=project, favourite=True), make(builder, title="two", tags=["sea", "dusk"])
    gone = make(builder, title="three")
    builder.post(f"{BASE}/{gone['id']}/bin")
    new_chat(builder), builder.post("/api/builder/preferences", json={"text": "a"}), builder.post("/api/builder/preferences", json={"text": "b"})
    counts = builder.get("/api/builder/counts").json()
    assert counts["prompts"] == {"all": 2, "favourites": 1, "deleted": 1, "no_project": 1, "projects": {project: 1}, "tags": {"dusk": 1, "sea": 2}}
    assert counts["chats"]["all"] == 1 and counts["preferences"] == {"all": 2, "enabled": 2, "orphaned": 0}


def test_the_projects_list_counts_prompts_and_chats(builder):
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    make(builder, project_id=project), make(builder, title="b", project_id=project)
    new_chat(builder, project_id=project)
    counts = builder.get("/api/projects").json()["projects"][0]["counts"]
    assert counts["prompts"] == 2 and counts["chats"] == 1 and counts["image"] == 0


def test_deleting_a_project_deletes_no_prompt_or_chat(builder):
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    prompt, chat = make(builder, project_id=project), new_chat(builder, project_id=project)
    builder.delete(f"/api/projects/{project}")
    assert builder.get(f"{BASE}/{prompt['id']}").json()["project_id"] is None and builder.get(f"/api/builder/chats/{chat}").json()["chat"]["project_id"] is None


# ------------------------------------------------------------------ a run made from a prompt (rule 3 and 4, criteria 236-238)
def test_a_run_from_a_prompt_records_where_it_came_from_and_says_edited_when_the_words_differ(builder):
    prompt = make(builder, title="Lighthouse")
    run = from_prompt(builder, prompt)
    assert run["prompt_ref"] == {"id": prompt["id"], "title": "Lighthouse", "version": 1, "deleted": False, "edited": False}
    edited = builder.post("/api/runs", json={"prompt": "A lighthouse at dusk, but bigger", "options": {"width": 256, "height": 256, "steps": 3},
                                             "prompt_ref": {"prompt_id": prompt["id"], "version": 1}}).json()
    assert edited["prompt_ref"]["edited"] is True and edited["prompt"] == "A lighthouse at dusk, but bigger"  # the run keeps the text it used
    spaced = builder.post("/api/runs", json={"prompt": "A  lighthouse\nat dusk", "options": {"width": 256, "height": 256, "steps": 3}, "prompt_ref": {"prompt_id": prompt["id"], "version": 1}}).json()
    assert spaced["prompt_ref"]["edited"] is False  # white space is not an edit
    assert builder.get(f"/api/runs/{run['id']}").json()["prompt_ref"] == run["prompt_ref"]
    assert create_run(builder)["prompt_ref"] is None  # a run made any other way says nothing


def test_the_run_keeps_saying_which_version_even_after_the_prompt_grows(builder):
    prompt = make(builder)
    run = from_prompt(builder, prompt)
    builder.post(f"{BASE}/{prompt['id']}/versions", json={"text": "A lighthouse at dawn"})
    after = builder.get(f"/api/runs/{run['id']}").json()["prompt_ref"]
    assert after["version"] == 1 and after["edited"] is False  # judged against the version it was made from, not the newest


def test_a_run_from_a_prompt_in_a_project_is_filed_there_and_kept_but_a_quick_try_is_neither(builder):
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    prompt = make(builder, project_id=project)
    full = from_prompt(builder, prompt)
    quick = from_prompt(builder, prompt, draft=True)
    assert full["project_id"] == project and full["pinned"] is True and full["expires_at"] is None
    assert quick["project_id"] is None and quick["pinned"] is False and quick["prompt_ref"]["id"] == prompt["id"]  # linked, but throwaway
    assert builder.get("/api/projects").json()["projects"][0]["counts"]["image"] == 1


def test_a_run_from_a_prompt_in_no_project_is_not_filed_or_kept(builder):
    run = from_prompt(builder, make(builder))
    assert run["project_id"] is None and run["pinned"] is False


@pytest.mark.parametrize("body", [
    {"mode": "generate", "prompt": "a fox"}, {"mode": "edit", "prompt": "a fox", "input_images": [{"image_id": "a" * 32}]},
    {"mode": "vector", "prompt": "a fox"}, {"mode": "music", "prompt": "a calm tune"}])
def test_every_kind_of_run_carries_the_reference_through_resolving(body):
    """The reference is for all kinds (DESIGN.md section 37.4). Whether a tab is available on a machine is a different question, so this is asked of the request itself."""
    from studio.config import Settings
    from studio.runspec import PromptRef, RunCreate, resolve_run

    ref = PromptRef(prompt_id="c" * 32, version=3)
    resolved = resolve_run(RunCreate(**body, prompt_ref={"prompt_id": "c" * 32, "version": 3}), Settings.from_env({}))
    assert resolved.prompt_ref == ref and resolve_run(RunCreate(**body), Settings.from_env({})).prompt_ref is None


def test_a_music_run_is_never_called_edited_because_its_description_is_built_from_fields(tmp_path):
    from studio.builder.store import BuilderStore
    from studio.db import Database
    from test_db import run_row

    db = Database(tmp_path / "s.sqlite")
    try:
        prompt = BuilderStore(db).create_prompt(now="2026-11-01T10:00:00.000Z", title="Tune", text="A calm tune", for_kind="music")
        for n, mode in ((1, "music"), (2, "generate")):
            row = run_row(n, "done")
            row["mode"], row["prompt"] = mode, "Global Metadata\nBasic Attributes: bpm is 90."
            db.insert_run_if_capacity(row, cap=10)
            with db.tx() as c:
                c.execute("UPDATE runs SET prompt_id=?, prompt_version=1 WHERE id=?", (prompt, row["id"]))
        refs = db.prompt_refs_for_runs([db.get_run(f"{n:032x}") for n in (1, 2)])
        assert refs[f"{1:032x}"]["edited"] is False and refs[f"{2:032x}"]["edited"] is True
    finally:
        db.close()


def test_a_run_from_a_prompt_that_is_not_there_or_in_the_bin_or_has_no_such_version_is_refused(builder):
    prompt = make(builder)
    ghost = {"id": "f" * 32, "text": "x"}
    assert from_prompt(builder, ghost, expect=404) == {"detail": "That prompt doesn't exist.", "code": "prompt_not_found"}
    missing = from_prompt(builder, prompt, version=7, expect=422)
    assert missing["detail"][0]["loc"] == ["body", "prompt_ref", "version"] and "no version 7" in missing["detail"][0]["msg"]
    builder.post(f"{BASE}/{prompt['id']}/bin")
    assert from_prompt(builder, prompt, expect=409)["code"] == "prompt_in_bin"
    assert builder.get("/api/runs").json()["runs"] == []  # none of them was queued


@pytest.mark.parametrize("ref", [{"prompt_id": "x", "version": 1}, {"prompt_id": "a" * 32, "version": 0}, {"prompt_id": "a" * 32}, {"version": 1}, {"prompt_id": "a" * 32, "version": 1, "x": 1}])
def test_a_malformed_reference_is_a_422(builder, ref):
    assert builder.post("/api/runs", json={"prompt": "x", "prompt_ref": ref}).status_code == 422


def test_the_history_can_be_narrowed_to_the_runs_made_from_a_prompt(builder):
    one, two = make(builder, title="one"), make(builder, title="two")
    a, b = from_prompt(builder, one), from_prompt(builder, two)
    create_run(builder)
    got = builder.get("/api/runs", params={"prompt": one["id"]}).json()["runs"]
    assert [r["id"] for r in got] == [a["id"]] and b
    assert builder.get("/api/runs", params={"prompt": "f" * 32}).json()["runs"] == [] and builder.get("/api/runs", params={"prompt": "nope"}).status_code == 422
    assert len(builder.get("/api/runs").json()["runs"]) == 3


def test_the_prompt_shows_its_pictures_under_the_version_and_on_its_card(builder):
    prompt = make(builder)
    builder.post(f"{BASE}/{prompt['id']}/versions", json={"text": "A lighthouse at dawn"})
    first, second = from_prompt(builder, prompt, 1), from_prompt(builder, prompt, 2)
    for run in (first, second):
        wait_for(builder, run["id"])
    detail = builder.get(f"{BASE}/{prompt['id']}").json()
    by_version = {v["n"]: v for v in detail["versions"]}
    assert [r["run_id"] for r in by_version[1]["runs"]] == [first["id"]] and [r["run_id"] for r in by_version[2]["runs"]] == [second["id"]]
    assert by_version[1]["runs"][0]["images"][0]["thumb_url"].endswith("/thumb") and by_version[1]["runs"][0]["status"] == "done"
    card = builder.get(BASE).json()["prompts"][0]
    assert card["pictures"] == 2 and len(card["thumbs"]) == 2 and builder.get(card["thumbs"][0]["url"]).status_code == 200


def test_the_events_a_prompt_makes_are_ids_only(builder):
    bus = builder.app.state.bus
    sub = bus.subscribe()
    prompt = make(builder)
    builder.patch(f"{BASE}/{prompt['id']}", json={"title": "x"})
    builder.post("/api/builder/preferences", json={"text": "a"})
    seen = []
    while not sub.queue.empty():
        seen.append(sub.queue.get_nowait())
    bus.unsubscribe(sub)
    changed = [data for event, data in seen if event == "builder.changed"]
    assert {"kind": "prompt", "id": prompt["id"]} in changed and any(d["kind"] == "preferences" for d in changed)
    assert all(set(d) == {"kind", "id"} for d in changed)


# ------------------------------------------------------------------ what mutation checks showed the tests above did not pin down
def test_the_most_tags_and_the_longest_words_are_accepted_and_one_more_is_not(builder):
    """The limits are limits: exactly at each is fine (a prompt of 8000 characters, 12 tags, a note of 300), and one over is refused."""
    prompt = make(builder, text="x" * 8000, negative="n" * 8000, tags=[f"t{n}" for n in range(12)])
    assert len(prompt["text"]) == 8000 and len(prompt["negative"]) == 8000 and len(prompt["tags"]) == 12
    url = f"{BASE}/{prompt['id']}"
    assert builder.patch(url, json={"tags": [f"u{n}" for n in range(12)]}).status_code == 200
    assert builder.patch(url, json={"tags": [f"u{n}" for n in range(13)]}).json()["code"] == "too_many_tags"
    assert builder.post("/api/builder/preferences", json={"text": "p" * 300}).status_code == 201
    assert builder.post("/api/builder/preferences", json={"text": "p" * 301}).json()["code"] == "too_long"


def test_a_change_that_names_only_some_fields_changes_only_those(builder):
    """A patch with just a title must leave the project, the tags and the star as they were: the fields not sent are not the fields set to nothing."""
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    prompt = make(builder, tags=["sea"], project_id=project, favourite=True)
    url = f"{BASE}/{prompt['id']}"
    after = builder.patch(url, json={"title": "Only the title"}).json()
    assert (after["title"], after["project_id"], after["tags"], after["favourite"], after["for"]) == ("Only the title", project, ["sea"], True, "images")
    assert builder.patch(url, json={"favourite": False}).json()["project_id"] == project
    assert builder.patch(url, json={"tags": []}).json()["project_id"] == project


def test_a_value_that_only_looks_like_the_right_type_is_refused(builder):
    """Strict types: 1 is not a true, "3" is not a message number, "yes" is not a switch. A page that sent them would be saving something other than it means."""
    base = {"title": "t", "text": "a fox"}
    assert builder.post(BASE, json={**base, "favourite": 1}).status_code == 422
    assert builder.post(BASE, json={**base, "message_seq": "3"}).status_code == 422
    prompt = make(builder)
    url = f"{BASE}/{prompt['id']}"
    assert builder.patch(url, json={"favourite": 1}).status_code == 422
    assert builder.post(f"{url}/versions", json={"text": "more", "message_seq": "3"}).status_code == 422
    note = builder.post("/api/builder/preferences", json={"text": "soft light"}).json()
    assert builder.patch(f"/api/builder/preferences/{note['id']}", json={"enabled": "yes"}).status_code == 422
    assert builder.patch(f"/api/builder/preferences/{note['id']}", json={"enabled": 0}).status_code == 422
    assert builder.get(url).json()["favourite"] is False and builder.get("/api/builder/preferences").json()["preferences"][0]["enabled"] is True  # nothing changed
