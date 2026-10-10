"""Enlarging a picture with an upscaler model, tile by tile (DESIGN.md §27.5, §28.3).

Shared by `studio/upscale_job.py` (Enlarge) and `scripts/upscale_probe.py` (the probe), so the code that is tested and measured is
the code that runs. The arithmetic (`tile_boxes`, `padding_needed`) needs nothing; `upscale_tiled` imports NumPy and PyTorch when it
is called, so importing this module is cheap and the API process never loads them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

from PIL import Image

DEFAULT_TILE = 512
# Source pixels of context around each tile that are fed to the model and thrown away after. Measured with the real Real-ESRGAN
# x2plus model on a CPU against a single whole-picture pass (DESIGN.md §28.3): at a tile edge the tiled result differs by 1.3/255 on
# average (24 at most) with 32 pixels, and by 0.4/255 (11 at most) with 64, for about 23% more work per tile.
DEFAULT_OVERLAP = 64


@dataclass(frozen=True)
class Tile:
    core: tuple[int, int, int, int]  # x0, y0, x1, y1: the part of the picture this tile is responsible for
    fed: tuple[int, int, int, int]  # the box given to the model: the core grown by the overlap, kept inside the picture


def tile_boxes(width: int, height: int, tile: int, overlap: int) -> list[Tile]:
    """Cut a width x height picture into cores of at most `tile` pixels a side, each with `overlap` pixels of context on every
    side that has a neighbour (a picture's own edge is not padded). Together the cores cover every pixel exactly once."""
    if tile < 1 or overlap < 0:
        raise ValueError("the tile must be at least 1 pixel and the overlap cannot be negative")
    tiles = []
    for y0 in range(0, height, tile):
        for x0 in range(0, width, tile):
            x1, y1 = min(x0 + tile, width), min(y0 + tile, height)
            tiles.append(Tile((x0, y0, x1, y1), (max(0, x0 - overlap), max(0, y0 - overlap), min(width, x1 + overlap), min(height, y1 + overlap))))
    return tiles


def padding_needed(width: int, height: int, minimum: int = 0, multiple_of: int = 1, square: bool = False) -> tuple[int, int]:
    """Extra pixels (right, bottom) so that a width x height input meets a model's size requirements: at least `minimum` on each
    side, a multiple of `multiple_of`, and square if it must be. (The ESRGAN x2 model needs multiples of 4.)"""
    target_w, target_h = max(width, minimum), max(height, minimum)
    if square:
        target_w = target_h = max(target_w, target_h)
    multiple = max(1, multiple_of)
    target_w, target_h = -(-target_w // multiple) * multiple, -(-target_h // multiple) * multiple
    if square:
        target_w = target_h = max(target_w, target_h)
    return target_w - width, target_h - height


def upscale_tiled(model, image: Image.Image, tile: int, overlap: int, device, dtype,
                  on_tile: Optional[Callable[[int, int, float], None]] = None) -> Image.Image:
    """Upscale `image` (RGB) with a spandrel model, tile by tile. Each tile is padded to the model's size requirements, run, cut
    back, and only its core is kept, so the tiles join without seams as long as the overlap covers the model's reach."""
    import numpy as np
    import torch

    source = np.array(image.convert("RGB"))  # a copy: PyTorch wants arrays it may write to
    height, width = source.shape[:2]
    scale = int(model.scale)
    result = np.empty((height * scale, width * scale, 3), dtype=np.uint8)
    requirements = getattr(model, "size_requirements", None)
    tiles = tile_boxes(width, height, tile, overlap)
    for number, piece in enumerate(tiles, 1):
        started = time.perf_counter()
        fx0, fy0, fx1, fy1 = piece.fed
        fed = torch.from_numpy(np.ascontiguousarray(source[fy0:fy1, fx0:fx1])).to(device).permute(2, 0, 1).unsqueeze(0).to(dtype) / 255.0
        right, bottom = padding_needed(
            fx1 - fx0, fy1 - fy0, getattr(requirements, "minimum", 0), getattr(requirements, "multiple_of", 1), getattr(requirements, "square", False))
        if right or bottom:
            fed = torch.nn.functional.pad(fed, (0, right, 0, bottom), mode="replicate")
        with torch.inference_mode():
            out = model(fed)
        out = out[..., : (fy1 - fy0) * scale, : (fx1 - fx0) * scale]  # the padding's share of the output is dropped
        cx0, cy0 = (piece.core[0] - fx0) * scale, (piece.core[1] - fy0) * scale
        cw, ch = (piece.core[2] - piece.core[0]) * scale, (piece.core[3] - piece.core[1]) * scale
        core = out[0, :, cy0:cy0 + ch, cx0:cx0 + cw].float().clamp(0, 1).mul(255).round().to(torch.uint8).permute(1, 2, 0).cpu().numpy()
        result[piece.core[1] * scale:piece.core[3] * scale, piece.core[0] * scale:piece.core[2] * scale] = core
        if device.type == "cuda":
            torch.cuda.synchronize()
        if on_tile:
            on_tile(number, len(tiles), time.perf_counter() - started)
    return Image.fromarray(result, "RGB")
