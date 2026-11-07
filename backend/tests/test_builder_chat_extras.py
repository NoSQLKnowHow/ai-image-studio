"""What a chat is given besides words (DESIGN.md section 37.3 rules 7, 8, 13; criteria 233, 234, 243, 244, 254): preferences, saved prompts, pictures."""

from __future__ import annotations

import io
import json

import pytest
from builder_support import Spy, builder, names, new_chat, png_bytes, result_of, send  # noqa: F401 (fixtures)
from PIL import Image

from studio.builder.fake import FakeModel, script
from studio.builder.files import MAX_SIDE

NOW = "2026-11-01T10:00:00.000Z"


def add_preference(client, pid: str, text: str, *, project=None, enabled=1, position=0) -> None:
    with client.app.state.db.tx() as c:
        c.execute("INSERT INTO preferences (id, text, project_id, enabled, source, position, created_at) VALUES (?, ?, ?, ?, 'hand', ?, ?)", (pid, text, project, enabled, position, NOW))


# ------------------------------------------------------------------ preferences (rule 7, criterion 233)
def test_the_enabled_preferences_that_apply_are_told_and_the_chat_says_how_many(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    other = builder.post("/api/projects", json={"name": "Pitch"}).json()["id"]
    add_preference(builder, "a", "photographic, natural light", position=2)
    add_preference(builder, "b", "flat style for the logo", project=project, position=1)
    add_preference(builder, "c", "switched off", enabled=0)
    add_preference(builder, "d", "belongs to the pitch", project=other)
    chat = new_chat(builder, project_id=project)
    events = send(builder, chat, text="a fox")
    assert dict(events)["context"]["preferences"] == 2
    system = spy.system
    assert system.index("flat style for the logo") < system.index("photographic, natural light")  # the order the person put them in
    assert "switched off" not in system and "belongs to the pitch" not in system
    assert result_of(events)["context"]["preferences"] == 2  # and it is kept with the reply, for the chat to say


def test_a_chat_in_no_project_is_told_only_the_ones_for_everywhere(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    add_preference(builder, "a", "everywhere note")
    add_preference(builder, "b", "logo note", project=project)
    send(builder, new_chat(builder), text="a fox")
    assert "everywhere note" in spy.system and "logo note" not in spy.system


def test_a_preference_of_a_deleted_project_is_off_and_never_told_to_anyone(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    add_preference(builder, "b", "logo note", project=project)
    builder.delete(f"/api/projects/{project}")
    send(builder, new_chat(builder), text="a fox")
    assert "logo note" not in spy.system


def test_a_suggestion_the_person_already_has_is_not_made_again(builder):
    add_preference(builder, "a", "Photographic, natural light")
    chat = new_chat(builder)
    send(builder, chat, text="idea")
    content = result_of(send(builder, chat, text="more"))  # the scripted model suggests "photographic, natural light" on its second reply
    assert content["remember"] == []


# ------------------------------------------------------------------ the person's saved prompts as examples (rule 8, criterion 234)
def save_prompts(client) -> dict:
    store = client.app.state.builder_store
    return {
        "lighthouse": store.create_prompt(now=NOW, title="Lighthouse at dusk", text="A lighthouse at dusk, waves breaking, warm light", tags=["sea"]),
        "storm": store.create_prompt(now=NOW, title="Storm over the harbour", text="A storm over a harbour with a lighthouse"),
        "fox": store.create_prompt(now=NOW, title="Red fox", text="A red fox in the snow"),
    }


def test_on_the_first_reply_the_most_similar_saved_prompts_are_shown_and_named(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    ids = save_prompts(builder)
    chat = new_chat(builder)
    events = send(builder, chat, text="a lighthouse in a storm")
    shown = dict(events)["context"]["examples"]
    assert {e["id"] for e in shown} == {ids["lighthouse"], ids["storm"]} and all(e["title"] for e in shown)
    assert '### "Lighthouse at dusk" (for images)' in spy.system and "A storm over a harbour with a lighthouse" in spy.system and "Red fox" not in spy.system
    assert {e["id"] for e in result_of(events)["context"]["examples"]} == {ids["lighthouse"], ids["storm"]}
    send(builder, chat, text="more")
    assert "saved prompts" not in spy.system  # only on the first reply


def test_the_person_can_leave_one_out_and_can_turn_it_all_off(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    ids = save_prompts(builder)
    events = send(builder, new_chat(builder), text="a lighthouse in a storm", exclude=[ids["storm"]])
    assert [e["id"] for e in dict(events)["context"]["examples"]] == [ids["lighthouse"]] and "Storm over the harbour" not in spy.system
    events = send(builder, new_chat(builder), text="a lighthouse in a storm", similar=False)
    assert dict(events)["context"]["examples"] == [] and "saved prompts" not in spy.system


def test_a_prompt_in_the_bin_is_never_shown(builder):
    ids = save_prompts(builder)
    builder.app.state.builder_store.bin_item("prompt", ids["lighthouse"], NOW)
    events = send(builder, new_chat(builder), text="a lighthouse in a storm")
    assert [e["id"] for e in dict(events)["context"]["examples"]] == [ids["storm"]]


def test_those_of_the_chats_project_come_first_and_at_most_three_are_shown(builder):
    store = builder.app.state.builder_store
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    for n in range(4):
        store.create_prompt(now=NOW, title=f"elsewhere {n}", text="a lighthouse")
    mine = store.create_prompt(now=NOW, title="mine", text="a lighthouse", project_id=project)
    shown = dict(send(builder, new_chat(builder, project_id=project), text="a lighthouse"))["context"]["examples"]
    assert len(shown) == 3 and shown[0]["id"] == mine


def test_with_nothing_similar_nothing_is_shown(builder):
    save_prompts(builder)
    assert dict(send(builder, new_chat(builder), text="quantum zeppelin"))["context"]["examples"] == []


# ------------------------------------------------------------------ pictures (rule 13, criteria 243, 244, 254)
def upload(client, chat, data=None, **params):
    return client.post(f"/api/builder/chats/{chat}/attachments", content=data if data is not None else png_bytes(), params=params)


def test_a_picture_is_kept_in_the_chats_folder_and_waits_for_a_message(builder):
    chat = new_chat(builder)
    response = upload(builder, chat, name="fox.png")
    body = response.json()
    assert response.status_code == 201 and (body["width"], body["height"], body["name"], body["message_seq"]) == (64, 48, "fox.png", 0) and body["url"] == f"/api/builder/attachments/{body['id']}"
    file = builder.app.state.storage.root / "builder" / chat
    assert [p.suffix for p in file.iterdir()] == [".jpg"]  # a picture without transparency is kept as a JPEG
    served = builder.get(body["url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/jpeg" and Image.open(io.BytesIO(served.content)).size == (64, 48)
    assert builder.get(f"/api/builder/chats/{chat}").json()["pending"][0]["id"] == body["id"]


def test_a_picture_with_transparency_stays_a_png(builder):
    chat = new_chat(builder)
    body = upload(builder, chat, png_bytes(mode="RGBA", colour=(0, 0, 0, 0))).json()
    assert builder.get(body["url"]).headers["content-type"] == "image/png"


def test_a_large_picture_is_shrunk_to_1536_on_its_long_side(builder):
    chat = new_chat(builder)
    body = upload(builder, chat, png_bytes(3000, 2000)).json()
    assert (body["width"], body["height"]) == (MAX_SIDE, 1024)
    small = upload(builder, chat, png_bytes(1536, 100)).json()
    assert (small["width"], small["height"]) == (1536, 100)  # at the limit: left alone


@pytest.mark.parametrize("data,status,words", [(b"", 422, "empty"), (b"not an image at all", 415, "isn't a PNG, JPEG or WebP"), (png_bytes(fmt="GIF"), 415, "GIF")])
def test_a_file_that_is_not_a_usable_picture_is_refused_in_the_words_of_an_edit(builder, data, status, words):
    chat = new_chat(builder)
    response = upload(builder, chat, data)
    assert response.status_code == status and words in response.json()["detail"] and response.json()["code"] == "bad_picture"
    assert not (builder.app.state.storage.root / "builder" / chat).exists() or not list((builder.app.state.storage.root / "builder" / chat).iterdir())


def test_a_file_over_the_upload_limit_is_refused(client_factory):
    client = client_factory(builder_llm="fake", max_upload_mb=1)
    chat = new_chat(client)
    assert upload(client, chat, b"x" * (1024 * 1024 + 10)).status_code == 413


def test_at_most_eight_pictures_wait_at_once(builder):
    chat = new_chat(builder)
    for _ in range(8):
        assert upload(builder, chat).status_code == 201
    over = upload(builder, chat)
    assert over.status_code == 422 and over.json()["code"] == "too_many"


def test_a_waiting_picture_can_be_taken_back_and_its_file_goes(builder):
    chat = new_chat(builder)
    body = upload(builder, chat).json()
    folder = builder.app.state.storage.root / "builder" / chat
    assert len(list(folder.iterdir())) == 1
    assert builder.delete(f"/api/builder/attachments/{body['id']}").json() == {"deleted": body["id"]}
    assert list(folder.iterdir()) == [] and builder.get(body["url"]).status_code == 404 and builder.get(f"/api/builder/chats/{chat}").json()["pending"] == []


def test_a_picture_that_was_sent_with_a_message_stays_with_the_chat(builder):
    chat = new_chat(builder)
    body = upload(builder, chat).json()
    send(builder, chat, text="a fox like this", attachments=[body["id"]])
    assert builder.delete(f"/api/builder/attachments/{body['id']}").status_code == 404
    reopened = builder.get(f"/api/builder/chats/{chat}").json()
    assert reopened["pending"] == [] and [a["id"] for a in reopened["messages"][0]["attachments"]] == [body["id"]] and reopened["messages"][1]["attachments"] == []


def test_the_model_is_sent_the_pictures_with_the_message_and_told_it_can_see_them(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    ids = [upload(builder, chat, png_bytes(colour=c), name=n).json()["id"] for c, n in (((255, 0, 0), "a.png"), ((0, 255, 0), "b.png"))]
    events = send(builder, chat, text="describe these", attachments=ids)
    last = spy.calls[-1]["messages"][-1]
    assert len(last["images"]) == 2 and all(isinstance(i, str) and len(i) > 100 for i in last["images"])
    assert "attached 2 pictures, numbered in the order given. You can see them" in spy.system
    assert result_of(events)["reply"].endswith("I can see 2 pictures.")  # (the scripted model counts them)


def test_a_model_that_cannot_see_is_told_so_with_the_picture_names_and_the_reply_can_say_it(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    monkeypatch.setattr(builder.app.state.builder, "can_see", lambda has=True: False)
    chat = new_chat(builder)
    ids = [upload(builder, chat, name="dog.png").json()["id"]]
    send(builder, chat, text="put the dog in the scene", attachments=ids)
    assert 'attached 1 picture ("dog.png"), but you cannot see pictures' in spy.system


def test_pictures_of_earlier_turns_go_back_with_those_turns(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    send(builder, chat, text="first", attachments=[upload(builder, chat).json()["id"]])
    send(builder, chat, text="second")
    messages = spy.calls[-1]["messages"]
    assert len(messages[1].get("images", [])) == 1 and "images" not in messages[-1]
    assert "attached" not in spy.system  # this turn has none


def test_a_picture_cannot_be_sent_with_a_message_of_another_chat(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    mine, theirs = new_chat(builder), new_chat(builder)
    stray = upload(builder, theirs).json()["id"]
    send(builder, mine, text="hello", attachments=[stray, "f" * 32])
    assert "images" not in spy.calls[-1]["messages"][-1]
    assert [p["id"] for p in builder.get(f"/api/builder/chats/{theirs}").json()["pending"]] == [stray]  # still waiting where it was


def test_a_picture_from_the_history_or_the_edit_tray_is_copied_in(builder):
    staged = builder.post("/api/uploads", content=png_bytes(40, 30)).json()
    chat = new_chat(builder)
    copied = builder.post(f"/api/builder/chats/{chat}/attachments/from-image", json={"image_id": staged["upload_id"]})
    assert copied.status_code == 201 and (copied.json()["width"], copied.json()["height"]) == (40, 30)
    assert builder.post(f"/api/builder/chats/{chat}/attachments/from-image", json={"image_id": "e" * 32}).status_code == 404
    assert builder.post(f"/api/builder/chats/{chat}/attachments/from-image", json={"image_id": 5}).status_code == 422
    assert builder.get(f"/api/builder/chats/{chat}").json()["pending"][0]["id"] == copied.json()["id"] and builder.delete(f"/api/uploads/{staged['upload_id']}").status_code in (200, 204)


def test_a_picture_whose_file_is_gone_does_not_stop_the_chat(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    body = upload(builder, chat).json()
    send(builder, chat, text="first", attachments=[body["id"]])
    for p in (builder.app.state.storage.root / "builder" / chat).iterdir():
        p.unlink()
    assert names(send(builder, chat, text="second"))[-1] == "result" and "images" not in spy.calls[-1]["messages"][1]


# ------------------------------------------------------------------ the vision model (criterion 254)
def test_pictures_go_to_the_vision_model_when_one_is_set_and_otherwise_to_the_main_model(client_factory):
    from studio.builder.service import BuilderService

    plain = BuilderService(client_factory(ollama_url="http://example.invalid:11434", ollama_model="main:1b").app.state.settings)
    assert plain.model_for(True) == "main:1b" and plain.model_for(False) == "main:1b"
    with_vision = BuilderService(client_factory(ollama_url="http://example.invalid:11434", ollama_model="main:1b", ollama_vision_model="eyes:7b").app.state.settings)
    assert with_vision.model_for(True) == "eyes:7b" and with_vision.model_for(False) == "main:1b"


# ------------------------------------------------------------------ the scripted model itself (criterion 252)
def conversation(user: str, kind: str = "images", think=False, schema=None) -> list:
    return [{"role": "system", "content": f"- This prompt is for: {kind}."}, {"role": "user", "content": user}]


def test_the_scripted_model_gives_the_same_answer_to_the_same_conversation():
    first = script(conversation("a lighthouse"), False, None)
    assert first == script(conversation("a lighthouse"), False, None) and first["questions"] and first["draft"]["prompt"] == "a lighthouse."


@pytest.mark.parametrize("kind,key", [("images", "shape"), ("edit", "shape"), ("vector", "colours"), ("music", "seconds")])
def test_it_suggests_the_settings_of_the_kind(kind, key):
    assert key in script(conversation("idea", kind), False, None)["draft"]["settings"]


def test_it_streams_its_answer_in_small_pieces_and_thinks_only_when_told_to():
    pieces = list(FakeModel().chat("m", conversation("idea"), think=True))
    assert [p.kind for p in pieces][:2] == ["thinking", "thinking"] and pieces[-1].kind == "done" and sum(p.kind == "content" for p in pieces) > 5
    assert all(len(p.text) <= 14 for p in pieces if p.kind == "content")
    assert json.loads("".join(p.text for p in pieces if p.kind == "content"))["reply"]
    assert [p.kind for p in FakeModel().chat("m", conversation("idea"), think=False)][0] == "content"
