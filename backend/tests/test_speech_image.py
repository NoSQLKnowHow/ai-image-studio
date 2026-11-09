"""The files that put the Speech tab into the container image (DESIGN.md §38.3, §38.18): compose.yaml, .env.example, the Dockerfile, the requirement files of each engine
and the build-time check. Nothing here builds an image (that is done on the Spark); what is checked is that these files agree with the code and with each other, which is
what goes wrong first: a setting the code reads that compose does not pass, a default written twice that drifts, a pin the engine relies on that is not pinned.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from studio.config import Settings
from studio.speech import registry

ROOT = Path(__file__).resolve().parents[2]


def repo_file(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def speech_names_in_code() -> set[str]:
    """Every STUDIO_SPEECH_* name config.py reads by its literal name, plus the one for each real engine's model (built from its id, as config.py does)."""
    source = (Path(__file__).resolve().parents[1] / "studio" / "config.py").read_text(encoding="utf-8")
    literal = set(re.findall(r'"(STUDIO_SPEECH_[A-Z0-9_]+)"', source))
    per_engine = {f"STUDIO_SPEECH_{engine_id.upper().replace('-', '_')}_MODEL" for engine_id in registry.REAL_ENGINES}
    return literal | per_engine


IMAGE_SETS = {"STUDIO_SPEECH_LIBS"}  # the Dockerfile sets it (ENV): a person has no reason to


def test_the_scan_finds_the_settings_it_should():
    names = speech_names_in_code()
    assert {"STUDIO_SPEECH_ENGINES", "STUDIO_SPEECH_GPU_FRACTION", "STUDIO_SPEECH_TEMP_HOURS", "STUDIO_SPEECH_QWEN3_TTS_MODEL", "STUDIO_SPEECH_PIPER_MODEL"} <= names
    assert len(names) >= 18


def test_every_speech_setting_the_code_reads_is_in_compose_and_in_the_example_file():
    compose, example = repo_file("compose.yaml"), repo_file(".env.example")
    for name in sorted(speech_names_in_code() - IMAGE_SETS):
        assert f"{name}:" in compose, f"{name} is read by the code but compose.yaml does not pass it on"
        assert f"\n{name}=" in example, f"{name} is read by the code but .env.example does not show it"


def test_compose_does_not_pass_what_the_code_does_not_read():
    """The other direction: a setting in compose that nothing reads is a setting that does nothing, and a person would change it and see no effect."""
    passed = set(re.findall(r"^\s+(STUDIO_SPEECH_[A-Z0-9_]+):", repo_file("compose.yaml"), flags=re.M))
    assert passed == speech_names_in_code() - IMAGE_SETS


def test_the_values_compose_passes_when_nothing_is_set_are_the_codes_own_defaults():
    passed = dict(re.findall(r"^\s+(STUDIO_SPEECH_[A-Z0-9_]+): \$\{[A-Z0-9_]+:-([^}]*)\}$", repo_file("compose.yaml"), flags=re.M))
    assert set(passed) == speech_names_in_code() - IMAGE_SETS
    from_compose, from_nothing = Settings.from_env(passed), Settings.from_env({})
    for field in (f for f in Settings.__dataclass_fields__ if f.startswith("speech_")):
        assert getattr(from_compose, field) == getattr(from_nothing, field), f"compose passes a different default for {field}"  # one set of defaults, the code's


def test_the_example_files_values_are_the_defaults_too():
    shown = dict(re.findall(r"^(STUDIO_SPEECH_[A-Z0-9_]+)=(.*)$", repo_file(".env.example"), flags=re.M))
    assert set(shown) == speech_names_in_code() - IMAGE_SETS
    parsed, defaults = Settings.from_env(shown), Settings.from_env({})
    for field in (f for f in Settings.__dataclass_fields__ if f.startswith("speech_")):
        assert getattr(parsed, field) == getattr(defaults, field), f".env.example shows a value for {field} that is not the default"


# ---------------------------------------------------------------- the Dockerfile and the requirement files
def requirement_pins(name: str) -> dict[str, str]:
    pins = {}
    for line in repo_file(f"backend/{name}").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            package, _, version = line.partition("==")
            pins[package.strip().lower()] = version.strip()
    return pins


def test_every_requirement_file_the_dockerfile_copies_for_speech_exists():
    docker = repo_file("Dockerfile")
    copied = set(re.findall(r"backend/(requirements-speech-[a-z0-9-]+\.txt)", docker))
    assert copied == {"requirements-speech-kokoro.txt", "requirements-speech-piper.txt", "requirements-speech-qwen3-extras.txt",
                      "requirements-speech-qwen3.txt", "requirements-speech-voxtral.txt"}
    for name in copied:
        assert (ROOT / "backend" / name).is_file(), name


def test_qwen3_tts_is_installed_in_a_folder_of_its_own_without_dependencies():
    docker = repo_file("Dockerfile")
    assert re.search(r"pip install --no-deps --target /opt/speech-libs/qwen3-tts -r /app/backend/requirements-speech-qwen3\.txt", docker)
    assert "ENV STUDIO_SPEECH_LIBS=/opt/speech-libs" in docker
    assert requirement_pins("requirements-speech-qwen3.txt") == {
        "qwen-tts": "0.1.1", "transformers": "4.57.3", "accelerate": "1.12.0", "huggingface-hub": "0.36.2", "tokenizers": "0.22.2"}  # what qwen-tts 0.1.1 pins, and what that needs


def test_the_folder_the_dockerfile_fills_is_the_folder_the_worker_puts_first_on_its_path(tmp_path):
    from studio.worker_client import worker_env

    settings = Settings.from_env({"STUDIO_SPEECH_LIBS": "/opt/speech-libs"})
    paths = worker_env(settings, "speech", "qwen3-tts")["PYTHONPATH"].split(":")
    assert paths[0] == "/opt/speech-libs/qwen3-tts"  # the engine's id, as the Dockerfile's folder is named
    assert "/opt/speech-libs/qwen3-tts" not in worker_env(settings, "speech", "kokoro")["PYTHONPATH"]  # and no other engine sees it


def test_every_other_speech_step_is_optional_and_none_replaces_nvidias_builds():
    docker = repo_file("Dockerfile")
    for step in ("kokoro", "piper", "qwen3-extras"):
        block = re.search(rf"RUN \(pip install -c /app/ngc-pins\.txt -r /app/backend/requirements-speech-{step}\.txt.*?\|\| echo \"NOTE:", docker, flags=re.S)
        assert block, f"the {step} step must be under NVIDIA's pins and print a note instead of failing the build"
    assert "ARG SPEECH_VOXTRAL=0" in docker and 'if [ "$SPEECH_VOXTRAL" = "1" ]' in docker  # Voxtral is off unless asked for
    assert "SPEECH_VOXTRAL: ${SPEECH_VOXTRAL:-0}" in repo_file("compose.yaml") and "\nSPEECH_VOXTRAL=0" in repo_file(".env.example")


def test_kokoros_requirements_carry_the_spacy_model_misaki_would_otherwise_download_at_run_time():
    text = repo_file("backend/requirements-speech-kokoro.txt")
    assert requirement_pins("requirements-speech-kokoro.txt") == {"kokoro": "0.9.4", "misaki[en]": "0.9.4"}
    assert re.search(r"^en_core_web_sm @ https://github\.com/explosion/spacy-models/releases/download/en_core_web_sm-3\.8\.0/en_core_web_sm-3\.8\.0-py3-none-any\.whl$", text, flags=re.M)


def test_pipers_requirement_is_the_version_whose_api_the_engine_was_written_against():
    assert requirement_pins("requirements-speech-piper.txt") == {"piper-tts": "1.8.0"}


def test_voxtrals_requirements_name_what_the_model_card_asks_for():
    text = repo_file("backend/requirements-speech-voxtral.txt")
    assert re.search(r"^vllm$", text, flags=re.M) and re.search(r"^vllm-omni>=0\.18\.0$", text, flags=re.M) and re.search(r"^mistral_common>=1\.10\.0$", text, flags=re.M)


def test_the_speech_steps_come_after_the_pins_exist_and_before_the_build_check_runs():
    docker = repo_file("Dockerfile")
    assert docker.index("ngc_pins.py") < docker.index("requirements-speech-kokoro.txt") < docker.index("python /app/docker/check_image.py")


# ---------------------------------------------------------------- the build-time check
@pytest.fixture(scope="module")
def check_image():
    spec = importlib.util.spec_from_file_location("check_image_for_tests", ROOT / "docker" / "check_image.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_image_for_tests"] = module
    spec.loader.exec_module(module)
    return module


def test_the_build_check_has_a_line_for_ffmpeg_and_one_for_every_real_engine(check_image):
    notes = check_image.speech_notes()
    assert notes[0].startswith("Speech: ") and "ffmpeg" in notes[0]
    assert [n.split(":")[1].strip() for n in notes[1:]] == list(registry.REAL_ENGINES)  # in the order the page lists them, none missing, none named by the check itself
    assert all(n.startswith("Speech: ") for n in notes)


def test_an_engine_that_cannot_run_is_a_note_with_its_reason_and_never_a_problem(check_image):
    notes = check_image.speech_notes()
    assert all("not available here" in n or "can run" in n for n in notes[1:])
    kokoro = next(n for n in notes if "kokoro" in n)
    assert "Kokoro's libraries are not installed" in kokoro  # the engine's own words, as its pill will show them


def test_the_build_check_asks_each_engine_in_a_process_with_that_engines_libraries_first(check_image, monkeypatch, tmp_path):
    seen = []
    real_run = check_image.subprocess.run

    def spy(command, *args, **kwargs):
        if len(command) > 3 and command[1] == "-c" and "probe_engine" in command[2]:
            seen.append((command[3], kwargs["env"]["PYTHONPATH"].split(":")[0]))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(check_image.subprocess, "run", spy)
    monkeypatch.setenv("STUDIO_SPEECH_LIBS", str(tmp_path / "speech-libs"))
    check_image.speech_notes()
    assert seen == [(engine, str(tmp_path / "speech-libs" / engine)) for engine in registry.REAL_ENGINES]
