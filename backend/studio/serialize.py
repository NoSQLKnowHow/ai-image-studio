"""JSON shapes returned by the API and pushed over server-sent events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from .fourk import enlarge_plan_or_none, plan_or_none


def utcnow() -> str:
    return format_ts(datetime.now(timezone.utc))


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_ts(moment: datetime) -> str:
    """The same shape as utcnow(), so timestamps compare correctly as text (the sweep relies on it)."""
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def four_k_fields(row: sqlite3.Row, base: str, copy: Optional[dict[str, Any]]) -> dict[str, Any]:
    """What a picture says about Make 4K and Enlarge (DESIGN.md §27.3, §28.2): `can_4k` is the server's rule for whether Make 4K
    is offered, `four_k_size` is what it would make (null if it is not offered); `can_enlarge` and `enlarge_size` are the same for
    Enlarge (the same frame, up to 4x, with how many x2 passes of the model it takes). `four_k` is the copy if one has been made
    (`copy` is its file's size in bytes, its width and height, and its `method`, `resize` or `model`), else None. The same for a
    result and for an edit's source. An Enlarge copy replaces a Make 4K copy at the same address, so its URLs carry the method:
    a browser that kept the old one (the address is immutable) fetches the new one."""
    plan = plan_or_none(row["width"], row["height"])
    enlarge = enlarge_plan_or_none(row["width"], row["height"])
    four_k = None
    if copy is not None:
        suffix = "?method=model" if copy.get("method") == "model" else ""
        four_k = {
            "width": copy["width"],
            "height": copy["height"],
            "bytes": copy["bytes"],
            "method": copy.get("method", "resize"),
            "url": f"{base}/4k{suffix}",
            "download_url": f"{base}/4k{suffix}{'&' if suffix else '?'}download=1",
        }
    return {
        "can_4k": plan is not None,
        "four_k_size": None if plan is None else {"width": plan.out_width, "height": plan.out_height, "trimmed": plan.trimmed},
        "can_enlarge": enlarge is not None,
        "enlarge_size": None if enlarge is None else {
            "width": enlarge.plan.out_width, "height": enlarge.plan.out_height, "trimmed": enlarge.plan.trimmed, "passes": enlarge.passes,
        },
        "four_k": four_k,
    }


def image_payload(row: sqlite3.Row, four_k: Optional[dict[str, Any]] = None) -> dict[str, Any]:
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


def input_payload(row: sqlite3.Row, four_k: Optional[dict[str, Any]] = None) -> dict[str, Any]:
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
    four_k: Optional[dict[str, dict[str, Any]]] = None,
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
