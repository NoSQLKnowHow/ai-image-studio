"""Fan-out of live events to server-sent-event subscribers (DESIGN.md §7).

Each browser connection gets a bounded queue. A subscriber that falls too far behind
is dropped; the browser reconnects and refetches, which is simpler and safer than
letting one slow client grow memory without bound.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

OVERFLOW = object()


class Subscription:
    def __init__(self, maxsize: int):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.dropped = False


class EventBus:
    def __init__(self, max_queue: int = 500):
        self._max_queue = max_queue
        self._subs: set[Subscription] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def subscribe(self) -> Subscription:
        sub = Subscription(self._max_queue)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        """Must be called from the event loop thread."""
        for sub in list(self._subs):
            try:
                sub.queue.put_nowait((event, data))
            except asyncio.QueueFull:
                sub.dropped = True
                self._subs.discard(sub)
                try:  # make room for the overflow marker so the stream ends promptly
                    sub.queue.get_nowait()
                    sub.queue.put_nowait((OVERFLOW, None))
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


def format_sse(event: str, data: Optional[dict[str, Any]]) -> str:
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"
