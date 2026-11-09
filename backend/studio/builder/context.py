"""The conversation as the model is sent it (DESIGN.md section 37.5, criterion 232, 255).

A chat is stored as messages; the model is sent the instruction (`instructions.system_prompt`, never dropped), the earlier turns, and the person's newest turn.
When the whole does not fit the context window (`STUDIO_OLLAMA_NUM_CTX`) the OLDEST turns are dropped first, here, before sending, so that Ollama never has to cut
anything itself; the instruction, the preferences, the examples and the draft are in the instruction and so are never dropped, and neither is the newest turn.

Earlier replies are sent back as what they SAID (the reply and its questions), in the same JSON the model writes, and not as the draft each time (the current
draft is in the instruction once), and never with any reasoning: the reasoning is not stored (rule 18).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

PICTURE_TOKENS = 1000  # PROPOSED: what one attached picture is counted as; Ollama does not say before the call
CHARS_PER_TOKEN = 3  # a deliberately low estimate (English is nearer 4), so that the guess errs towards dropping a turn rather than overflowing
MIN_REPLY_TOKENS = 2048


def estimate_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


def reply_room(num_ctx: int) -> int:
    """The part of the window kept for the model's own writing, reasoning included: a quarter, and at least 2048 tokens."""
    return max(MIN_REPLY_TOKENS, num_ctx // 4)


@dataclass
class Turn:
    """One stored message, ready to be sent: `content` is the stored structure (an answer, or what the person chose), `images` are base64 pictures."""

    role: str
    text: str
    content: Optional[Mapping[str, Any]] = None
    images: Sequence[str] = field(default_factory=tuple)


@dataclass
class Built:
    messages: list[dict[str, Any]]
    dropped: int  # earlier turns left out to make room
    tokens: int  # the estimate of what is sent
    over: bool  # even the instruction and the newest turn alone do not fit: it is sent anyway, and the model may lose the start of it


def render_user(turn: Turn) -> str:
    """What the person said: their words, then their answers to the questions, in words."""
    lines = [turn.text.strip()] if turn.text.strip() else []
    answers = (turn.content or {}).get("answers") or []
    rendered = []
    for a in answers:
        question = a.get("question") or a.get("id")
        mode = a.get("mode")
        if mode == "surprise":
            rendered.append(f'- "{question}": Surprise me (you choose, and say what you chose).')
        elif mode == "skip":
            rendered.append(f'- "{question}": Skip (leave it out, and do not ask it again).')
        elif a.get("chips"):
            rendered.append(f'- "{question}": ' + "; ".join(str(c) for c in a["chips"]))
    if rendered:
        lines += ["My answers to your questions:", *rendered]
    return "\n".join(lines) if lines else "(no words)"


def render_assistant(turn: Turn) -> str:
    """What the model said, as the JSON it writes, without the draft (it is in the instruction) and without the notices (they were for the person)."""
    content = turn.content or {}
    if content.get("plain") or not content:
        return turn.text
    said: dict[str, Any] = {"reply": content.get("reply", turn.text)}
    questions = content.get("questions") or []
    if questions:
        said["questions"] = [{"id": q["id"], "text": q["text"], "chips": q.get("chips", [])} for q in questions]
    if content.get("done"):
        said["done"] = True
    return json.dumps(said, ensure_ascii=False)


def build_messages(system: str, turns: Sequence[Turn], *, intent_line: str, num_ctx: int) -> Built:
    """The messages for one reply. `turns` are the stored turns in order, the last being the person's newest. The newest turn also carries `intent_line`, what
    the person just did (Write it now, …), in square brackets after their words."""
    if not turns or turns[-1].role != "user":
        raise ValueError("a reply answers a person's turn: the last turn must be theirs")
    *earlier, newest = turns
    newest_text = render_user(newest) + (f"\n\n[From the studio: {intent_line}]" if intent_line else "")
    fixed = estimate_tokens(system) + estimate_tokens(newest_text) + PICTURE_TOKENS * len(newest.images)
    room = num_ctx - reply_room(num_ctx) - fixed

    kept: list[tuple[Turn, str, int]] = []
    for turn in reversed(earlier):
        text = render_user(turn) if turn.role == "user" else render_assistant(turn)
        cost = estimate_tokens(text) + PICTURE_TOKENS * len(turn.images)
        if cost > room:
            break  # the oldest are dropped first, so the kept turns stay one unbroken run
        kept.append((turn, text, cost))
        room -= cost
    kept.reverse()
    while kept and kept[0][0].role != "user":  # the model is not sent a conversation that begins with its own words
        room += kept[0][2]
        kept.pop(0)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for turn, text, _ in kept:
        message: dict[str, Any] = {"role": turn.role, "content": text}
        if turn.images:
            message["images"] = list(turn.images)
        messages.append(message)
    last: dict[str, Any] = {"role": "user", "content": newest_text}
    if newest.images:
        last["images"] = list(newest.images)
    messages.append(last)
    used = num_ctx - reply_room(num_ctx) - room
    return Built(messages=messages, dropped=len(earlier) - len(kept), tokens=used, over=fixed > num_ctx - reply_room(num_ctx))
