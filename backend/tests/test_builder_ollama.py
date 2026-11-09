"""The studio's Ollama client against a fake Ollama (DESIGN.md §37.4, §37.3 rules 10 and 12; criteria 252, 253, 256).

What is checked: what the studio SENDS (the model, the messages, think, keep_alive, the context size, the schema), what it does with what comes back
(pieces in order, errors turned into sentences the page can show), that silence is the limit and not total time, that Stop works from another thread,
that a redirect is not followed, and that no failure ever carries the server's address."""

from __future__ import annotations

import threading
import time

import pytest
from fake_ollama import FakeOllama

from studio.builder.ollama import OllamaClient, OllamaError, clean_text, keep_alive_value, model_present


def client_for(fake: FakeOllama, **over) -> OllamaClient:
    values = dict(timeout_s=5, max_reply_s=30, keep_alive="5m", num_ctx=32768)
    values.update(over)
    return OllamaClient(fake.url, **values)


@pytest.fixture
def fake():
    with FakeOllama() as server:
        yield server


def pieces(call):
    return [(p.kind, p.text) for p in call]


# ------------------------------------------------------------------ the small helpers
def test_a_model_name_without_a_tag_means_latest():
    assert model_present(["qwen3.8:27b", "llama3:latest"], "qwen3.8:27b")
    assert model_present(["llama3:latest"], "llama3")  # as Ollama reads it
    assert not model_present(["llama3:8b"], "llama3")
    assert not model_present(["a:b"], "")
    assert not model_present([], "qwen3.8:27b")


def test_keep_alive_goes_as_a_number_of_seconds_or_as_the_duration_text():
    assert keep_alive_value("0") == 0 and keep_alive_value("-1") == -1 and keep_alive_value("300") == 300
    assert keep_alive_value("5m") == "5m" and keep_alive_value("1h") == "1h"


def test_text_from_outside_is_made_safe_to_show():
    assert clean_text("a\x00b\x1bc\n\n  d") == "a b c d"
    assert len(clean_text("x" * 1000)) == 300


# ------------------------------------------------------------------ the quick calls
def test_the_version_the_models_and_what_ollama_says_about_one(fake):
    c = client_for(fake)
    assert c.version() == "0.99.0"
    fake.models = ["qwen3.8:27b", "llava:7b"]
    assert c.models() == ["qwen3.8:27b", "llava:7b"]
    assert c.show("qwen3.8:27b") == {"capabilities": ["completion", "vision", "thinking", "tools"], "context_length": 262144}
    assert c.show("nothing:here") is None  # best-effort: a model that is not there is not an error


def test_show_is_best_effort_whatever_goes_wrong(fake):
    c = client_for(fake)
    fake.show_status = 500
    assert c.show("qwen3.8:27b") is None
    fake.show_status = None
    fake.context_length = None
    assert c.show("qwen3.8:27b")["context_length"] is None  # an Ollama that does not say


def test_unload_is_a_chat_with_no_messages_and_keep_alive_zero(fake):
    client_for(fake).unload("qwen3.8:27b")
    [sent] = fake.calls("/api/chat")
    assert sent["body"] == {"model": "qwen3.8:27b", "messages": [], "keep_alive": 0}
    assert fake.unloaded == ["qwen3.8:27b"]


# ------------------------------------------------------------------ a reply
def test_a_reply_arrives_as_thinking_then_content_then_done(fake):
    fake.script = [("thinking", "hmm. "), ("thinking", "ok."), ("content", "A "), ("content", "lighthouse."), ("done",)]
    got = pieces(client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "idea"}]))
    assert got == [("thinking", "hmm. "), ("thinking", "ok."), ("content", "A "), ("content", "lighthouse."), ("done", "")]


def test_what_the_studio_sends_with_a_reply(fake):
    schema = {"type": "object", "properties": {"reply": {"type": "string"}}}
    messages = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "a fox"}]
    pieces(client_for(fake, keep_alive="10m", num_ctx=16384).chat("qwen3.8:27b", messages, think=True, schema=schema))
    [sent] = fake.calls("/api/chat")
    assert sent["body"] == {"model": "qwen3.8:27b", "messages": messages, "stream": True, "keep_alive": "10m", "options": {"num_ctx": 16384}, "think": True, "format": schema}


@pytest.mark.parametrize("think,present", [(True, True), (False, True), (None, False)])
def test_think_is_sent_only_when_the_setting_says_so(fake, think, present):
    """`model` (None here) sends nothing and leaves it to the model (criterion 253); on and off send true and false."""
    pieces(client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "x"}], think=think))
    body = fake.calls("/api/chat")[0]["body"]
    assert ("think" in body) is present
    if present:
        assert body["think"] is think


def test_keep_alive_zero_is_sent_as_the_number_zero(fake):
    pieces(client_for(fake, keep_alive="0").chat("qwen3.8:27b", [{"role": "user", "content": "x"}]))
    assert fake.calls("/api/chat")[0]["body"]["keep_alive"] == 0 and isinstance(fake.calls("/api/chat")[0]["body"]["keep_alive"], int)


def test_the_summary_ollama_ends_with_is_passed_on(fake):
    [*_, last] = list(client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "x"}]))
    assert last.kind == "done" and last.info["done_reason"] == "stop" and last.info["eval_count"] == 7


# ------------------------------------------------------------------ what goes wrong
def raises(call, kind):
    with pytest.raises(OllamaError) as caught:
        list(call)
    assert caught.value.kind == kind, (caught.value.kind, caught.value.message)
    return caught.value


def test_a_model_ollama_does_not_have_is_named_with_what_to_do(fake):
    error = raises(client_for(fake).chat("nothing:here", [{"role": "user", "content": "x"}]), "model_missing")
    assert "nothing:here" in error.message and "ollama pull nothing:here" in error.hint


def test_running_out_of_memory_is_told_as_that_with_what_to_try(fake):
    fake.chat_status = (500, "model requires more system memory (30.1 GiB) than is available (12.0 GiB)")
    error = raises(client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "x"}]), "out_of_memory")
    assert "memory" in error.message and "STUDIO_OLLAMA_NUM_CTX" in error.hint


def test_any_other_refusal_is_shown_in_ollamas_own_words(fake):
    fake.chat_status = (400, "invalid options: nonsense\x00")
    error = raises(client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "x"}]), "refused")
    assert error.message == "invalid options: nonsense"  # the control character is gone


def test_an_error_in_the_middle_of_a_reply_is_a_failure_too(fake):
    fake.script = [("content", "Half "), ("error", "the model crashed")]
    call = client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "x"}])
    seen = []
    with pytest.raises(OllamaError) as caught:
        for piece in call:
            seen.append(piece.text)
    assert seen == ["Half "] and "crashed" in caught.value.message  # what had arrived was delivered before the failure


def test_a_line_that_is_not_json_is_an_unreadable_answer(fake):
    fake.script = [("garbage",)]
    raises(client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "x"}]), "bad_answer")


def test_a_reply_that_stops_before_it_is_done_is_not_taken_for_finished(fake):
    fake.script = [("content", "Half a thought")]  # the stream ends with no "done" and no final chunk
    error = raises(client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "x"}]), "bad_answer")
    assert "before it was finished" in error.message


def test_a_connection_dropped_in_the_middle_is_an_early_end(fake):
    fake.script = [("content", "Half "), ("die",)]
    raises(client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "x"}]), "bad_answer")


def test_a_server_that_is_not_there_is_unreachable_and_the_sentence_does_not_name_it():
    with FakeOllama() as gone:
        url_host, url_port = gone.host, gone.port
        client = client_for(gone)
    error = raises(client.chat("qwen3.8:27b", [{"role": "user", "content": "x"}]), "unreachable")
    for call in (client.version, client.models, lambda: client.unload("m")):
        with pytest.raises(OllamaError) as caught:
            call()
        assert caught.value.kind == "unreachable"
    text = f"{error.message} {error.hint}"
    assert str(url_host) not in text and str(url_port) not in text  # rule 12


def test_a_redirect_is_not_followed(fake):
    other = FakeOllama().start()
    try:
        fake.redirect_to = other.url + "/api/version"
        c = client_for(fake)
        with pytest.raises(OllamaError) as caught:
            c.version()
        assert caught.value.kind == "refused" and "redirect" in caught.value.message
        assert other.requests == []  # the other address never heard from the studio
        raises(c.chat("qwen3.8:27b", [{"role": "user", "content": "x"}]), "refused")
        assert other.requests == []
    finally:
        other.stop()


# ------------------------------------------------------------------ time
def test_silence_longer_than_the_limit_fails_the_reply(fake):
    fake.script = [("content", "Start "), ("stall",)]
    started = time.monotonic()
    error = raises(client_for(fake, timeout_s=1).chat("qwen3.8:27b", [{"role": "user", "content": "x"}]), "timeout")
    assert 0.8 < time.monotonic() - started < 4 and "1 seconds" in error.message


def test_a_long_think_that_keeps_arriving_is_not_cut_off(fake):
    """The point of §37.6 choice 18: the limit is silence, not the total. Pieces every 0.4 s for 2.4 s pass a 1 s silence limit."""
    fake.script = [s for _ in range(6) for s in (("thinking", "."), ("sleep", 0.4))] + [("content", "Done."), ("done",)]
    got = pieces(client_for(fake, timeout_s=1, max_reply_s=30).chat("qwen3.8:27b", [{"role": "user", "content": "x"}]))
    assert got[-2:] == [("content", "Done."), ("done", "")] and sum(1 for k, _ in got if k == "thinking") == 6


def test_a_reply_that_goes_on_too_long_is_stopped_at_the_cap(fake):
    fake.script = [s for _ in range(20) for s in (("thinking", "."), ("sleep", 0.3))] + [("done",)]
    started = time.monotonic()
    error = raises(client_for(fake, timeout_s=2, max_reply_s=1).chat("qwen3.8:27b", [{"role": "user", "content": "x"}]), "too_long")
    assert time.monotonic() - started < 4 and "1 seconds" in error.message and "STUDIO_OLLAMA_MAX_REPLY_S" in error.hint


def test_a_quick_call_gives_up_if_ollama_accepts_the_connection_and_says_nothing():
    import socket

    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(1)  # it accepts the connection (the operating system does) and nobody ever answers
    try:
        c = OllamaClient(f"http://127.0.0.1:{silent.getsockname()[1]}", timeout_s=30, max_reply_s=60, keep_alive="5m", num_ctx=1024)
        started = time.monotonic()
        with pytest.raises(OllamaError) as caught:
            c._json_call("GET", "/api/version", timeout=0.5)
        assert caught.value.kind == "timeout" and time.monotonic() - started < 3
        assert "0 seconds" in caught.value.message  # int(0.5): it says how long it waited, and never where
    finally:
        silent.close()


def test_stop_works_from_another_thread_and_ends_the_reply_at_once(fake):
    fake.script = [("content", "Start "), ("stall",)]
    call = client_for(fake, timeout_s=30).chat("qwen3.8:27b", [{"role": "user", "content": "x"}])
    seen, errors = [], []

    def read():
        try:
            for piece in call:
                seen.append(piece.text)
        except OllamaError as exc:
            errors.append(exc)

    reader = threading.Thread(target=read)
    reader.start()
    time.sleep(0.5)
    assert seen == ["Start "]  # it was under way
    started = time.monotonic()
    call.cancel()
    reader.join(5)
    assert not reader.is_alive() and time.monotonic() - started < 2
    assert [e.kind for e in errors] == ["stopped"]


def test_a_reply_cancelled_before_it_starts_never_connects(fake):
    call = client_for(fake).chat("qwen3.8:27b", [{"role": "user", "content": "x"}])
    call.cancel()
    raises(call, "stopped")
    assert fake.calls("/api/chat") == []
