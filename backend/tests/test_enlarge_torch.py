"""Enlarge with a real model file and the real code path (DESIGN.md §28.3; criteria 95, 99, 101, 105): spandrel's own loader, the
tiling, the job and the server's process handling, with a tiny random-weight ESRGAN (under a megabyte) standing in for Real-ESRGAN.
It shows that the plumbing works end to end and says nothing about how a real model looks: that is a Spark question. Needs PyTorch
and spandrel, so it is skipped where they are not installed (the container has them), as the real-music tests are."""

from __future__ import annotations

import asyncio
import io
import sys

import pytest
from PIL import Image

torch = pytest.importorskip("torch")
pytest.importorskip("numpy")
pytest.importorskip("spandrel")

try:  # spandrel's own network class, to make a checkpoint it recognises (the module name is spandrel's, and pinned in the image)
    from spandrel.architectures.ESRGAN.__arch.RRDB import RRDBNet
except ImportError:  # pragma: no cover
    pytest.skip("spandrel's ESRGAN network moved: this fixture needs updating", allow_module_level=True)

from conftest import create_run, wait_for

from studio import fourk, tiling, upscale_job
from studio.config import Settings
from studio.upscaler import ModelUpscaler


def checkpoint(path, scale: int = 2):
    torch.manual_seed(0)
    torch.save(RRDBNet(in_nc=3, out_nc=3, num_filters=4, num_blocks=1, scale=scale).state_dict(), path)
    return path


@pytest.fixture(scope="module")
def tiny(tmp_path_factory):
    return checkpoint(tmp_path_factory.mktemp("models") / "tiny_x2.pth")


def photo(path, size):
    picture = Image.effect_noise(size, 40).convert("RGB")
    picture.save(path)
    return path


# ------------------------------------------------------------------ the engine
def test_the_real_loader_loads_a_x2_model_and_the_engine_enlarges_by_the_passes(tiny):
    upscale, label, device = upscale_job.load_engine(tiny, "cpu")
    assert label == "ESRGAN ×2 (tiny_x2.pth)" and device == "cpu"
    source = Image.effect_noise((40, 24), 50).convert("RGB")
    assert upscale(source, 1).size == (80, 48) and upscale(source, 2).size == (160, 96)
    assert upscale(source, 1).mode == "RGB"


@pytest.mark.parametrize("size", [(1, 1), (7, 5), (37, 23), (130, 3)])
def test_odd_sizes_come_out_exactly_twice_as_big(tiny, size):
    upscale, _, _ = upscale_job.load_engine(tiny, "cpu")
    assert upscale(Image.new("RGB", size, (90, 120, 150)), 1).size == (size[0] * 2, size[1] * 2)


def test_a_x4_model_is_refused_because_the_plan_counts_in_x2_passes(tmp_path):
    with pytest.raises(upscale_job.Problem) as info:
        upscale_job.load_engine(checkpoint(tmp_path / "x4.pth", scale=4), "cpu")
    assert info.value.code == upscale_job.EXIT_MODEL and "×4 model" in str(info.value) and "needs a ×2" in str(info.value)


def test_a_file_that_is_not_a_model_is_refused_with_the_reason(tmp_path):
    junk = tmp_path / "junk.pth"
    junk.write_bytes(b"this is not a checkpoint")
    with pytest.raises(upscale_job.Problem) as info:
        upscale_job.load_engine(junk, "cpu")
    assert info.value.code == upscale_job.EXIT_MODEL and "junk.pth could not be loaded" in str(info.value)


@pytest.mark.skipif(torch.cuda.is_available(), reason="this machine has a GPU")
def test_asking_for_cuda_without_a_gpu_is_an_environment_problem(tiny):
    with pytest.raises(upscale_job.Problem) as info:
        upscale_job.load_engine(tiny, "cuda")
    assert info.value.code == upscale_job.EXIT_ENVIRONMENT and "cannot see a GPU" in str(info.value)


def test_the_tiles_join_without_gaps_through_the_real_loader(tiny):
    """A picture bigger than a tile, cut into several: the tiled result is the whole-picture result where the context reaches."""
    model = __import__("spandrel").ModelLoader().load_from_file(tiny).to("cpu", torch.float32).eval()
    picture = Image.effect_noise((150, 110), 60).convert("RGB")
    whole = tiling.upscale_tiled(model, picture, 4096, 0, torch.device("cpu"), torch.float32)
    tiled = tiling.upscale_tiled(model, picture, 64, 48, torch.device("cpu"), torch.float32)
    assert tiled.size == whole.size == (300, 220)
    import numpy as np

    assert np.abs(np.asarray(tiled).astype(int) - np.asarray(whole).astype(int)).max() <= 1  # a rounding apart at most


def test_the_tiling_defaults_are_the_measured_ones():
    assert (tiling.DEFAULT_TILE, tiling.DEFAULT_OVERLAP) == (512, 64) and tiling.DEFAULT_OVERLAP * 2 < tiling.DEFAULT_TILE


# ------------------------------------------------------------------ the job, in this process
def test_the_job_makes_a_whole_4k_copy_with_the_real_loader(tiny, tmp_path):
    source, target = photo(tmp_path / "0.png", (2752, 1536)), tmp_path / "0-4k-enlarged.png"
    assert upscale_job.main(["--model", str(tiny), "--device", "cpu", "--src", str(source), "--dst", str(target)]) == 0
    with Image.open(target) as made:
        assert made.size == (3840, 2160) and "ESRGAN ×2 (tiny_x2.pth)" in made.text["make4k"] and "(×2)" in made.text["make4k"]


def test_a_two_pass_picture_is_run_twice_and_still_ends_at_exactly_the_frame(tiny, tmp_path):
    source, target = photo(tmp_path / "0.png", (960, 544)), tmp_path / "0-4k-enlarged.png"
    assert upscale_job.main(["--model", str(tiny), "--device", "cpu", "--src", str(source), "--dst", str(target)]) == 0
    with Image.open(target) as made:
        assert made.size == (3840, 2160) and "(×4)" in made.text["make4k"]


def test_a_transparent_picture_keeps_its_transparency_through_the_real_loader(tiny, tmp_path):
    source, target = tmp_path / "0.png", tmp_path / "0-4k-enlarged.png"
    rgba = Image.new("RGBA", (2048, 2048), (200, 100, 50, 255))
    for x in range(1024):
        for y in range(2048):
            rgba.putpixel((x, y), (200, 100, 50, 0))
    rgba.save(source)
    assert upscale_job.main(["--model", str(tiny), "--device", "cpu", "--src", str(source), "--dst", str(target)]) == 0
    with Image.open(target) as made:
        assert made.mode == "RGBA" and made.getpixel((100, 100))[3] == 0 and made.getpixel((3500, 100))[3] == 255


# ------------------------------------------------------------------ the server's process, for real
def test_the_server_runs_the_job_in_a_real_process_and_gets_the_file(tiny, tmp_path):
    source, target = photo(tmp_path / "0.png", (2048, 2048)), tmp_path / "0-4k-enlarged.png"
    upscaler = ModelUpscaler(Settings(data_dir=tmp_path / "d", pipeline="real", upscaler_model=tiny, upscaler_device="cpu"))
    assert upscaler.availability().available is True
    asyncio.run(upscaler.enlarge(source, target))
    assert fourk.file_size(target) == (3840, 3840)


def test_a_picture_that_cannot_be_enlarged_comes_back_as_the_servers_not_eligible_error(tiny, tmp_path):
    source = photo(tmp_path / "0.png", (512, 512))
    upscaler = ModelUpscaler(Settings(data_dir=tmp_path / "d", pipeline="real", upscaler_model=tiny, upscaler_device="cpu"))
    with pytest.raises(fourk.NotEligible, match="Enlarge goes up to 4×"):
        asyncio.run(upscaler.enlarge(source, tmp_path / "out.png"))


def test_a_model_that_cannot_be_loaded_comes_back_as_a_500_style_error_with_the_reason(tmp_path):
    junk = tmp_path / "junk.pth"
    junk.write_bytes(b"not a checkpoint")
    source = photo(tmp_path / "0.png", (2048, 2048))
    upscaler = ModelUpscaler(Settings(data_dir=tmp_path / "d", pipeline="real", upscaler_model=junk, upscaler_device="cpu"))
    from studio.upscaler import UpscaleFailed

    with pytest.raises(UpscaleFailed, match="junk.pth could not be loaded"):
        asyncio.run(upscaler.enlarge(source, tmp_path / "out.png"))


def test_the_whole_stack_from_the_page_s_request_to_the_file(client, tiny):
    """The API, the job manager, a real process, spandrel's loader and the tiling, with the fake pipeline making the picture."""
    client.app.state.jobs.upscaler = ModelUpscaler(Settings(data_dir=client.app.state.settings.data_dir, pipeline="real",
                                                           upscaler_model=tiny, upscaler_device="cpu"))
    assert client.get("/api/capabilities").json()["upscaler"] == {
        "available": True, "model": "tiny_x2.pth", "reason": None, "hint": None, "max_enlargement": 4}
    run = wait_for(client, create_run(client, "a harbour", width=2048, height=2048, steps=2)["id"])
    response = client.post(f"/api/images/{run['images'][0]['id']}/enlarge")
    assert response.status_code == 201, response.text
    copy = response.json()["images"][0]["four_k"]
    assert (copy["width"], copy["height"], copy["method"]) == (3840, 3840, "model")
    with Image.open(io.BytesIO(client.get(copy["url"]).content)) as made:
        assert made.size == (3840, 3840) and "ESRGAN ×2 (tiny_x2.pth)" in made.text["make4k"]
    assert sys.executable  # (the process was started with the interpreter that runs the tests)
