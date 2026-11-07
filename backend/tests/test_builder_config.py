"""The Prompt builder's settings (DESIGN.md §37.4, §37.3 rule 12): what `.env` can set, what is refused, and that the address stays private.

The address of the person's Ollama is theirs alone. It is read from the environment, kept out of `repr` (so a settings object that lands in a log
line does not carry it), and a bad one is refused WITHOUT being quoted in the error."""

from __future__ import annotations

import pytest

from studio.config import ConfigError, Settings

SECRET = "ollama-box.lan.example"


def env(**values: str) -> dict[str, str]:
    return {key.upper(): value for key, value in values.items()}


def test_by_default_the_builder_is_not_set_up_and_its_numbers_are_the_specs():
    s = Settings.from_env({})
    assert (s.ollama_url, s.ollama_model, s.ollama_vision_model) == ("", "", "")
    assert s.builder_configured is False
    assert (s.ollama_keep_alive, s.ollama_num_ctx, s.ollama_think) == ("5m", 32768, "on")  # §37.4, §37.6 choices 14 and 19
    assert (s.ollama_timeout_s, s.ollama_max_reply_s, s.builder_at_once, s.builder_llm) == (120, 900, 1, "real")  # choice 18, rule 9


def test_it_is_set_up_only_with_an_address_and_a_model_or_the_scripted_model():
    assert Settings.from_env(env(studio_ollama_url=f"http://{SECRET}:11434")).builder_configured is False  # no model named
    assert Settings.from_env(env(studio_ollama_model="qwen3.8:27b")).builder_configured is False  # no address
    assert Settings.from_env(env(studio_ollama_url=f"http://{SECRET}:11434", studio_ollama_model="qwen3.8:27b")).builder_configured is True
    assert Settings.from_env(env(studio_builder_llm="fake")).builder_configured is True  # the tests' scripted model needs neither


@pytest.mark.parametrize("given,kept", [
    (f"http://{SECRET}:11434", f"http://{SECRET}:11434"),
    (f"http://{SECRET}:11434/", f"http://{SECRET}:11434"),  # a trailing slash is not part of the address
    (f"  https://{SECRET}  ", f"https://{SECRET}"),
    ("http://host.docker.internal:11434", "http://host.docker.internal:11434"),
    ("http://[::1]:11434", "http://[::1]:11434"),
])
def test_an_address_is_taken_as_a_scheme_and_a_host(given, kept):
    assert Settings.from_env(env(studio_ollama_url=given)).ollama_url == kept


@pytest.mark.parametrize("bad", [
    f"ftp://{SECRET}", f"{SECRET}:11434", f"http://user:pw@{SECRET}:11434", f"http://{SECRET}:11434/api", f"http://{SECRET}:11434?x=1", f"http://{SECRET}:notaport",
    f"http://{SECRET}:99999", "file:///etc/passwd",
])
def test_a_bad_address_is_refused_and_is_never_quoted_in_the_error(bad):
    with pytest.raises(ConfigError) as caught:
        Settings.from_env(env(studio_ollama_url=bad))
    message = str(caught.value)
    assert "STUDIO_OLLAMA_URL" in message
    assert SECRET not in message and "pw@" not in message and bad not in message  # the value is private, even when wrong


def test_an_address_with_nothing_after_the_scheme_is_refused():
    with pytest.raises(ConfigError, match="STUDIO_OLLAMA_URL"):
        Settings.from_env(env(studio_ollama_url="http://"))  # (not in the quoting test above: the message's own example starts the same way)


def test_the_address_is_not_in_the_settings_repr():
    s = Settings.from_env(env(studio_ollama_url=f"http://{SECRET}:11434", studio_ollama_model="m"))
    assert SECRET not in repr(s) and "11434" not in repr(s)
    assert "ollama_model='m'" in repr(s)  # the rest is there


@pytest.mark.parametrize("given,ok", [("5m", True), ("30s", True), ("1h", True), ("0", True), ("-1", True), ("300", True), ("5 minutes", False), ("m5", False), ("1.5h", False)])
def test_keep_alive_is_seconds_or_a_number_with_a_unit(given, ok):
    if ok:
        assert Settings.from_env(env(studio_ollama_keep_alive=given)).ollama_keep_alive == given
    else:
        with pytest.raises(ConfigError, match="STUDIO_OLLAMA_KEEP_ALIVE"):
            Settings.from_env(env(studio_ollama_keep_alive=given))


@pytest.mark.parametrize("given", ["on", "off", "model", "ON", " Off "])
def test_think_is_on_off_or_left_to_the_model(given):
    assert Settings.from_env(env(studio_ollama_think=given)).ollama_think == given.strip().lower()


def test_think_refuses_other_words():
    with pytest.raises(ConfigError, match="STUDIO_OLLAMA_THINK"):
        Settings.from_env(env(studio_ollama_think="maybe"))


def test_the_numbers_have_ranges_and_a_whole_reply_may_not_be_shorter_than_the_silence_limit():
    s = Settings.from_env(env(studio_ollama_num_ctx="16384", studio_ollama_timeout_s="60", studio_ollama_max_reply_s="600", studio_builder_at_once="2"))
    assert (s.ollama_num_ctx, s.ollama_timeout_s, s.ollama_max_reply_s, s.builder_at_once) == (16384, 60, 600, 2)
    for name, value in (("STUDIO_OLLAMA_NUM_CTX", "100"), ("STUDIO_OLLAMA_TIMEOUT_S", "1"), ("STUDIO_BUILDER_AT_ONCE", "0"), ("STUDIO_OLLAMA_MAX_REPLY_S", "5")):
        with pytest.raises(ConfigError, match=name):
            Settings.from_env({name: value})
    with pytest.raises(ConfigError, match="MAX_REPLY_S must not be smaller"):
        Settings.from_env(env(studio_ollama_timeout_s="300", studio_ollama_max_reply_s="200"))


def test_the_llm_is_real_or_fake_and_nothing_else():
    assert Settings.from_env(env(studio_builder_llm="FAKE")).builder_llm == "fake"
    with pytest.raises(ConfigError, match="STUDIO_BUILDER_LLM"):
        Settings.from_env(env(studio_builder_llm="gpt"))


# ------------------------------------------------------------------ compose.yaml and .env.example say what the code reads
def _repo_file(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[2] / name).read_text(encoding="utf-8")


def _builder_names() -> list[str]:
    import re as _re

    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "studio" / "config.py").read_text(encoding="utf-8")
    return sorted(set(_re.findall(r'"(STUDIO_(?:OLLAMA|BUILDER)_[A-Z_]+)"', source)))


def test_every_builder_setting_the_code_reads_is_in_compose_and_in_the_example_file():
    names = _builder_names()
    assert "STUDIO_OLLAMA_URL" in names and "STUDIO_BUILDER_LLM" in names and len(names) >= 10  # the scan found them
    compose, example = _repo_file("compose.yaml"), _repo_file(".env.example")
    # STUDIO_BUILDER_LLM=fake is the tests' scripted model: not something to offer in the files a person copies
    for name in (n for n in names if n != "STUDIO_BUILDER_LLM"):
        assert f"{name}:" in compose, f"{name} is read by the code but compose.yaml does not pass it on"
        assert f"\n{name}=" in example, f"{name} is read by the code but .env.example does not show it"
    assert "STUDIO_BUILDER_LLM" not in compose and "STUDIO_BUILDER_LLM" not in example


def test_the_values_compose_passes_when_nothing_is_set_are_accepted_by_the_parser():
    import re as _re

    compose = _repo_file("compose.yaml")
    passed = dict(_re.findall(r"^\s+(STUDIO_(?:OLLAMA|BUILDER)_[A-Z_]+): \$\{[A-Z_]+:-([^}]*)\}$", compose, flags=_re.M))
    assert set(passed) == {n for n in _builder_names() if n != "STUDIO_BUILDER_LLM"}
    settings = Settings.from_env(passed)  # as the container gets them: an empty address and model, the rest at their defaults
    assert settings.ollama_url == "" and settings.builder_configured is False
    assert (settings.ollama_keep_alive, settings.ollama_num_ctx, settings.ollama_think) == ("5m", 32768, "on")
    assert (settings.ollama_timeout_s, settings.ollama_max_reply_s, settings.builder_at_once) == (120, 900, 1)
    assert Settings.from_env({}).ollama_num_ctx == settings.ollama_num_ctx  # and they are the code's own defaults, not a second set


def test_the_container_can_reach_an_ollama_on_the_spark_itself():
    assert 'host.docker.internal:host-gateway' in _repo_file("compose.yaml")
