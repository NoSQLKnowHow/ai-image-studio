"""The studio's client for Ollama (DESIGN.md §37.4).

Written on the Python standard library (`http.client`), so the container needs no new package. It speaks to the few calls of Ollama's API that the
Prompt builder uses: `GET /api/version`, `GET /api/tags`, `POST /api/show` (best-effort), `POST /api/chat` (streamed, one JSON object per line) and the same
call with no messages and `keep_alive` 0, which unloads the model.

Three rules from §37.3 shape it:
  * The address is the person's own and private (rule 12). It is never put in a message or a log line: every failure here is turned into a FIXED
    sentence, never into the text of the exception that caused it (which can name the host). It follows no redirect.
  * Silence, not total time, is the main limit (§37.6 choice 18): the socket's timeout is the time Ollama may say nothing, so a model that is loading or
    thinking (bytes keep arriving) is not cut off, and one that has gone quiet is. `max_reply_s` caps a whole reply.
  * A failure never damages anything (rule 10): it is an `OllamaError` with a `kind` the page can act on and a sentence for the person.

Every call here blocks, so the routes run them in a thread.
"""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # what is removed from text that comes from outside before it is shown
MAX_ERROR_CHARS = 300  # Ollama's own error text is shown, shortened
SHORT_CALL_S = 5.0  # the quick calls (version, tags, show, unload) must answer within this


class OllamaError(Exception):
    """Something went wrong with a call. `kind` is one of: unreachable, timeout, too_long, model_missing, out_of_memory, refused, bad_answer, stopped.
    `message` is a sentence for the person and `hint` what to try; neither contains the server's address."""

    def __init__(self, kind: str, message: str, hint: Optional[str] = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint


def clean_text(text: Any, limit: int = MAX_ERROR_CHARS) -> str:
    """Text from outside, made safe to show: no control characters, one line of whitespace, cut to `limit`."""
    return " ".join(_CONTROL.sub(" ", str(text)).split())[:limit]


def keep_alive_value(setting: str) -> Any:
    """`STUDIO_OLLAMA_KEEP_ALIVE` as Ollama takes it: a bare number is seconds (an integer), anything else (`5m`) a duration string."""
    return int(setting) if re.fullmatch(r"-?[0-9]+", setting) else setting


def model_present(models: list[str], wanted: str) -> bool:
    """Whether Ollama lists `wanted`. A name with no tag means `:latest`, as Ollama itself reads it."""
    return bool(wanted) and (wanted in models or (":" not in wanted and f"{wanted}:latest" in models))


def _classify(status: int, text: str, model: str) -> OllamaError:
    """Ollama's own error (an HTTP status and its `error` text) as an OllamaError the page can act on."""
    shown = clean_text(text) or f"Ollama answered with status {status}."
    lowered = shown.lower()
    if status == 404 or "not found" in lowered:
        return OllamaError("model_missing", f"Ollama doesn't have the model {model}.", f"Pull it on the Ollama machine: ollama pull {model}")
    if "memory" in lowered:
        return OllamaError("out_of_memory", f"Ollama ran out of memory: {shown}",
                           "Unload the image model (the Images tab) or Ollama's model, or ask for a smaller context window (STUDIO_OLLAMA_NUM_CTX).")
    return OllamaError("refused", shown)


@dataclass
class ChatPiece:
    """One thing that arrived in a reply: a piece of the model's `thinking`, a piece of its `content` (the reply), or the end (`done`, with Ollama's summary)."""

    kind: str  # "thinking" | "content" | "done"
    text: str = ""
    info: dict[str, Any] = field(default_factory=dict)


class OllamaClient:
    def __init__(self, base_url: str, *, timeout_s: float, max_reply_s: float, keep_alive: str, num_ctx: int) -> None:
        parts = urlsplit(base_url)
        self._https = parts.scheme == "https"
        self._host = parts.hostname or ""
        self._port = parts.port or (443 if self._https else 80)
        self.timeout_s = float(timeout_s)
        self.max_reply_s = float(max_reply_s)
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx

    # ------------------------------------------------------------------ plumbing
    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
        return cls(self._host, self._port, timeout=timeout)

    @staticmethod
    def _unreachable() -> OllamaError:
        return OllamaError("unreachable", "Can't reach the Ollama server.",
                           "Check that Ollama is running, and that the address in STUDIO_OLLAMA_URL works from inside the studio container (Test connection shows what was found).")

    def _json_call(self, method: str, path: str, body: Optional[dict[str, Any]] = None, *, model: str = "", timeout: float = SHORT_CALL_S) -> Any:
        """One short call and its JSON answer. Anything that goes wrong is an OllamaError with a fixed sentence."""
        conn = self._connection(timeout)
        try:
            payload = None if body is None else json.dumps(body).encode("utf-8")
            conn.request(method, path, body=payload, headers={"Content-Type": "application/json", "Accept": "application/json"})
            response = conn.getresponse()
            raw = response.read(2_000_000)
            if 300 <= response.status < 400:
                raise OllamaError("refused", "Ollama answered with a redirect, which the studio does not follow.",
                                  "Set STUDIO_OLLAMA_URL to the address Ollama itself answers on.")
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, ValueError):
                data = None
            if response.status >= 400:
                raise _classify(response.status, (data or {}).get("error", "") if isinstance(data, dict) else "", model)
            if data is None:
                raise OllamaError("bad_answer", "Ollama sent something the studio could not read.")
            return data
        except OllamaError:
            raise
        except (socket.timeout, TimeoutError):
            raise OllamaError("timeout", f"Ollama did not answer within {int(timeout)} seconds.",
                              "It may be busy loading a model. Try again in a moment.") from None
        except (OSError, http.client.HTTPException):
            raise self._unreachable() from None
        finally:
            conn.close()

    # ------------------------------------------------------------------ the quick calls
    def version(self) -> str:
        data = self._json_call("GET", "/api/version")
        return clean_text(data.get("version", "")) if isinstance(data, dict) else ""

    def models(self) -> list[str]:
        """The names of the models Ollama has (`GET /api/tags`)."""
        data = self._json_call("GET", "/api/tags")
        listed = data.get("models", []) if isinstance(data, dict) else []
        return [clean_text(m.get("name") or m.get("model") or "", 200) for m in listed if isinstance(m, dict) and (m.get("name") or m.get("model"))]

    def show(self, model: str) -> Optional[dict[str, Any]]:
        """What Ollama says about a model, best-effort (§37.1: a hint, never a fact the studio depends on): `{capabilities: [...], context_length: int|None}`,
        or None when it cannot say (an older Ollama, or the model is not there)."""
        try:
            data = self._json_call("POST", "/api/show", {"model": model}, model=model)
        except OllamaError:
            return None
        if not isinstance(data, dict):
            return None
        capabilities = [clean_text(c, 40) for c in data.get("capabilities", []) if isinstance(c, str)] if isinstance(data.get("capabilities"), list) else []
        context = None
        info = data.get("model_info")
        if isinstance(info, dict):
            for key, value in info.items():
                if isinstance(key, str) and key.endswith(".context_length") and isinstance(value, int) and not isinstance(value, bool):
                    context = value
                    break
        return {"capabilities": capabilities, "context_length": context}

    def unload(self, model: str) -> None:
        """Ask Ollama to take `model` out of memory now: a chat with no messages and `keep_alive` 0 (Ollama's documented way)."""
        self._json_call("POST", "/api/chat", {"model": model, "messages": [], "keep_alive": 0}, model=model)

    # ------------------------------------------------------------------ a reply
    def chat(self, model: str, messages: list[dict[str, Any]], *, think: Optional[bool] = None, schema: Optional[dict[str, Any]] = None) -> "ChatCall":
        """Start a reply. Nothing is sent until the result is iterated; `cancel()` on it stops it, from any thread."""
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": True, "keep_alive": keep_alive_value(self.keep_alive), "options": {"num_ctx": self.num_ctx}}
        if think is not None:  # `STUDIO_OLLAMA_THINK=model` sends nothing and leaves it to the model
            body["think"] = think
        if schema is not None:
            body["format"] = schema
        return ChatCall(self, model, body)


class ChatCall:
    """One streamed reply. Iterate it for `ChatPiece`s. It can be cancelled from another thread (Stop), which closes the connection and ends the
    iteration with an OllamaError of kind "stopped"."""

    def __init__(self, client: OllamaClient, model: str, body: dict[str, Any]) -> None:
        self._client = client
        self._model = model
        self._body = body
        self._conn: Optional[http.client.HTTPConnection] = None
        self._stopped = threading.Event()
        self._lock = threading.Lock()

    def cancel(self) -> None:
        """Stop the reply, from any thread. Shutting the socket down is what wakes a thread that is blocked reading from it. The connection itself is
        NOT closed here: closing it under the reading thread makes `http.client` fail in odd ways (an AttributeError from inside its chunk reader); the
        reading thread closes it when it ends, in `__iter__`."""
        self._stopped.set()
        with self._lock:
            conn = self._conn
        if conn is not None:
            try:
                if conn.sock is not None:
                    conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def __iter__(self) -> Iterator[ChatPiece]:
        client = self._client
        started = time.monotonic()
        conn = client._connection(client.timeout_s)  # the socket timeout is the silence limit
        with self._lock:
            self._conn = conn
        try:
            if self._stopped.is_set():
                raise OllamaError("stopped", "The reply was stopped.")
            conn.request("POST", "/api/chat", body=json.dumps(self._body).encode("utf-8"), headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"})
            response = conn.getresponse()
            if 300 <= response.status < 400:
                raise OllamaError("refused", "Ollama answered with a redirect, which the studio does not follow.", "Set STUDIO_OLLAMA_URL to the address Ollama itself answers on.")
            if response.status >= 400:
                try:
                    error = json.loads(response.read(100_000).decode("utf-8", "replace")).get("error", "")
                except (ValueError, AttributeError):
                    error = ""
                raise _classify(response.status, error, self._model)
            finished = False
            while True:
                line = response.readline()
                if self._stopped.is_set():
                    raise OllamaError("stopped", "The reply was stopped.")
                if not line:
                    break
                if time.monotonic() - started > client.max_reply_s:
                    raise OllamaError("too_long", f"The reply took longer than {int(client.max_reply_s)} seconds, so it was stopped.",
                                      "Try again with Think off for a quicker reply, or raise STUDIO_OLLAMA_MAX_REPLY_S.")
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    raise OllamaError("bad_answer", "Ollama sent something the studio could not read.") from None
                if not isinstance(obj, dict):
                    raise OllamaError("bad_answer", "Ollama sent something the studio could not read.")
                if obj.get("error"):  # an error that came mid-stream, after the 200
                    raise _classify(500, obj.get("error"), self._model)
                message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                thinking, content = message.get("thinking"), message.get("content")
                if isinstance(thinking, str) and thinking:
                    yield ChatPiece("thinking", thinking)
                if isinstance(content, str) and content:
                    yield ChatPiece("content", content)
                if obj.get("done"):
                    finished = True
                    yield ChatPiece("done", info={k: obj[k] for k in ("done_reason", "eval_count", "prompt_eval_count", "total_duration") if k in obj})
                    break
            if not finished:
                raise OllamaError("bad_answer", "Ollama's reply ended before it was finished.", "Try again.")
        except OllamaError:
            raise
        except (socket.timeout, TimeoutError):
            if self._stopped.is_set():
                raise OllamaError("stopped", "The reply was stopped.") from None
            raise OllamaError("timeout", f"Ollama said nothing for {int(client.timeout_s)} seconds, so the reply was given up.",
                              "It may be out of memory or stuck. Try again, or raise STUDIO_OLLAMA_TIMEOUT_S if the model is slow to load.") from None
        except AttributeError:  # http.client lost its connection under it (a Stop that came at the wrong moment): not a bug of ours, not a failure of Ollama's
            if self._stopped.is_set():
                raise OllamaError("stopped", "The reply was stopped.") from None
            raise
        except http.client.IncompleteRead:  # the stream was cut off: Ollama (or what is between us) died in the middle of the reply
            if self._stopped.is_set():
                raise OllamaError("stopped", "The reply was stopped.") from None
            raise OllamaError("bad_answer", "Ollama's reply ended before it was finished.", "Try again.") from None
        except (OSError, http.client.HTTPException, ValueError):
            if self._stopped.is_set():
                raise OllamaError("stopped", "The reply was stopped.") from None
            raise client._unreachable() from None
        finally:
            with self._lock:
                self._conn = None
            conn.close()
