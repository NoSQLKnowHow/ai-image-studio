"""The places that must agree about the port stay in step (DESIGN.md section 38.18, item 74).

There are two ports, and mixing them up is the classic mistake:

* the **container's own** port, 8080: what the server listens on inside the container (`Dockerfile`: `ENV STUDIO_PORT=8080` and `EXPOSE 8080`), what the health check asks
  (`docker/healthcheck.py`) and what the app's own default is (`Settings.port`). It is the RIGHT-hand number of the mapping in `compose.yaml`, and it does not change;
* the **Spark's** port, 8081 from 1.20: what you open in the browser. It is the LEFT-hand number of the mapping, set by `STUDIO_PORT` in `.env` with a default in
  `compose.yaml`; `.env.example` repeats that default, and so do the scripts that look for a running studio when `.env` does not say.

If `compose.yaml` ever passed `STUDIO_PORT` to the container too, setting it to 8081 would make the server listen on 8081 inside while the mapping still sent traffic to 8080,
and the page would be unreachable; so that is checked as well. None of these needs Docker to be checked: they are all text in the repository.
"""

from __future__ import annotations

import re
from pathlib import Path

from studio.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _one(pattern: str, relative: str) -> str:
    """The one match of `pattern` (with one group) in a file of the repository."""
    found = re.findall(pattern, _text(relative), flags=re.MULTILINE)
    assert len(found) == 1, f"{relative} should have exactly one match of {pattern!r}, and has {len(found)}"
    return found[0]


MAPPING = r'^\s*- "\$\{STUDIO_BIND:-0\.0\.0\.0\}:\$\{STUDIO_PORT:-(\d+)\}:(\d+)"'


def _mapping() -> tuple[str, str]:
    """(the Spark's default port, the container's port) from the `ports:` line of compose.yaml."""
    found = re.findall(MAPPING, _text("compose.yaml"), flags=re.MULTILINE)
    assert len(found) == 1, f"compose.yaml should publish exactly one port, as ${{STUDIO_BIND}}:${{STUDIO_PORT:-N}}:M, and has {len(found)} such lines"
    return found[0]


# ------------------------------------------------------------------ the container's own port

def test_the_container_side_of_the_mapping_is_what_the_image_listens_on():
    spark, container = _mapping()
    assert container == _one(r"^\s+STUDIO_PORT=(\d+) \\", "Dockerfile")      # the ENV the server reads inside the container
    assert container == _one(r"^EXPOSE (\d+)", "Dockerfile")
    assert container == _one(r'os\.environ\.get\("STUDIO_PORT", "(\d+)"\)', "docker/healthcheck.py")  # the health check runs inside it
    assert container == str(Settings.from_env({}).port)                       # and the app's own default is the same


def test_compose_does_not_hand_the_spark_port_to_the_container():
    """`STUDIO_PORT` in `.env` is the SPARK's port. If the container's environment got it too, the server would listen on it inside, where the mapping does not look."""
    lines = [l for l in _text("compose.yaml").splitlines() if "STUDIO_PORT" in l and not l.lstrip().startswith("#")]
    assert len(lines) == 1 and re.search(MAPPING, lines[0]), f"STUDIO_PORT should appear in compose.yaml only in the ports line, and appears in: {lines}"


# ------------------------------------------------------------------ the Spark's port

def test_the_spark_port_is_8081_in_every_place_that_repeats_it():
    spark, container = _mapping()
    assert spark == "8081" and spark != container
    assert spark == _one(r"^STUDIO_PORT=(\d+)\s*$", ".env.example")
    assert spark == _one(r"env_value STUDIO_PORT (\d+)\)", "scripts/_backup_common.sh")   # backup.sh and restore.sh find a running studio with it
    assert spark == _one(r'^URL="http://127\.0\.0\.1:(\d+)";', "scripts/edit_via_api.sh")
    assert spark == _one(r"\(default http://127\.0\.0\.1:(\d+)\)", "scripts/edit_via_api.sh")


def test_the_docs_that_say_what_to_open_on_the_spark_say_the_spark_port():
    spark, _ = _mapping()
    for relative in ("README.md", "docs/SPARK_TEST.md", "compose.yaml", ".env.example"):
        text = _text(relative)
        assert f"<spark-address>:{spark}" in text or relative == "docs/SPARK_TEST.md" and f"localhost:{spark}" in text, relative
        assert "<spark-address>:8080" not in text, f"{relative} still tells you to open the old port"
    # every curl in the Spark test runs on the Spark, against the published port
    assert "localhost:8080" not in _text("docs/SPARK_TEST.md")
    assert "localhost:8080" not in _text("README.md").split("## Development")[0], "the README's Spark sections still use the old port"


def test_the_scripts_default_matches_what_a_new_env_gets():
    """`cp .env.example .env` gives the port that compose.yaml would have used anyway, so a new install and an old one with the line deleted agree."""
    spark, _ = _mapping()
    assert spark == _one(r"^STUDIO_PORT=(\d+)", ".env.example")
