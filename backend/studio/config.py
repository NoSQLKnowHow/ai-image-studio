"""Server configuration, read from environment variables (DESIGN.md §13).

Every problem is collected and reported at once, so a misconfigured container
fails at start-up with one readable message instead of failing later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

PIPELINES = ("real", "fake")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}
HUB_MODES = ("auto", "offline", "online")  # STUDIO_LOCAL_FILES_ONLY: auto (cache first), true/offline, false/online (§26.11)


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
    fake_step_delay_ms: int = 30
    static_dir: Optional[Path] = None  # built web UI; None = <repo>/frontend/dist if present

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.sqlite"

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
            fake_step_delay_ms=integer("STUDIO_FAKE_STEP_DELAY_MS", cls.fake_step_delay_ms, 0, 10_000),
            static_dir=Path(env["STUDIO_STATIC_DIR"]).expanduser() if env.get("STUDIO_STATIC_DIR", "").strip() else None,
        )
        if settings.draft_size % 32:
            errors.append(f"STUDIO_DRAFT_SIZE={settings.draft_size} must be a multiple of 32 (the model needs it).")
        if errors:
            raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(errors))
        return settings
