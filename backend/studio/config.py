"""Server configuration, read from environment variables (DESIGN.md §13).

Every problem is collected and reported at once, so a misconfigured container
fails at start-up with one readable message instead of failing later.
"""

from __future__ import annotations

import os
import shlex
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

PIPELINES = ("real", "fake")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}
UPSCALER_DEVICES = ("auto", "cuda", "cpu")  # STUDIO_UPSCALER_DEVICE (DESIGN.md §28.3)
HUB_MODES = ("auto", "offline", "online")  # STUDIO_LOCAL_FILES_ONLY: auto (cache first), true/offline, false/online (§26.11)
OLLAMA_THINK_MODES = ("on", "off", "model")  # STUDIO_OLLAMA_THINK (DESIGN.md §37.4): send think:true, think:false, or nothing
BUILDER_LLMS = ("real", "fake")  # STUDIO_BUILDER_LLM: the real Ollama, or the scripted model the page's tests use
_KEEP_ALIVE = re.compile(r"^-?[0-9]+(s|m|h)?$")  # what Ollama's keep_alive takes that the studio allows: seconds, or a number with s, m or h


class ConfigError(Exception):
    """The environment configuration is invalid. The message lists every problem."""


@dataclass(frozen=True)
class Settings:
    model: str = "Qwen/Qwen-Image-2.1"
    data_dir: Path = Path("/data")
    pipeline: str = "real"
    host: str = "0.0.0.0"
    port: int = 8080
    allowed_hosts: tuple[str, ...] = ()  # empty = accept any Host header
    idle_timeout_min: float = 30.0  # 0 = unload as soon as the queue is empty
    queue_cap: int = 10
    retention_days: int = 30
    bin_days: int = 30  # how long a deleted run stays in the bin (DESIGN.md §30); 0 = no bin: deleting is for good
    max_images_per_run: int = 8
    max_prompt_chars: int = 8000
    max_upload_mb: int = 20
    max_input_images: int = 4  # per edit; Qwen allows up to 10, raised only after measuring on the Spark
    upload_ttl_hours: int = 24  # how long an upload that no run has claimed is kept
    draft_size: int = 512  # a draft's long side in pixels (DESIGN.md §22.2)
    draft_steps: int = 12  # the most steps a draft may use
    edit_warn_units: int = 8  # an edit costing more "units" (images x (resolution/1024)^2) gets a warning; 0 = never (DESIGN.md §21.4)
    min_free_gb: Optional[float] = None  # None = memory pre-flight check off (set after measuring, M2)
    cpu_offload: bool = False
    hub_mode: str = "auto"  # where model files come from (DESIGN.md §26.11): "auto" cache first, "offline" never online, "online" as before
    music_model: str = "MiniMaxAI/MiniMax-Music3"
    music_min_free_gb: Optional[float] = None  # like min_free_gb, for the music model (about 24 GB loaded)
    music_max_seconds: int = 300  # the longest `duration` a run may ask for (the model's own limit is 360)
    music_max_tracks: int = 4  # versions per music run
    music_libs: Optional[Path] = None  # a folder put first on the music worker's Python path (the image's own diffusers 0.40.0)
    upscaler_model: Optional[Path] = None  # Enlarge's model file (DESIGN.md §28.3); None = $HF_HOME/upscalers/RealESRGAN_x2plus.pth
    upscaler_device: str = "auto"  # where Enlarge runs: auto (the GPU if there is one), cuda, or cpu
    # The command that cleans up one drawing into an SVG (DESIGN.md §31.12.4): the program and its first arguments, to which the server adds `--source`,
    # `--options` and `--out`. Empty means the engine's own, `python -m studio.vector.job`. It exists so that the server's tests run a fake job and need
    # no engine; STUDIO_VECTOR_JOB_COMMAND is the same thing for a person (split like a shell command line).
    vector_job_command: tuple[str, ...] = ()
    # The Prompt builder (DESIGN.md §37.4). The address of the person's Ollama is read from their own `.env` and from nowhere else; it is kept out of
    # `repr` (so that a settings object that finds its way into a log line does not carry it) and is never put in a message the server sends (§37.3 rule 12).
    ollama_url: str = field(default="", repr=False)  # "" = the Prompt builder is not set up
    ollama_model: str = ""  # the model that writes the prompts; "" = not set up
    ollama_vision_model: str = ""  # the model used when a picture is attached; "" = the main model, if it reports it can look at pictures
    ollama_keep_alive: str = "5m"  # sent on every request: how long Ollama keeps its model in memory afterwards ("0" = unload at once)
    ollama_num_ctx: int = 32768  # the context window asked for, in tokens (§37.6 choice 19)
    ollama_think: str = "on"  # on: think:true, off: think:false, model: send nothing (§37.6 choice 14)
    ollama_timeout_s: int = 120  # how long Ollama may stay silent before a reply fails (§37.6 choice 18)
    ollama_max_reply_s: int = 900  # the longest one reply may take altogether
    builder_at_once: int = 1  # replies written at the same time (§37.3 rule 9)
    builder_llm: str = "real"  # "fake" = the scripted model of the tests (§37.4)
    # Speech: text read aloud (DESIGN.md §38.10). Engines are named by their ids, which only the speech registry knows, so a model path is looked up by id.
    speech_engines: tuple[str, ...] = ()  # the engines to offer (STUDIO_SPEECH_ENGINES); empty = every one the registry has
    speech_libs: Optional[Path] = None  # a folder holding one folder of libraries per engine, put first on that engine's worker path (§38.3)
    speech_models: tuple[tuple[str, str], ...] = ()  # (engine id, model id or path) from STUDIO_SPEECH_<ID>_MODEL; an engine not listed uses its own default
    speech_min_free_gb: Optional[float] = None  # the memory check before a speech engine loads; None = the engine's own estimate (real pipeline only)
    speech_gpu_fraction: float = 0.2  # the share of GPU memory an engine that runs a server of its own may let that server take (§38.3)
    speech_max_chars: int = 1_500_000  # the longest text one reading may hold, about 250,000 words
    speech_part_max_chars: int = 30_000  # the longest part of a reading, about 35 minutes of audio
    speech_max_upload_mb: int = 50  # the biggest .txt, .md, .pdf, .epub or .docx file
    speech_fetch_max_mb: int = 5  # the biggest web page
    speech_fetch_timeout_s: int = 20  # the time allowed for each step of fetching a web page (60 seconds in all)
    speech_pause_ms: int = 450  # the pause after a paragraph; a heading's is twice that
    speech_voice_min_seconds: int = 8  # how short and how long a recording of a person's voice may be
    speech_voice_max_seconds: int = 60
    fake_step_delay_ms: int = 30
    static_dir: Optional[Path] = None  # built web UI; None = <repo>/frontend/dist if present

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.sqlite"

    @property
    def builder_configured(self) -> bool:
        """Whether the Prompt builder has what it needs to talk to a model: an address and a model name, or the scripted model of the tests."""
        return self.builder_llm == "fake" or bool(self.ollama_url and self.ollama_model)

    def speech_model_for(self, engine_id: str) -> str:
        """The model the person chose for a speech engine (STUDIO_SPEECH_<ID>_MODEL), or "" for the engine's own default."""
        return dict(self.speech_models).get(engine_id, "")

    @property
    def upscaler_model_path(self) -> Path:
        """The upscaler model file Enlarge uses: STUDIO_UPSCALER_MODEL, else RealESRGAN_x2plus.pth in the `upscalers` folder of the
        Hugging Face cache (the folder the container sees as /models, where the probe looks too)."""
        if self.upscaler_model is not None:
            return self.upscaler_model
        return Path(os.environ.get("HF_HOME") or "~/.cache/huggingface").expanduser() / "upscalers" / "RealESRGAN_x2plus.pth"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        env = os.environ if env is None else env
        errors: list[str] = []

        def text(name: str, default: str) -> str:
            value = env.get(name, default).strip()
            if not value:
                errors.append(f"{name} must not be empty.")
                return default
            return value

        def integer(name: str, default: int, lo: int, hi: int) -> int:
            raw = env.get(name)
            if raw is None or raw.strip() == "":
                return default
            try:
                value = int(raw.strip())
            except ValueError:
                errors.append(f"{name}={raw!r} is not a whole number.")
                return default
            if not lo <= value <= hi:
                errors.append(f"{name}={value} is out of range ({lo}-{hi}).")
                return default
            return value

        def boolean(name: str, default: bool) -> bool:
            raw = env.get(name)
            if raw is None or raw.strip() == "":  # like the others: `KEY=` in a .env file means "default"
                return default
            value = raw.strip().lower()
            if value in _TRUE:
                return True
            if value in _FALSE:
                return False
            errors.append(f"{name}={raw!r} is not a boolean (use true/false).")
            return default

        def hub(name: str, default: str) -> str:
            raw = env.get(name)
            if raw is None or raw.strip() == "":
                return default
            value = raw.strip().lower()
            if value == "auto":
                return "auto"
            if value in _TRUE:
                return "offline"
            if value in _FALSE:
                return "online"
            errors.append(f"{name}={raw!r} must be auto, true or false.")
            return default

        def number(name: str, default: float, lo: float, hi: float) -> float:
            raw = env.get(name)
            if raw is None or raw.strip() == "":
                return default
            try:
                value = float(raw.strip())
            except ValueError:
                errors.append(f"{name}={raw!r} is not a number.")
                return default
            if not lo <= value <= hi:
                errors.append(f"{name}={value:g} is out of range ({lo:g}-{hi:g}).")
                return default
            return value

        def optional_float(name: str, lo: float) -> Optional[float]:
            raw = env.get(name)
            if raw is None or raw.strip() == "":
                return None
            try:
                value = float(raw.strip())
            except ValueError:
                errors.append(f"{name}={raw!r} is not a number.")
                return None
            if not value >= lo:
                errors.append(f"{name}={value} must be at least {lo}.")
                return None
            return value

        upscaler_device = env.get("STUDIO_UPSCALER_DEVICE", "auto").strip().lower() or "auto"
        if upscaler_device not in UPSCALER_DEVICES:
            errors.append(f"STUDIO_UPSCALER_DEVICE={upscaler_device!r} must be one of: {', '.join(UPSCALER_DEVICES)}.")
            upscaler_device = "auto"

        try:
            vector_job_command = tuple(shlex.split(env.get("STUDIO_VECTOR_JOB_COMMAND", "")))
        except ValueError as exc:  # an unclosed quote
            errors.append(f"STUDIO_VECTOR_JOB_COMMAND is not a command line ({exc}).")
            vector_job_command = ()

        # The Prompt builder's settings. A bad address is reported WITHOUT echoing it (the address is private: DESIGN.md §37.3 rule 12).
        ollama_url = env.get("STUDIO_OLLAMA_URL", "").strip().rstrip("/")
        if ollama_url:
            try:
                parts = urlsplit(ollama_url)
                shape_ok = parts.scheme in ("http", "https") and bool(parts.hostname) and parts.path == "" and not parts.query and not parts.fragment
                shape_ok = shape_ok and parts.username is None and parts.password is None
                parts.port  # noqa: B018 - raises ValueError for a port that is not a number or out of range
            except ValueError:
                shape_ok = False
            if not shape_ok:
                errors.append("STUDIO_OLLAMA_URL must look like http://host:11434 (a web address with no path, user name or password).")
                ollama_url = ""
        keep_alive = env.get("STUDIO_OLLAMA_KEEP_ALIVE", "").strip().lower() or cls.ollama_keep_alive
        if not _KEEP_ALIVE.match(keep_alive):
            errors.append("STUDIO_OLLAMA_KEEP_ALIVE must be a number of seconds, or a number followed by s, m or h (for example 5m); 0 unloads at once.")
            keep_alive = cls.ollama_keep_alive
        ollama_think = env.get("STUDIO_OLLAMA_THINK", "").strip().lower() or cls.ollama_think
        if ollama_think not in OLLAMA_THINK_MODES:
            errors.append(f"STUDIO_OLLAMA_THINK={ollama_think!r} must be one of: {', '.join(OLLAMA_THINK_MODES)}.")
            ollama_think = cls.ollama_think
        builder_llm = env.get("STUDIO_BUILDER_LLM", "").strip().lower() or cls.builder_llm
        if builder_llm not in BUILDER_LLMS:
            errors.append(f"STUDIO_BUILDER_LLM={builder_llm!r} must be one of: {', '.join(BUILDER_LLMS)}.")
            builder_llm = cls.builder_llm

        # Speech (DESIGN.md §38.10). A model path is set per engine by STUDIO_SPEECH_<ID>_MODEL, the id in capitals with its hyphens as underscores; any
        # such variable is accepted here, and the registry decides which engines exist, so this file never names one.
        speech_models = tuple(sorted(
            (match.group(1).lower().replace("_", "-"), value.strip())
            for name, value in env.items()
            if (match := re.fullmatch(r"STUDIO_SPEECH_([A-Z0-9_]+)_MODEL", name)) and value.strip()
        ))
        speech_engines = tuple(e.strip().lower() for e in env.get("STUDIO_SPEECH_ENGINES", "").split(",") if e.strip())
        if speech_engines:
            from .speech.registry import FAKE_ENGINES, REAL_ENGINES

            unknown = [e for e in speech_engines if e not in REAL_ENGINES and e not in FAKE_ENGINES]
            if unknown:
                errors.append(f"STUDIO_SPEECH_ENGINES names an engine that does not exist: {', '.join(unknown)} "
                              f"(the engines are: {', '.join(REAL_ENGINES)}).")
                speech_engines = tuple(e for e in speech_engines if e not in unknown)

        pipeline = env.get("STUDIO_PIPELINE", "real").strip().lower()
        if pipeline not in PIPELINES:
            errors.append(f"STUDIO_PIPELINE={pipeline!r} must be one of: {', '.join(PIPELINES)}.")
            pipeline = "real"

        allowed_hosts = tuple(
            h.strip().lower() for h in env.get("STUDIO_ALLOWED_HOSTS", "").split(",") if h.strip()
        )

        if env.get("STUDIO_TOKEN", "").strip():
            errors.append(
                "STUDIO_TOKEN is reserved for a future shared-password feature and is not implemented yet. "
                "Unset it; setting it would NOT protect the server."
            )

        settings = cls(
            model=text("STUDIO_MODEL", cls.model),
            data_dir=Path(text("STUDIO_DATA_DIR", str(cls.data_dir))).expanduser(),
            pipeline=pipeline,
            host=text("STUDIO_HOST", cls.host),
            port=integer("STUDIO_PORT", cls.port, 1, 65535),
            allowed_hosts=allowed_hosts,
            idle_timeout_min=number("STUDIO_IDLE_TIMEOUT_MIN", cls.idle_timeout_min, 0, 1440),
            queue_cap=integer("STUDIO_QUEUE_CAP", cls.queue_cap, 1, 1000),
            retention_days=integer("STUDIO_RETENTION_DAYS", cls.retention_days, 0, 36500),
            bin_days=integer("STUDIO_BIN_DAYS", cls.bin_days, 0, 36500),
            max_images_per_run=integer("STUDIO_MAX_IMAGES_PER_RUN", cls.max_images_per_run, 1, 64),
            max_prompt_chars=integer("STUDIO_MAX_PROMPT_CHARS", cls.max_prompt_chars, 100, 100_000),
            max_upload_mb=integer("STUDIO_MAX_UPLOAD_MB", cls.max_upload_mb, 1, 200),
            max_input_images=integer("STUDIO_MAX_INPUT_IMAGES", cls.max_input_images, 1, 10),
            upload_ttl_hours=integer("STUDIO_UPLOAD_TTL_HOURS", cls.upload_ttl_hours, 1, 720),
            draft_size=integer("STUDIO_DRAFT_SIZE", cls.draft_size, 256, 1024),
            draft_steps=integer("STUDIO_DRAFT_STEPS", cls.draft_steps, 1, 100),
            edit_warn_units=integer("STUDIO_EDIT_WARN_UNITS", cls.edit_warn_units, 0, 1000),
            min_free_gb=optional_float("STUDIO_MIN_FREE_GB", 0.0),
            cpu_offload=boolean("STUDIO_CPU_OFFLOAD", cls.cpu_offload),
            hub_mode=hub("STUDIO_LOCAL_FILES_ONLY", cls.hub_mode),
            music_model=text("STUDIO_MUSIC_MODEL", cls.music_model),
            music_min_free_gb=optional_float("STUDIO_MUSIC_MIN_FREE_GB", 0.0),
            music_max_seconds=integer("STUDIO_MUSIC_MAX_SECONDS", cls.music_max_seconds, 10, 360),
            music_max_tracks=integer("STUDIO_MUSIC_MAX_TRACKS", cls.music_max_tracks, 1, 8),
            music_libs=Path(env["STUDIO_MUSIC_LIBS"]).expanduser() if env.get("STUDIO_MUSIC_LIBS", "").strip() else None,
            upscaler_model=Path(env["STUDIO_UPSCALER_MODEL"]).expanduser() if env.get("STUDIO_UPSCALER_MODEL", "").strip() else None,
            upscaler_device=upscaler_device,
            vector_job_command=vector_job_command,
            ollama_url=ollama_url,
            ollama_model=env.get("STUDIO_OLLAMA_MODEL", "").strip(),
            ollama_vision_model=env.get("STUDIO_OLLAMA_VISION_MODEL", "").strip(),
            ollama_keep_alive=keep_alive,
            ollama_num_ctx=integer("STUDIO_OLLAMA_NUM_CTX", cls.ollama_num_ctx, 512, 1_048_576),
            ollama_think=ollama_think,
            ollama_timeout_s=integer("STUDIO_OLLAMA_TIMEOUT_S", cls.ollama_timeout_s, 5, 3600),
            ollama_max_reply_s=integer("STUDIO_OLLAMA_MAX_REPLY_S", cls.ollama_max_reply_s, 10, 7200),
            builder_at_once=integer("STUDIO_BUILDER_AT_ONCE", cls.builder_at_once, 1, 4),
            builder_llm=builder_llm,
            speech_engines=speech_engines,
            speech_libs=Path(env["STUDIO_SPEECH_LIBS"]).expanduser() if env.get("STUDIO_SPEECH_LIBS", "").strip() else None,
            speech_models=speech_models,
            speech_min_free_gb=optional_float("STUDIO_SPEECH_MIN_FREE_GB", 0.0),
            speech_gpu_fraction=number("STUDIO_SPEECH_GPU_FRACTION", cls.speech_gpu_fraction, 0.05, 0.9),
            speech_max_chars=integer("STUDIO_SPEECH_MAX_CHARS", cls.speech_max_chars, 1000, 20_000_000),
            speech_part_max_chars=integer("STUDIO_SPEECH_PART_MAX_CHARS", cls.speech_part_max_chars, 500, 200_000),
            speech_max_upload_mb=integer("STUDIO_SPEECH_MAX_UPLOAD_MB", cls.speech_max_upload_mb, 1, 500),
            speech_fetch_max_mb=integer("STUDIO_SPEECH_FETCH_MAX_MB", cls.speech_fetch_max_mb, 1, 100),
            speech_fetch_timeout_s=integer("STUDIO_SPEECH_FETCH_TIMEOUT_S", cls.speech_fetch_timeout_s, 2, 120),
            speech_pause_ms=integer("STUDIO_SPEECH_PAUSE_MS", cls.speech_pause_ms, 0, 3000),
            speech_voice_min_seconds=integer("STUDIO_SPEECH_VOICE_MIN_SECONDS", cls.speech_voice_min_seconds, 1, 120),
            speech_voice_max_seconds=integer("STUDIO_SPEECH_VOICE_MAX_SECONDS", cls.speech_voice_max_seconds, 5, 600),
            fake_step_delay_ms=integer("STUDIO_FAKE_STEP_DELAY_MS", cls.fake_step_delay_ms, 0, 10_000),
            static_dir=Path(env["STUDIO_STATIC_DIR"]).expanduser() if env.get("STUDIO_STATIC_DIR", "").strip() else None,
        )
        if settings.draft_size % 32:
            errors.append(f"STUDIO_DRAFT_SIZE={settings.draft_size} must be a multiple of 32 (the model needs it).")
        if settings.speech_voice_min_seconds >= settings.speech_voice_max_seconds:
            errors.append("STUDIO_SPEECH_VOICE_MIN_SECONDS must be smaller than STUDIO_SPEECH_VOICE_MAX_SECONDS.")
        if settings.speech_part_max_chars > settings.speech_max_chars:
            errors.append("STUDIO_SPEECH_PART_MAX_CHARS must not be larger than STUDIO_SPEECH_MAX_CHARS.")
        if settings.ollama_max_reply_s < settings.ollama_timeout_s:
            errors.append("STUDIO_OLLAMA_MAX_REPLY_S must not be smaller than STUDIO_OLLAMA_TIMEOUT_S (the cap on a whole reply is the longer of the two limits).")
        if errors:
            raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(errors))
        return settings
