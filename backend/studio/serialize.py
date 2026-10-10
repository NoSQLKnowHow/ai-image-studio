"""JSON shapes returned by the API and pushed over server-sent events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from .fourk import plan_or_none


def utcnow() -> str:
    return format_ts(datetime.now(timezone.utc))


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_ts(moment: datetime) -> str:
    """The same shape as utcnow(), so timestamps compare correctly as text (the sweep relies on it)."""
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def four_k_fields(row: sqlite3.Row, base: str, copy: Optional[dict[str, int]]) -> dict[str, Any]:
    """What a picture says about Make 4K (DESIGN.md §27.3): `can_4k` is the server's rule for whether it is offered,
    `four_k_size` is what it would make (null if it is not offered), and `four_k` is the copy if one has been made (`copy`
    is its file's size in bytes and its width and height), else None. The same for a result and for an edit's source."""
    plan = plan_or_none(row["width"], row["height"])
    return {
        "can_4k": plan is not None,
        "four_k_size": None if plan is None else {"width": plan.out_width, "height": plan.out_height, "trimmed": plan.trimmed},
        "four_k": None if copy is None else {
            "width": copy["width"],
            "height": copy["height"],
            "bytes": copy["bytes"],
            "url": f"{base}/4k",
            "download_url": f"{base}/4k?download=1",
        },
    }


def image_payload(row: sqlite3.Row, four_k: Optional[dict[str, int]] = None) -> dict[str, Any]:
    base = f"/api/images/{row['id']}"
    return {
        "id": row["id"],
        "idx": row["idx"],
        "seed": row["seed"],
        "width": row["width"],
        "height": row["height"],
        "has_alpha": bool(row["has_alpha"]),
        "url": base,
        "thumb_url": f"{base}/thumb" if row["thumb_path"] else None,
        "download_url": f"{base}?download=1",
        **four_k_fields(row, base, four_k),
    }


def track_payload(row: sqlite3.Row) -> dict[str, Any]:
    base = f"/api/audio/{row['id']}"
    return {
        "id": row["id"],
        "idx": row["idx"],
        "seed": row["seed"],
        "seconds": row["seconds"],
        "sample_rate": row["sample_rate"],
        "channels": row["channels"],
        "bytes": row["bytes"],
        "url": base,
        "download_url": f"{base}?download=1",
    }


def input_payload(row: sqlite3.Row, four_k: Optional[dict[str, int]] = None) -> dict[str, Any]:
    """An image an edit was given: its place in the order the model sees them (1 = "image 1") and its role."""
    base = f"/api/images/{row['id']}"
    return {
        "position": row["position"],
        "role": row["role"],
        "id": row["id"],
        "width": row["width"],
        "height": row["height"],
        "has_alpha": bool(row["has_alpha"]),
        "url": base,
        "thumb_url": f"{base}/thumb" if row["thumb_path"] else None,
        **four_k_fields(row, base, four_k),
    }


def run_payload(
    row: sqlite3.Row,
    images: list[sqlite3.Row],
    progress: Optional[dict[str, Any]] = None,
    queue_position: Optional[int] = None,
    canceling: bool = False,
    expires_at: Optional[str] = None,
    inputs: Optional[list[sqlite3.Row]] = None,
    tracks: Optional[list[sqlite3.Row]] = None,
    four_k: Optional[dict[str, dict[str, int]]] = None,
) -> dict[str, Any]:
    error = None
    if row["error_message"]:
        error = {"message": row["error_message"], "hint": row["error_hint"]}
    return {
        "id": row["id"],
        "status": row["status"],
        "mode": row["mode"],
        "prompt": row["prompt"],
        "effective_prompt": row["effective_prompt"],
        "options": json.loads(row["options_json"]),
        "model_id": row["model_id"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "error": error,
        "pinned": bool(row["pinned"]),
        "expires_at": expires_at,
        "queue_position": queue_position if row["status"] == "queued" else None,
        "progress": progress if row["status"] == "running" else None,
        "canceling": canceling and row["status"] == "running",
        "lyrics": row["lyrics"],
        "inputs": [input_payload(item, (four_k or {}).get(item["id"])) for item in inputs or []],
        "images": [image_payload(img, (four_k or {}).get(img["id"])) for img in images],
        "tracks": [track_payload(track) for track in tracks or []],
    }
