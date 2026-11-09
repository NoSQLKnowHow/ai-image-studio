"""What the real speech engines share (DESIGN.md §38.3): turning an engine's audio into the studio's 16-bit mono PCM, reading a WAV an engine's server sent back,
seeding, and the words for the two ways a piece can fail.

Nothing here imports a heavy library at module level. The API process imports every engine module to describe it (the engine's `INFO` and `PRESETS`), and it has
no torch; an engine imports its own libraries when it is asked to probe, load or speak, and then only in the speech worker.
"""

from __future__ import annotations

import array
import struct
import sys
from typing import Any, Optional

from ...pipelines.base import OutOfMemory, PipelineError
from ...sysinfo import MEMORY_HINT

PCM_MAX = 32767


def pcm16(samples: Any) -> bytes:
    """16-bit mono little-endian PCM from float samples in [-1, 1]: a torch tensor, a numpy array or a plain list. One rule for all three, so the same audio gives the
    same bytes whatever form it arrives in: not-a-number is silence, a value past full scale is clipped to it, the scale is 32767 both ways, and the result is rounded
    and not truncated. Raises PipelineError for no audio at all, which an engine can produce for text it could not make anything of (only punctuation)."""
    if hasattr(samples, "detach"):  # a torch tensor
        samples = samples.detach().cpu().float().numpy()
    if not isinstance(samples, (list, tuple, array.array)):
        import numpy as np

        data = np.asarray(samples, dtype=np.float32)
        if data.ndim == 2:  # (channels, samples) or (samples, channels): the shorter axis is the channels
            data = data.mean(axis=0 if data.shape[0] <= data.shape[1] else 1)
        data = np.nan_to_num(data.reshape(-1), nan=0.0, posinf=1.0, neginf=-1.0)
        out = np.rint(np.clip(data, -1.0, 1.0) * PCM_MAX).astype("<i2").tobytes()
    else:
        converted = array.array("h", (0 if s != s else int(round(max(-1.0, min(1.0, s)) * PCM_MAX)) for s in samples))  # the same rule as above: clip, scale, round
        if sys.byteorder == "big":
            converted.byteswap()
        out = converted.tobytes()
    if not out:
        raise PipelineError("The engine made no audio for this text.", hint="A paragraph of nothing but punctuation or symbols cannot be spoken. Edit it, or take it out.")
    return out


def wav_to_pcm16(data: bytes) -> tuple[bytes, int]:
    """(16-bit mono PCM, sample rate) from a WAV file as an engine's server returns it: 16-, 24- or 32-bit integer samples or 32- or 64-bit float, any number of
    channels (averaged into one). Raises PipelineError if it is not a WAV the studio can read."""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise PipelineError("The speech server did not send a WAV file.", hint="Check the engine's log.")
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        tag, length = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = pos + 8
        if tag == b"fmt " and body + 16 <= len(data):
            code, channels, rate, _bps, _align, bits = struct.unpack("<HHIIHH", data[body:body + 16])
            if code == 0xFFFE and length >= 26:  # WAVE_FORMAT_EXTENSIBLE: the real code is the first two bytes of the sub-format
                code = struct.unpack("<H", data[body + 24:body + 26])[0]
            fmt = (code, channels, rate, bits)
        elif tag == b"data":
            if fmt is None:
                break
            code, channels, rate, bits = fmt
            end = len(data) if length in (0, 0xFFFFFFFF) or body + length > len(data) else body + length
            frame = channels * bits // 8
            if frame <= 0 or channels < 1 or rate < 1:
                break
            raw = data[body:body + ((end - body) // frame) * frame]
            return pcm16(_floats(raw, code, channels, bits)), rate
        pos = body + length + (length % 2)
    raise PipelineError("The speech server sent a WAV file the studio cannot read.", hint="Check the engine's log.")


def _floats(raw: bytes, code: int, channels: int, bits: int) -> list[float]:
    """Samples as floats in [-1, 1], one per frame (channels averaged)."""
    if code == 1 and bits == 16:
        values = array.array("h")
        values.frombytes(raw)
        if sys.byteorder == "big":
            values.byteswap()
        scale = 32768.0
        flat = [v / scale for v in values]
    elif code == 1 and bits == 24:
        flat = [int.from_bytes(raw[i:i + 3], "little", signed=True) / 8388608.0 for i in range(0, len(raw), 3)]
    elif code == 1 and bits == 32:
        flat = [v / 2147483648.0 for v in struct.unpack(f"<{len(raw) // 4}i", raw)]
    elif code == 3 and bits in (32, 64):
        flat = list(struct.unpack(f"<{len(raw) // (bits // 8)}{'f' if bits == 32 else 'd'}", raw))
    else:
        raise PipelineError(f"The speech server sent audio in a form the studio does not read (format {code}, {bits} bits).")
    if channels == 1:
        return flat
    return [sum(flat[i:i + channels]) / channels for i in range(0, len(flat) - channels + 1, channels)]


def seed_torch(torch: Any, seed: int) -> None:
    """Make a piece repeatable: the same text, voice and seed give the same audio as far as the engine allows (DESIGN.md §38.3)."""
    torch.manual_seed(seed)
    cuda = getattr(torch, "cuda", None)
    if cuda is not None and cuda.is_available():
        cuda.manual_seed_all(seed)


def is_out_of_memory(exc: BaseException, torch: Optional[Any] = None) -> bool:
    cuda_oom = getattr(getattr(torch, "cuda", None), "OutOfMemoryError", None) if torch is not None else None
    return (cuda_oom is not None and isinstance(exc, cuda_oom)) or "out of memory" in str(exc).lower()


def speaking_failed(exc: BaseException, engine: str, torch: Optional[Any] = None) -> PipelineError:
    """The studio's error for an exception raised while an engine was making one piece: running out of memory is its own kind (the reading is told how to make
    room), anything else says what the engine said. A `PipelineError` an engine raised itself is returned as it is."""
    if isinstance(exc, PipelineError):
        return exc
    if is_out_of_memory(exc, torch):
        try:
            torch.cuda.empty_cache()  # type: ignore[union-attr]
        except Exception:
            pass
        return OutOfMemory(f"{engine} ran out of memory while speaking this paragraph.", hint=MEMORY_HINT)
    return PipelineError(f"{engine} could not speak this paragraph: {type(exc).__name__}: {exc}", hint="If it keeps failing on the same paragraph, edit its words or take it out.")
