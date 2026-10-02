"""JSON shapes returned by the API and pushed over server-sent events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def image_payload(row: sqlite3.Row) -> dict[str, Any]:
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
    }


def run_payload(
    row: sqlite3.Row,
    images: list[sqlite3.Row],
    progress: Optional[dict[str, Any]] = None,
    queue_position: Optional[int] = None,
    canceling: bool = False,
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
        "queue_position": queue_position if row["status"] == "queued" else None,
        "progress": progress if row["status"] == "running" else None,
        "canceling": canceling and row["status"] == "running",
        "images": [image_payload(img) for img in images],
    }
