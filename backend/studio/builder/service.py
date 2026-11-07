"""What the Prompt builder's routes share (DESIGN.md §37.4): the settings, the Ollama client, and what was last found out about the server.

The page asks "is it set up, is it reachable, does it have my model, can it look at pictures" often (every time the tab opens, and in the
capabilities). Asking Ollama each time would put a network call behind every page load, so the answer is kept for a few seconds, and the capabilities
use only the answer that is already known and never wait on the network.

With `STUDIO_BUILDER_LLM=fake` there is no Ollama at all: the service reports a scripted model that is always there. That is for the tests of the page,
as `STUDIO_PIPELINE=fake` is for the image model.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import Settings
from .ollama import OllamaClient, OllamaError, model_present

STATUS_TTL_S = 10.0  # how long a status answer is reused
FAKE_MODEL = "fake-builder"
NOT_SET_UP = "The Prompt builder needs an Ollama server. Set STUDIO_OLLAMA_URL and STUDIO_OLLAMA_MODEL in .env, then restart."


@dataclass
class Probe:
    """What a look at the Ollama server found. Never holds the address."""

    reachable: bool = False
    version: str = ""
    models: list[str] = field(default_factory=list)
    model_present: bool = False
    vision: bool = False
    vision_model_present: bool = False
    context_length: Optional[int] = None
    error: Optional[OllamaError] = None


class BuilderService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.fake = settings.builder_llm == "fake"
        self.client: Optional[OllamaClient] = None
        if not self.fake and settings.builder_configured:
            self.client = OllamaClient(settings.ollama_url, timeout_s=settings.ollama_timeout_s, max_reply_s=settings.ollama_max_reply_s,
                                       keep_alive=settings.ollama_keep_alive, num_ctx=settings.ollama_num_ctx)
        self._lock = threading.Lock()
        self._last: Optional[tuple[float, Probe]] = None

    # ------------------------------------------------------------------ what is set up
    @property
    def configured(self) -> bool:
        return self.settings.builder_configured

    @property
    def model(self) -> str:
        return FAKE_MODEL if self.fake else self.settings.ollama_model

    def think(self, override: Optional[bool] = None) -> Optional[bool]:
        """The `think` value to send with a chat call (DESIGN.md §37.4, criterion 253): a message's own Think switch wins; otherwise the setting
        (`on` sends true, `off` sends false, `model` sends nothing and leaves it to the model, which is None here)."""
        if override is not None:
            return override
        return {"on": True, "off": False}.get(self.settings.ollama_think)

    # ------------------------------------------------------------------ looking at the server
    def _look(self) -> Probe:
        """Ask Ollama (blocking, a few short calls). A failure is the answer, not an exception."""
        if self.fake:
            return Probe(reachable=True, version="fake", models=[FAKE_MODEL], model_present=True, vision=False)
        assert self.client is not None
        try:
            version = self.client.version()
            models = self.client.models()
        except OllamaError as exc:
            return Probe(reachable=False, error=exc)
        present = model_present(models, self.settings.ollama_model)
        shown = self.client.show(self.settings.ollama_model) if present else None
        vision_model = self.settings.ollama_vision_model
        vision_present = bool(vision_model) and model_present(models, vision_model)
        # a vision model of its own, if it is there, or the main model if Ollama says it reports the ability (a hint: §37.1)
        vision = vision_present or bool(shown and "vision" in shown["capabilities"])
        return Probe(reachable=True, version=version, models=models, model_present=present, vision=vision, vision_model_present=vision_present,
                     context_length=shown["context_length"] if shown else None)

    def probe(self, *, fresh: bool = False) -> Probe:
        with self._lock:
            if not fresh and self._last is not None and time.monotonic() - self._last[0] < STATUS_TTL_S:
                return self._last[1]
        found = self._look()
        with self._lock:
            self._last = (time.monotonic(), found)
        return found

    def _words(self, found: Probe) -> tuple[Optional[str], Optional[str]]:
        """The message and the hint the page shows for what was found, or (None, None) when all is well."""
        if found.error is not None:
            return found.error.message, found.error.hint
        if not found.model_present:
            return (f"Ollama doesn't have the model {self.settings.ollama_model}.", f"Pull it on the Ollama machine: ollama pull {self.settings.ollama_model}")
        return None, None

    def status(self) -> dict[str, Any]:
        """`GET /api/builder/status`: {configured, reachable, model, vision, message, hint}."""
        if not self.configured:
            return {"configured": False, "reachable": False, "model": self.settings.ollama_model, "vision": False, "message": NOT_SET_UP, "hint": None}
        found = self.probe()
        message, hint = self._words(found)
        return {"configured": True, "reachable": found.reachable, "model": self.model, "vision": found.vision, "message": message, "hint": hint}

    def test(self) -> dict[str, Any]:
        """`POST /api/builder/test` (Test connection): what a fresh look found. The address is not in it."""
        found = self.probe(fresh=True)
        message, hint = self._words(found)
        return {
            "reachable": found.reachable, "version": found.version, "models": found.models, "model": self.model, "model_present": found.model_present,
            "vision": found.vision, "vision_model": self.settings.ollama_vision_model, "vision_model_present": found.vision_model_present,
            "context_length": found.context_length, "message": message, "hint": hint,
        }

    def cached(self) -> dict[str, Any]:
        """`builder` of the capabilities: only what is already known, never a call. `reachable` and `vision` are null until something has looked."""
        last = self._last[1] if self._last is not None else None
        return {
            "available": self.fake or bool(self.settings.ollama_url),
            "configured": self.configured,
            "reachable": None if last is None else last.reachable,
            "model": self.model,
            "vision": None if last is None else last.vision,
        }

    # ------------------------------------------------------------------ memory
    def unload(self) -> dict[str, Any]:
        """Ask Ollama to take its model(s) out of memory (§37.4 "Memory"). Only on the person's press: the studio never does this by itself (§37.6 choice 17)."""
        if self.fake:
            return {"ok": True, "unloaded": [FAKE_MODEL]}
        assert self.client is not None
        names = [self.settings.ollama_model]
        vision = self.settings.ollama_vision_model
        if vision and vision != self.settings.ollama_model:
            names.append(vision)
        done: list[str] = []
        for name in names:
            try:
                self.client.unload(name)
                done.append(name)
            except OllamaError as exc:
                # a model that is not there (or not loaded) is nothing to unload; a server that cannot be reached is a failure the person should see
                if exc.kind != "model_missing":
                    raise
        return {"ok": True, "unloaded": done}
