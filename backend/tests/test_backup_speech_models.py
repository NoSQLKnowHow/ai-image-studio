"""The backup scripts' list of speech models stays in step with the engines (DESIGN.md section 38.18, item 70).

`scripts/backup.sh` has to know which Hugging Face repositories each speech engine downloads, and it is a shell script: it cannot import the engines. So their names are
written a second time in `scripts/_backup_common.sh`, as the studio's defaults for the image and music models are. A name that changes on one side only would give a backup
that quietly leaves a model out (or looks for one that is not used), and nobody would notice until a restore. These tests read the shell file's values and the engines'
own constants, and run the shell function that turns an engine's id into the name of its setting, so that a change on one side fails here.

What they do not decide is whether a model is in the cache or what its folder holds: that is the backup tests' (`scripts/tests/backup_restore_test.sh`).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from studio.config import Settings
from studio.speech.engines import kokoro, piper, qwen3, voxtral
from studio.speech.registry import REAL_ENGINES

ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "scripts" / "_backup_common.sh"


def _value(name: str) -> str:
    """What `scripts/_backup_common.sh` assigns to NAME (a plain NAME="..." line at the left margin, as the file writes them all)."""
    found = re.findall(rf'^{name}="([^"]*)"', COMMON.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert len(found) == 1, f"{COMMON.name} should assign {name} exactly once, and has {len(found)} lines that do"
    return found[0]


def _shell(snippet: str, env: dict[str, str] | None = None) -> str:
    """Run a bit of shell with `_backup_common.sh` sourced, as backup.sh does, and return what it printed."""
    ran = subprocess.run(["bash", "-c", f'. "{COMMON}"; {snippet}'], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **(env or {})}, timeout=30)
    assert ran.returncode == 0, ran.stderr
    return ran.stdout


def test_the_engines_the_backup_looks_at_are_the_engines_there_are():
    assert set(_value("SPEECH_ENGINES").split()) == set(REAL_ENGINES)


def test_qwen3_tts_is_three_checkpoints_under_one_namespace():
    assert _value("SPEECH_QWEN3_NAMESPACE") == qwen3.NAMESPACE
    # in the order the backup lists them: the presets, a designed voice, a cloned one; and no checkpoint the engine has that the backup does not know
    in_order = [qwen3.CHECKPOINTS[variant] for variant in ("custom", "design", "clone")]
    assert _value("SPEECH_QWEN3_CHECKPOINTS").split() == in_order
    assert set(qwen3.CHECKPOINTS.values()) == set(in_order)


@pytest.mark.parametrize("variable, constant", [
    ("DEFAULT_SPEECH_KOKORO", kokoro.REPO),
    ("DEFAULT_SPEECH_PIPER", piper.REPO),
    ("DEFAULT_SPEECH_VOXTRAL", voxtral.MODEL),
])
def test_each_other_engines_default_repository_is_the_one_the_engine_downloads(variable, constant):
    assert _value(variable) == constant


@pytest.mark.parametrize("engine", sorted(REAL_ENGINES))
def test_the_setting_the_backup_reads_is_the_setting_the_studio_reads(engine):
    """`STUDIO_SPEECH_<ID>_MODEL`, the id in capitals with its hyphens as underscores. The studio's Settings reads it for the engine; the shell function must read the same one."""
    variable = "STUDIO_SPEECH_" + engine.upper().replace("-", "_") + "_MODEL"
    assert Settings.from_env({variable: "chosen/model"}).speech_model_for(engine) == "chosen/model"   # the studio's side
    assert _shell(f"speech_setting {engine}", {variable: "chosen/model"}) == "chosen/model"          # the backup's side
    assert _shell(f"speech_setting {engine}") == ""                                                   # and nothing set is empty, which means the engine's own


@pytest.mark.parametrize("repo", [qwen3.NAMESPACE + "/" + name for name in qwen3.CHECKPOINTS.values()] + [kokoro.REPO, piper.REPO, voxtral.MODEL])
def test_a_repository_is_looked_for_where_the_hugging_face_cache_keeps_it(repo):
    """`hub/models--Org--Name`: how huggingface_hub lays out its cache, and the only shape `valid_model_path` accepts. Every repository the engines use must be one."""
    path = _shell(f'hub_path "{repo}"')
    assert path == "hub/models--" + repo.replace("/", "--")
    assert _shell(f'valid_model_path "{path}" && echo yes') == "yes\n", f"{path} would be refused by a restore"


def test_the_setting_that_names_a_place_on_disk_is_told_from_one_that_names_a_repository():
    """The backup can only hold what is in the cache, so a folder is a warning and a repository is followed. The same four shapes `hf_model_path` knows, and `~`."""
    for on_disk in ("/models/mine", "./mine", "../mine", "~/mine"):
        assert _shell(f'on_disk "{on_disk}" && echo disk') == "disk\n", on_disk
    for named in ("myorg", "myorg/model", "Qwen", ""):
        assert _shell(f'on_disk "{named}" || echo name') == "name\n", named
