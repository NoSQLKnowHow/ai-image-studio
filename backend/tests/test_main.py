"""`python -m studio` as a real process: start-up failures, and a quiet Ctrl-C."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def start(tmp_path: Path, **env_extra: str) -> tuple[subprocess.Popen, int]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, PYTHONPATH=str(BACKEND), STUDIO_PIPELINE="fake", STUDIO_HOST="127.0.0.1",
               STUDIO_PORT=str(port), STUDIO_DATA_DIR=str(tmp_path / "data"))
    env.update(env_extra)
    proc = subprocess.Popen([sys.executable, "-m", "studio"], env=env, cwd=tmp_path,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc, port


def test_ctrl_c_shuts_down_quietly_with_exit_code_0(tmp_path):
    proc, port = start(tmp_path)
    deadline = time.monotonic() + 20
    while True:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1).read()
            break
        except OSError:
            assert proc.poll() is None and time.monotonic() < deadline, proc.stdout.read() if proc.stdout else ""
            time.sleep(0.1)
    proc.send_signal(signal.SIGINT)
    output, _ = proc.communicate(timeout=20)
    assert proc.returncode == 0, output
    assert "Traceback" not in output and "Finished server process" in output


def test_startup_failure_exits_3_with_a_clear_message(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    proc, _ = start(tmp_path, STUDIO_DATA_DIR=str(blocker / "data"))
    output, _ = proc.communicate(timeout=30)
    assert proc.returncode == 3, output
    assert "is not writable" in output


def test_bad_configuration_exits_2_before_starting(tmp_path):
    proc, _ = start(tmp_path, STUDIO_QUEUE_CAP="lots")
    output, _ = proc.communicate(timeout=30)
    assert proc.returncode == 2 and "STUDIO_QUEUE_CAP" in output and "Uvicorn running" not in output
