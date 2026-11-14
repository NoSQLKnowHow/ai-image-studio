"""From the paragraphs of a part to the pieces an engine speaks (DESIGN.md §38.5 item 4, criterion 270).

A piece is what one call to an engine speaks and what the player highlights. The rules:
  * a paragraph that fits the engine's `max_chars` is one piece;
  * a longer one is cut at sentence ends into several, packed as full as the allowance lets them be; the later ones are marked `continues`, because the
    page shows them as the same paragraph (the highlight then covers a piece and not a whole paragraph: the one place "paragraph-level" bends);
  * a sentence that is too long on its own is cut at a semicolon, comma, dash or space (`sentences.cut_long`);
  * a paragraph shorter than `min_chars` is joined to the next paragraph, because very short utterances are an engine's weak spot (Kokoro's card says so);
    it is never joined across a heading, NEVER ACROSS A CHANGE OF SPEAKER (DESIGN.md §44.5: `Bob: Yes.` is Bob's piece and is not spoken in Alice's voice), and a short
    last paragraph stays short;
  * a heading is its own piece, always.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .model import Paragraph, PieceSpec
from .sentences import cut_long, pack, split_sentences


def _paragraph_pieces(text: str, max_chars: int, speaker: Optional[str] = None) -> list[PieceSpec]:
    """One paragraph as one piece, or as several when it is longer than the allowance. Every piece of it has the paragraph's speaker."""
    if len(text) <= max_chars:
        return [PieceSpec("paragraph", text, speaker=speaker)]
    chunks = pack(split_sentences(text), max_chars)
    return [PieceSpec("paragraph", chunk, continues=i > 0, speaker=speaker) for i, chunk in enumerate(chunks)]


def make_pieces(paragraphs: Sequence[Paragraph], max_chars: int, min_chars: int) -> list[PieceSpec]:
    """The pieces of a part. Paragraphs that are skipped (code, tables) are not read, so they make none; empty ones make none."""
    pieces: list[PieceSpec] = []
    waiting = ""  # a short paragraph (or several) that will be joined to the next one
    waiting_speaker: Optional[str] = None  # whose it is: the next paragraph is joined to it only if it is the same speaker's
    for paragraph in paragraphs:
        text = paragraph.text.strip()
        if paragraph.skipped is not None or not text:
            continue
        if paragraph.kind == "heading":
            if waiting:  # never joined across a heading: it is spoken alone
                pieces.extend(_paragraph_pieces(waiting, max_chars, waiting_speaker))
                waiting = ""
            pieces.extend(PieceSpec("heading", chunk) for chunk in (cut_long(text, max_chars) if len(text) > max_chars else [text]))
            continue
        if waiting:
            joined = f"{waiting} {text}"
            if paragraph.speaker == waiting_speaker and len(joined) <= max_chars:
                text = joined
            else:  # a change of speaker, or the two do not fit together: the short one goes alone
                pieces.extend(_paragraph_pieces(waiting, max_chars, waiting_speaker))
        waiting = ""
        if len(text) < min_chars:
            waiting, waiting_speaker = text, paragraph.speaker
            continue
        pieces.extend(_paragraph_pieces(text, max_chars, paragraph.speaker))
    if waiting:  # a short paragraph at the end of the part (or before nothing that fits): it stays short
        pieces.extend(_paragraph_pieces(waiting, max_chars, waiting_speaker))
    return pieces
