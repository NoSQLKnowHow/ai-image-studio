"""A chat and its replies (DESIGN.md section 37; criteria 224-228, 232-234, 248, 250, 253, 255-257), against the scripted model."""

from __future__ import annotations

import json

import pytest
from builder_support import Spy, builder, in_thread, live, names, new_chat, result_of, send, wait_until  # noqa: F401 (fixtures)

from studio.builder.ollama import OllamaError

NOW = "2026-11-01T10:00:00.000Z"


# ------------------------------------------------------------------ chats
def test_a_chat_is_made_with_a_name_a_kind_and_a_detail(builder):
    response = builder.post("/api/builder/chats", json={"title": "  A   lighthouse  ", "for": "vector", "detail": "rich"})
    chat = response.json()
    assert response.status_code == 201 and chat["title"] == "A lighthouse" and chat["for"] == "vector" and chat["detail"] == "rich"
    assert set(chat) == {"id", "title", "for", "project_id", "detail", "prompt_id", "created_at", "updated_at", "deleted_at"} and len(chat["id"]) == 32
    plain = builder.post("/api/builder/chats", json={}).json()
    assert plain["title"] == "New chat" and plain["for"] == "images" and plain["detail"] == "normal"


@pytest.mark.parametrize("body", [{"for": "sculptures"}, {"detail": "huge"}, {"title": "t" * 121}, {"surprise": 1}, {"project_id": 5}])
def test_a_chat_with_a_value_outside_the_sets_is_refused(builder, body):
    assert builder.post("/api/builder/chats", json=body).status_code == 422


def test_a_chat_can_only_be_filed_in_a_project_that_exists_and_linked_to_a_prompt_that_does(builder):
    assert builder.post("/api/builder/chats", json={"project_id": "a" * 32}).json()["code"] == "no_project"
    assert builder.post("/api/builder/chats", json={"prompt_id": "a" * 32}).status_code == 404
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    assert builder.post("/api/builder/chats", json={"project_id": project}).json()["project_id"] == project
    store = builder.app.state.builder_store
    prompt = store.create_prompt(now=NOW, title="t", text="x")
    assert builder.post("/api/builder/chats", json={"prompt_id": prompt}).json()["prompt_id"] == prompt
    store.bin_item("prompt", prompt, NOW)
    assert builder.post("/api/builder/chats", json={"prompt_id": prompt}).json()["code"] == "prompt_in_bin"


def test_chats_are_listed_newest_first_with_the_last_line_and_the_bin_apart(builder):
    first, second = new_chat(builder, title="first"), new_chat(builder, title="second")
    send(builder, first, text="a lighthouse in a storm")
    listed = builder.get("/api/builder/chats").json()["chats"]
    assert [c["id"] for c in listed] == [first, second] and listed[0]["message_count"] == 2 and listed[0]["last_text"].startswith("Lovely idea")
    builder.post(f"/api/builder/chats/{second}/bin")
    assert [c["id"] for c in builder.get("/api/builder/chats").json()["chats"]] == [first]
    assert [c["id"] for c in builder.get("/api/builder/chats", params={"deleted": "true"}).json()["chats"]] == [second]


def test_chats_can_be_listed_by_project(builder):
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    inside, outside = new_chat(builder, project_id=project), new_chat(builder)
    assert [c["id"] for c in builder.get("/api/builder/chats", params={"project": project}).json()["chats"]] == [inside]
    assert [c["id"] for c in builder.get("/api/builder/chats", params={"project": "none"}).json()["chats"]] == [outside]


def test_a_chat_is_renamed_refiled_and_changed_in_what_it_is_for(builder):
    chat = new_chat(builder)
    project = builder.post("/api/projects", json={"name": "Logo"}).json()["id"]
    changed = builder.patch(f"/api/builder/chats/{chat}", json={"title": " Harbour  at dusk ", "for": "music", "detail": "short", "project_id": project}).json()
    assert (changed["title"], changed["for"], changed["detail"], changed["project_id"]) == ("Harbour at dusk", "music", "short", project)
    assert builder.patch(f"/api/builder/chats/{chat}", json={"project_id": None}).json()["project_id"] is None
    assert builder.patch(f"/api/builder/chats/{chat}", json={"title": "   "}).json()["code"] == "bad_title"
    assert builder.patch(f"/api/builder/chats/{chat}", json={"title": ""}).status_code == 422
    assert builder.patch(f"/api/builder/chats/{chat}", json={"project_id": "b" * 32}).json()["code"] == "no_project"
    assert builder.patch("/api/builder/chats/" + "f" * 32, json={"title": "x"}).status_code == 404


def test_a_chat_goes_to_the_bin_comes_back_and_is_deleted_for_good_with_its_pictures(builder):
    chat = new_chat(builder)
    upload = builder.post(f"/api/builder/chats/{chat}/attachments", content=__import__("builder_support").png_bytes())
    assert upload.status_code == 201
    folder = builder.app.state.storage.root / "builder" / chat
    assert folder.is_dir() and len(list(folder.iterdir())) == 1
    assert builder.post(f"/api/builder/chats/{chat}/bin").json()["deleted_at"] is not None
    assert builder.post(f"/api/builder/chats/{chat}/restore").json()["deleted_at"] is None
    assert builder.post(f"/api/builder/chats/{chat}/restore").json()["code"] == "not_in_bin"
    assert builder.delete(f"/api/builder/chats/{chat}").json() == {"deleted": chat}
    assert not folder.exists() and builder.get(f"/api/builder/chats/{chat}").status_code == 404
    assert builder.delete(f"/api/builder/chats/{chat}").status_code == 404


def test_what_does_not_exist_is_not_found(builder):
    ghost = "e" * 32
    for method, path in (("get", f"/api/builder/chats/{ghost}"), ("post", f"/api/builder/chats/{ghost}/bin"), ("post", f"/api/builder/chats/{ghost}/restore"),
                         ("get", f"/api/builder/attachments/{ghost}"), ("delete", f"/api/builder/attachments/{ghost}")):
        assert getattr(builder, method)(path).status_code == 404, path
    assert builder.post(f"/api/builder/chats/{ghost}/messages", json={"text": "x"}).status_code == 404


# ------------------------------------------------------------------ the reply
def test_a_reply_streams_in_order_and_the_chat_is_saved_from_the_first_message(builder):
    chat = new_chat(builder)
    events = send(builder, chat, text="a lighthouse in a storm")
    assert names(events)[:2] == ["start", "context"] and names(events)[-1] == "result" and "thinking" in names(events)
    deltas = [d["text"] for n, d in events if n == "delta"]
    assert len(deltas) > 1 and all(b.startswith(a) for a, b in zip(deltas, deltas[1:])) and deltas[-1] == "Lovely idea. A few questions."  # it grows, and only grows
    result = events[-1][1]
    message = result["message"]
    assert message["role"] == "assistant" and message["seq"] == 2 and message["model"] == "fake-builder" and message["text"] == "Lovely idea. A few questions."
    content = message["content"]
    assert [q["text"] for q in content["questions"]] == ["What time of day?", "What style should it have?"] and len(content["questions"][0]["chips"]) == 4
    assert content["draft"]["prompt"] == "a lighthouse in a storm." and content["draft"]["settings"] == {"shape": "16:9", "guidance": 4.0} and content["notices"] == []
    assert result["chat"]["title"] == "A lighthouse in a storm"  # the model's title replaces the first words, as the chat still had that name
    saved = builder.get(f"/api/builder/chats/{chat}").json()
    assert [(m["seq"], m["role"]) for m in saved["messages"]] == [(1, "user"), (2, "assistant")] and saved["messages"][0]["text"] == "a lighthouse in a storm"
    assert saved["draft"]["prompt"] == "a lighthouse in a storm."


def test_a_chat_with_no_name_is_named_from_the_first_words_when_the_model_gives_no_title(builder):
    chat = new_chat(builder)
    send(builder, chat, text="  a   lighthouse in a storm [[bad-always]]")
    assert builder.get(f"/api/builder/chats/{chat}").json()["chat"]["title"] == "a lighthouse in a storm [[bad-always]]"


def test_the_models_title_does_not_replace_one_the_person_gave(builder):
    chat = new_chat(builder, title="My own name")
    send(builder, chat, text="a lighthouse in a storm")
    assert builder.get(f"/api/builder/chats/{chat}").json()["chat"]["title"] == "My own name"


def test_the_questions_are_answered_with_chips_surprise_and_skip_and_the_draft_grows(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    send(builder, chat, text="a lighthouse in a storm")
    events = send(builder, chat, text="with a small boat", answers=[{"id": "q1", "chips": ["Dusk", "Dawn"]}, {"id": "q2", "mode": "surprise"}])
    assert "Dusk; Dawn" in spy.last_user and 'Surprise me' in spy.last_user and "with a small boat" in spy.last_user
    stored_user = builder.get(f"/api/builder/chats/{chat}").json()["messages"][2]["content"]
    assert stored_user["answers"] == [{"id": "q1", "question": "What time of day?", "mode": "chips", "chips": ["Dusk", "Dawn"]},
                                      {"id": "q2", "question": "What style should it have?", "mode": "surprise", "chips": []}]
    content = result_of(events)
    assert content["draft"]["prompt"] == "a lighthouse in a storm, with a small boat, Dusk; Dawn." and [q["text"] for q in content["questions"]] == ["Which colours?"]
    assert content["remember"] == ["photographic, natural light"]


def test_an_answer_to_a_question_that_was_not_asked_is_ignored(builder):
    chat = new_chat(builder)
    send(builder, chat, text="idea")
    send(builder, chat, text="more", answers=[{"id": "zz", "chips": ["x"]}, {"id": "q1", "chips": []}, {"id": "q1", "mode": "skip"}])
    assert builder.get(f"/api/builder/chats/{chat}").json()["messages"][2]["content"]["answers"] == [{"id": "q1", "question": "What time of day?", "mode": "skip", "chips": []}]


def test_write_it_now_asks_no_more_and_is_done(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    send(builder, chat, text="idea")
    content = result_of(send(builder, chat, intent="write_now"))
    assert content["questions"] == [] and content["done"] is True and content["tags"] == ["scripted", "images"]
    assert "Ask nothing more" in spy.last_user and spy.last_user.startswith("(no words)")


def test_three_wordings_are_asked_for_and_kept_apart_from_the_draft(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    send(builder, chat, text="a fox")
    content = result_of(send(builder, chat, intent="variations"))
    assert [v["label"] for v in content["variations"]] == ["simpler", "more cinematic", "more detailed"]
    assert "variations" in spy.calls[-1]["schema"]["properties"] and "variations" not in spy.calls[0]["schema"]["properties"]


def test_the_chat_carries_on_with_a_draft_the_person_edited_by_hand(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    send(builder, chat, text="a fox")
    send(builder, chat, text="make it snowy", draft={"prompt": "MY OWN WORDS about a fox", "negative": "blur", "settings": {"shape": "1:1"}})
    assert "MY OWN WORDS about a fox" in spy.system and '"shape": "1:1"' in spy.system and "Negative prompt:\nblur" in spy.system
    assert builder.get(f"/api/builder/chats/{chat}").json()["messages"][2]["content"]["draft"]["prompt"] == "MY OWN WORDS about a fox"


def test_the_draft_is_not_lost_when_a_later_reply_has_none(builder):
    chat = new_chat(builder)
    send(builder, chat, text="a fox")
    send(builder, chat, text="hello [[bad-always]]")  # a plain-text reply: no draft
    assert builder.get(f"/api/builder/chats/{chat}").json()["draft"]["prompt"] == "a fox."


# ------------------------------------------------------------------ thinking (criterion 253, 255)
@pytest.mark.parametrize("setting,override,sent", [("on", None, True), ("off", None, False), ("model", None, None), ("on", False, False), ("off", True, True)])
def test_think_is_sent_as_the_setting_says_unless_the_message_has_its_own(client_factory, monkeypatch, setting, override, sent):
    client = client_factory(builder_llm="fake", ollama_think=setting)
    spy = Spy(client.app.state.builder, monkeypatch)
    chat = new_chat(client)
    body = {"text": "idea"} if override is None else {"text": "idea", "think": override}
    events = send(client, chat, **body)
    assert spy.calls[0]["think"] is sent
    assert ("thinking" in names(events)) is (sent is True)  # the scripted model thinks aloud only when told to


def test_thinking_is_shown_but_never_kept(builder):
    chat = new_chat(builder)
    events = send(builder, chat, text="idea")
    thinking = [d for n, d in events if n == "thinking"]
    assert thinking and thinking[0]["text"] == "Considering the idea. " and isinstance(thinking[0]["seconds"], float)
    everything = json.dumps(builder.get(f"/api/builder/chats/{chat}").json()) + json.dumps(builder.get("/api/builder/chats").json())
    assert "Considering" not in everything and "Weighing" not in everything  # not in the database, not shown when the chat is reopened


def test_the_reasoning_is_not_sent_back_with_later_turns(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    send(builder, chat, text="idea")
    send(builder, chat, text="more")
    assert "Considering" not in json.dumps(spy.calls[-1]["messages"]) and "thinking" not in json.dumps([m for m in spy.calls[-1]["messages"] if m["role"] == "assistant"])


# ------------------------------------------------------------------ an answer that is not in the structure asked for (criterion 227, 257)
def test_an_answer_that_cannot_be_read_is_asked_again_once_with_thinking_off(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    events = send(builder, chat, text="idea [[bad]]")
    assert names(events).count("retry") == 1 and [c["think"] for c in spy.calls] == [True, False]
    content = result_of(events)
    assert content["plain"] is False and content["questions"] and content["draft"]


@pytest.mark.parametrize("setting", ["on", "off", "model"])
def test_the_second_ask_has_thinking_off_whatever_the_setting(client_factory, monkeypatch, setting):
    client = client_factory(builder_llm="fake", ollama_think=setting)
    spy = Spy(client.app.state.builder, monkeypatch)
    send(client, new_chat(client), text="idea [[bad-once]]")
    assert len(spy.calls) == 2 and spy.calls[1]["think"] is False


def test_an_answer_that_is_still_not_readable_is_shown_as_plain_text_with_no_chips_and_no_draft_change(builder, monkeypatch):
    spy = Spy(builder.app.state.builder, monkeypatch)
    chat = new_chat(builder)
    send(builder, chat, text="a fox")
    events = send(builder, chat, text="more [[bad-always]]")
    content = result_of(events)
    assert len(spy.calls) == 3 and content["plain"] is True and content["questions"] == [] and content["draft"] is None and content["tags"] == []
    assert content["reply"].startswith("I think you want something lovely")
    assert builder.get(f"/api/builder/chats/{chat}").json()["draft"]["prompt"] == "a fox."  # unchanged


def test_what_breaks_the_studios_rules_is_dropped_and_the_reply_says_so(builder):
    chat = new_chat(builder)
    content = result_of(send(builder, chat, text="idea [[bad-settings]]"))
    assert content["draft"]["settings"] == {} and content["notices"] == [
        "The builder suggested shape 21:9; the studio's shapes are 1:1, 4:3, 3:4, 3:2, 2:3, 16:9, 9:16, so it was left out.",
        "The builder suggested guidance 40; the studio allows 0.1 to 20, so it was left out."]
    assert content["draft"]["prompt"] == "idea."  # the words stay; only the suggestion that broke a rule goes


def test_five_questions_become_three_and_a_long_reply_is_shortened(builder):
    chat = new_chat(builder)
    many = result_of(send(builder, chat, text="idea [[many]]"))
    assert len(many["questions"]) == 3 and "asked 5 questions" in many["notices"][0]
    long = result_of(send(builder, new_chat(builder), text="idea [[long]]"))
    assert len(long["reply"]) == 2001 and long["notices"] == ["The reply was shortened to 2000 characters."]


def test_markup_in_a_reply_is_kept_as_text(builder):
    content = result_of(send(builder, new_chat(builder), text="idea [[markup]]"))
    assert content["reply"] == "<b>bold</b> <script>alert(1)</script> [link](http://example.com)"


# ------------------------------------------------------------------ a reply that fails never damages the chat (rule 10, criterion 228)
@pytest.mark.parametrize("kind,status", [("unreachable", 502), ("timeout", 504), ("model_missing", 409), ("out_of_memory", 409), ("refused", 502)])
def test_a_failed_reply_keeps_the_chat_and_the_message_and_says_what_happened(builder, kind, status):
    chat = new_chat(builder)
    events = send(builder, chat, text=f"a fox [[error:{kind}]]")
    assert names(events)[-1] == "error"
    error = events[-1][1]
    assert error["code"] == f"ollama_{kind}" and error["status"] == status and error["message"] and "http" not in json.dumps(error)
    saved = builder.get(f"/api/builder/chats/{chat}").json()
    assert [m["role"] for m in saved["messages"]] == ["user"] and saved["messages"][0]["text"] == f"a fox [[error:{kind}]]"  # their words are still there


def test_retry_sends_the_same_message_again_and_adds_none(builder, monkeypatch):
    chat = new_chat(builder)
    spy = Spy(builder.app.state.builder, monkeypatch, fail_first=lambda: OllamaError("timeout", "Ollama stopped answering.", "Try again."))
    first = send(builder, chat, text="a lighthouse")
    assert names(first)[-1] == "error" and first[-1][1]["code"] == "ollama_timeout"
    second = send(builder, chat, retry=True)
    assert names(second)[-1] == "result"
    saved = builder.get(f"/api/builder/chats/{chat}").json()["messages"]
    assert [(m["seq"], m["role"], m["text"]) for m in saved][:2] == [(1, "user", "a lighthouse"), (2, "assistant", "Lovely idea. A few questions.")] and len(saved) == 2
    assert "a lighthouse" in spy.last_user


def test_retry_with_nothing_to_retry_is_refused(builder):
    chat = new_chat(builder)
    assert builder.post(f"/api/builder/chats/{chat}/messages", json={"retry": True}).json()["code"] == "nothing_to_retry"
    send(builder, chat, text="idea")
    assert builder.post(f"/api/builder/chats/{chat}/messages", json={"retry": True}).json()["code"] == "nothing_to_retry"  # the last message is the model's


def test_a_bug_in_a_reply_is_a_short_message_and_the_chat_is_as_it_was(builder, monkeypatch):
    monkeypatch.setattr(builder.app.state.builder, "chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret detail")))
    events = send(builder, new_chat(builder), text="idea")
    assert events[-1][0] == "error" and events[-1][1]["code"] == "builder_failed" and "secret" not in json.dumps(events[-1][1])


# ------------------------------------------------------------------ Stop, and closing the page (rule 6)
def test_stop_keeps_what_was_written_marked_stopped_and_changes_nothing_else(live):
    server = live()
    chat = server.chat()
    server.send(chat, text="a fox")
    got: list = []
    with server.stream(chat, text="more [[slow]]") as events:
        for event in events:
            got.append(event)
            if event[0] == "delta" and sum(1 for n, _ in got if n == "delta") == 3:
                assert server.http.post(f"/api/builder/chats/{chat}/stop").json() == {"stopping": True}
    assert names(got)[-1] == "stopped"
    message = got[-1][1]["message"]
    assert message["content"]["stopped"] is True and message["content"]["draft"] is None and message["content"]["questions"] == []
    assert message["text"] and message["text"].startswith("Thank")
    saved = server.http.get(f"/api/builder/chats/{chat}").json()
    assert saved["messages"][-1]["content"]["stopped"] is True and saved["draft"]["prompt"] == "a fox."  # the draft is as it was
    wait_until(lambda: server.jobs == {})


def test_closing_the_page_in_the_middle_keeps_none_of_the_reply(live):
    server = live()
    chat = server.chat()
    with server.stream(chat, text="a fox [[slow]]") as events:
        for name, _ in events:
            if name == "delta":
                break  # the page is closed
    wait_until(lambda: server.jobs == {})
    saved = server.http.get(f"/api/builder/chats/{chat}").json()
    assert [m["role"] for m in saved["messages"]] == ["user"]  # their words are saved; the half of a reply is not


def test_stop_before_any_words_keeps_nothing(live):
    server = live()
    chat = server.chat()
    got: list = []
    with server.stream(chat, text="a fox [[slow]]") as events:
        for event in events:
            got.append(event)
            if event[0] == "thinking":
                server.http.post(f"/api/builder/chats/{chat}/stop")
    assert names(got)[-1] == "stopped" and got[-1][1]["message"] is None
    assert [m["role"] for m in server.http.get(f"/api/builder/chats/{chat}").json()["messages"]] == ["user"]


def test_stop_with_no_reply_being_written_says_so(builder):
    assert builder.post(f"/api/builder/chats/{new_chat(builder)}/stop").json()["code"] == "no_reply"


def test_a_stopped_reply_is_not_sent_back_as_if_it_were_complete_and_the_chat_carries_on(live):
    server = live()
    chat = server.chat()
    with server.stream(chat, text="a fox [[slow]]") as events:
        count = 0
        for name, _ in events:
            count += name == "delta"
            if count == 2:
                server.http.post(f"/api/builder/chats/{chat}/stop")
    again = server.send(chat, text="a fox")
    assert names(again)[-1] == "result"


# ------------------------------------------------------------------ one at a time (rule 9, criterion 248)
def test_a_second_reply_waits_for_the_first_and_says_so(builder):
    one, two = new_chat(builder), new_chat(builder)
    first, a = in_thread(lambda: send(builder, one, text="a fox [[slow]]"))
    wait_until(lambda: one in builder.app.state.builder_jobs and builder.app.state.builder_slots.locked())
    second, b = in_thread(lambda: send(builder, two, text="an owl"))
    first.join(10), second.join(10)
    assert names(a[0])[-1] == "result" and names(b[0])[0] == "waiting" and names(b[0])[-1] == "result"
    assert "waiting" not in names(a[0])


def test_the_same_chat_cannot_have_two_replies_at_once(builder):
    chat = new_chat(builder)
    first, a = in_thread(lambda: send(builder, chat, text="a fox [[slow]]"))
    wait_until(lambda: chat in builder.app.state.builder_jobs)
    assert builder.post(f"/api/builder/chats/{chat}/messages", json={"text": "again"}).json()["code"] == "reply_in_progress"
    assert builder.delete(f"/api/builder/chats/{chat}").json()["code"] == "reply_in_progress"
    first.join(10)


def test_while_an_image_is_being_made_the_chat_says_replies_may_be_slower(builder):
    chat = new_chat(builder)
    assert "busy" not in names(send(builder, chat, text="idea"))
    from test_db import run_row

    builder.app.state.db.insert_run_if_capacity(run_row(1, "running"), cap=10)
    events = send(builder, chat, text="more")
    assert ("busy", {"images": True}) in events and names(events).index("busy") < names(events).index("start")


# ------------------------------------------------------------------ not set up, empty, in the bin
def test_with_no_builder_set_up_a_message_says_how(client):
    chat = client.post("/api/builder/chats", json={})
    chat_id = chat.json()["id"]
    response = client.post(f"/api/builder/chats/{chat_id}/messages", json={"text": "idea"})
    assert response.status_code == 409 and response.json()["code"] == "builder_not_configured"


def test_an_empty_message_a_message_to_a_chat_in_the_bin_and_a_too_long_draft_are_refused(builder):
    chat = new_chat(builder)
    assert builder.post(f"/api/builder/chats/{chat}/messages", json={}).json()["code"] == "empty_message"
    assert builder.post(f"/api/builder/chats/{chat}/messages", json={"text": "x" * 4001}).status_code == 422
    assert builder.post(f"/api/builder/chats/{chat}/messages", json={"text": "x", "draft": {"prompt": "p" * 8001}}).json()["code"] == "draft_too_long"
    assert builder.post(f"/api/builder/chats/{chat}/messages", json={"text": "x", "intent": "nonsense"}).status_code == 422
    builder.post(f"/api/builder/chats/{chat}/bin")
    assert builder.post(f"/api/builder/chats/{chat}/messages", json={"text": "x"}).json()["code"] == "chat_in_bin"


def test_a_button_is_a_message_even_with_no_words(builder):
    chat = new_chat(builder)
    send(builder, chat, text="idea")
    assert names(send(builder, chat, intent="write_now"))[-1] == "result"
