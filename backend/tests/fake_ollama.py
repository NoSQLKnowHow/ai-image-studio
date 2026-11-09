"""A fake Ollama for the Prompt builder's tests (DESIGN.md §37.4, criterion 252): a small HTTP server in the test process that speaks the calls the
studio makes, and can be told to stream, stall, send invalid JSON, refuse, redirect, or die in the middle of a reply.

It records every request it gets (`requests`), so a test can say what the studio sent: the model, the messages, `think`, `keep_alive`, the context
size, the JSON schema in `format`. It answers like Ollama: the chat reply is one JSON object per line in a chunked HTTP response.

A reply is a SCRIPT: a list of steps. ("thinking", text) and ("content", text) send a piece; ("done",) ends the reply properly; ("sleep", seconds) waits;
("error", text) sends an error object in the middle of the stream; ("garbage",) sends a line that is not JSON; ("die",) drops the connection without
ending the stream; ("stall",) goes silent until the server is stopped. The default script is a one-word reply.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional

Step = tuple


class QuietServer(ThreadingHTTPServer):
    """A server that does not print a traceback when the studio hangs up in the middle of a reply (Stop, a time-out): that is what those tests do on purpose."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass


class FakeOllama:
    def __init__(self, models: tuple[str, ...] = ("qwen3.8:27b",), version: str = "0.99.0", capabilities: tuple[str, ...] = ("completion", "vision", "thinking", "tools"),
                 context_length: Optional[int] = 262144) -> None:
        self.models = list(models)
        self.version = version
        self.capabilities = list(capabilities)
        self.context_length = context_length
        self.script: list[Step] = [("content", "ok"), ("done",)]
        self.requests: list[dict[str, Any]] = []  # {"method", "path", "body"}
        self.unloaded: list[str] = []
        self.redirect_to: Optional[str] = None  # answer every call with a 302 to here
        self.chat_status: Optional[tuple[int, str]] = None  # answer /api/chat with this status and error text
        self.show_status: Optional[int] = None  # answer /api/show with this status
        self.closing = threading.Event()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # keep the test output clean
                pass

            def _send_json(self, status: int, obj: Any) -> None:
                data = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self) -> Any:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                owner.requests.append({"method": self.command, "path": self.path, "body": body})
                return body

            def _redirect(self) -> bool:
                if owner.redirect_to is None:
                    return False
                self.send_response(302)
                self.send_header("Location", owner.redirect_to)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return True

            def do_GET(self) -> None:  # noqa: N802 - the name http.server looks for
                self._record()
                if self._redirect():
                    return
                if self.path == "/api/version":
                    self._send_json(200, {"version": owner.version})
                elif self.path == "/api/tags":
                    self._send_json(200, {"models": [{"name": m, "model": m, "size": 1} for m in owner.models]})
                else:
                    self._send_json(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                body = self._record()
                if self._redirect():
                    return
                if self.path == "/api/show":
                    if owner.show_status:
                        self._send_json(owner.show_status, {"error": "no"})
                    elif (body or {}).get("model") not in owner.models:
                        self._send_json(404, {"error": f"model '{(body or {}).get('model')}' not found"})
                    else:
                        info = {"general.architecture": "qwen", "qwen.context_length": owner.context_length} if owner.context_length else {}
                        self._send_json(200, {"capabilities": owner.capabilities, "model_info": info})
                elif self.path == "/api/chat":
                    self._chat(body or {})
                else:
                    self._send_json(404, {"error": "not found"})

            def _chunk(self, obj: Any) -> None:
                line = (obj if isinstance(obj, bytes) else json.dumps(obj).encode("utf-8")) + b"\n"
                self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                self.wfile.flush()

            def _chat(self, body: dict[str, Any]) -> None:
                if owner.chat_status:
                    self._send_json(owner.chat_status[0], {"error": owner.chat_status[1]})
                    return
                model = body.get("model")
                if model not in owner.models:
                    self._send_json(404, {"error": f"model '{model}' not found"})
                    return
                if body.get("messages") == [] and body.get("keep_alive") == 0:  # Ollama's documented way to unload a model
                    owner.unloaded.append(model)
                    self._send_json(200, {"model": model, "done": True, "done_reason": "unload"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    for step in list(owner.script):
                        kind = step[0]
                        if kind == "thinking":
                            self._chunk({"model": model, "message": {"role": "assistant", "content": "", "thinking": step[1]}, "done": False})
                        elif kind == "content":
                            self._chunk({"model": model, "message": {"role": "assistant", "content": step[1]}, "done": False})
                        elif kind == "done":
                            self._chunk({"model": model, "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop", "eval_count": 7})
                            self.wfile.write(b"0\r\n\r\n")
                            self.wfile.flush()
                        elif kind == "sleep":
                            time.sleep(step[1])
                        elif kind == "error":
                            self._chunk({"error": step[1]})
                        elif kind == "garbage":
                            self._chunk(b"this is not json")
                        elif kind == "die":
                            self.connection.shutdown(socket.SHUT_RDWR)
                            return
                        elif kind == "stall":
                            owner.closing.wait(60)
                            return
                    if not any(s[0] == "done" for s in owner.script):  # a script with no "done": end the stream properly, as a server that finished would
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return  # the studio hung up (Stop, or a time-out): the usual end of such a test

        self._server = QuietServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.host = "127.0.0.1"
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> "FakeOllama":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.closing.set()
        self._server.shutdown()
        self._server.server_close()

    def calls(self, path: str) -> list[dict[str, Any]]:
        """The requests made to `path`, oldest first."""
        return [r for r in self.requests if r["path"] == path]

    def __enter__(self) -> "FakeOllama":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
