"""studio/upscale_job.py (DESIGN.md §28.3): the process that does one enlargement, with the model replaced by a stand-in (the `load`
seam) so it runs without PyTorch. What each way of failing exits with is what the server turns into an answer for the page, so the
codes are pinned here and the server's side of the same table is in test_upscaler.py. The real model is in test_enlarge_torch.py."""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
from PIL import Image

from studio import fourk, upscale_job as job


def nearest(image, passes):
    return image.resize((image.width * 2 ** passes, image.height * 2 ** passes), Image.Resampling.NEAREST)


def loader(upscale=nearest, label="a stand-in", device="cpu", calls=None):
    def load(model_path, device_choice):
        if calls is not None:
            calls.append((model_path, device_choice))
        return upscale, label, device

    return load


@pytest.fixture
def setup(tmp_path):
    model = tmp_path / "model.pth"
    model.write_bytes(b"not a real model: the stand-in loader never reads it")

    def make(size=(2752, 1536), **kwargs):
        source = tmp_path / "0.png"
        Image.new("RGB", size, (20, 40, 60)).save(source)
        return argparse.Namespace(model=model, device=kwargs.get("device", "auto"), src=source, dst=tmp_path / "0-4k-enlarged.png")

    return make


def test_it_makes_the_copy_and_reports_size_time_device_and_model(setup):
    args = setup()
    line = job.run(args, loader())
    assert fourk.file_size(args.dst) == (3840, 2160)
    assert line.startswith("ENLARGED 3840x2160 in ") and " on cpu with a stand-in" in line


def test_the_model_is_loaded_with_the_devices_the_server_asked_for(setup):
    calls: list = []
    args = setup(device="cuda")
    job.run(args, loader(calls=calls))
    assert calls == [(args.model, "cuda")]


def test_a_missing_model_file_is_a_model_problem_and_nothing_is_loaded(setup):
    args = setup()
    args.model = args.model.parent / "nowhere.pth"
    calls: list = []
    with pytest.raises(job.Problem) as info:
        job.run(args, loader(calls=calls))
    assert info.value.code == job.EXIT_MODEL == 4 and "not there" in str(info.value) and calls == []


def test_a_picture_that_cannot_be_enlarged_is_refused_before_the_model_is_loaded(setup):
    args = setup(size=(512, 512))
    calls: list = []
    with pytest.raises(job.Problem) as info:
        job.run(args, loader(calls=calls))
    assert info.value.code == job.EXIT_NOT_ELIGIBLE == 2 and "Enlarge goes up to 4×" in str(info.value)
    assert calls == [] and not args.dst.exists()


def test_a_picture_that_is_already_4k_is_refused_the_same_way(setup):
    args = setup(size=(3840, 2160))
    with pytest.raises(job.Problem) as info:
        job.run(args, loader())
    assert info.value.code == 2 and "already 4K" in str(info.value)


def test_a_picture_file_that_is_gone_exits_7(setup):
    args = setup()
    args.src.unlink()
    with pytest.raises(job.Problem) as info:
        job.run(args, loader())
    assert info.value.code == job.EXIT_GONE == 7


def test_a_picture_file_that_is_not_a_picture_exits_8(setup):
    args = setup()
    args.src.write_bytes(b"this is not a PNG at all")
    with pytest.raises(job.Problem) as info:
        job.run(args, loader())
    assert info.value.code == job.EXIT_UNREADABLE == 8


def test_a_source_that_disappears_between_the_check_and_the_work_exits_7(setup):
    args = setup()

    def vanishing(image, passes):  # the file is checked first, then read again by make_enlarged; simulate the second read failing
        raise FileNotFoundError("gone")

    with pytest.raises(job.Problem) as info:
        job.run(args, loader(upscale=vanishing))
    assert info.value.code == 7


def test_a_problem_loading_the_model_passes_through_with_its_own_code(setup):
    def refuse(model_path, device_choice):
        raise job.Problem(job.EXIT_ENVIRONMENT, "spandrel is not installed")

    with pytest.raises(job.Problem) as info:
        job.run(setup(), refuse)
    assert info.value.code == 3 and "spandrel" in str(info.value)


@pytest.mark.parametrize("error", [
    RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"),
    RuntimeError("[enforce fail at alloc_cpu.cpp] DefaultCPUAllocator: can't allocate memory: you tried to allocate 90 bytes"),
    MemoryError(),
    type("OutOfMemoryError", (RuntimeError,), {})("boom"),
    type("AcceleratorError", (RuntimeError,), {})("CUDA error: out of memory"),  # what the GB10 raises
])
def test_running_out_of_memory_is_its_own_answer_with_what_to_do(setup, error):
    # make the upscaling itself run out of memory
    def exhausted(image, passes):
        raise error

    args = setup()
    with pytest.raises(job.Problem) as info:
        job.run(args, loader(upscale=exhausted))
    assert info.value.code == job.EXIT_OUT_OF_MEMORY == 9
    assert str(info.value) == job.OUT_OF_MEMORY
    assert "Not enough memory" in str(info.value) and "try again in a moment" in str(info.value) and "STUDIO_UPSCALER_DEVICE=cpu" in str(info.value)
    assert "model file" not in str(info.value)  # it is not a damaged model
    assert not args.dst.exists() and not list(args.dst.parent.glob("*.part"))


def test_the_out_of_memory_words_blame_the_gpu_and_not_the_model_file():
    """The words that replaced "Check the model file", shown to someone whose model file was fine."""
    words = job.OUT_OF_MEMORY
    assert "GPU" in words and "STUDIO_UPSCALER_DEVICE=cpu" in words
    assert "model file" not in words and "could not be moved" not in words and "Check the" not in words


# Which errors count as "out of memory": the CUDA and CPU allocator messages, and MemoryError. Anything else (a device assert, a bad
# tile, a full disk) is not, and keeps its own exit code.
@pytest.mark.parametrize("error,is_oom", [
    (RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"), True),
    (type("AcceleratorError", (RuntimeError,), {})("CUDA error: out of memory\nCompile with TORCH_USE_CUDA_DSA"), True),
    (MemoryError(), True),
    (RuntimeError("DefaultCPUAllocator: can't allocate memory"), True),
    (RuntimeError("CUDA error: device-side assert triggered"), False),
    (ValueError("the tiles do not line up"), False),
    (OSError("No space left on device"), False),
])
def test_what_counts_as_running_out_of_memory(error, is_oom):
    assert job.is_out_of_memory(error) is is_oom
    problem = job.problem_for(error, job.EXIT_MODEL, "the usual message")
    assert (problem.code, str(problem)) == ((job.EXIT_OUT_OF_MEMORY, job.OUT_OF_MEMORY) if is_oom else (job.EXIT_MODEL, "the usual message"))


def test_any_other_failure_of_the_model_is_reported_with_its_type_and_message(setup):
    def broken(image, passes):
        raise ValueError("the tiles do not line up")

    with pytest.raises(job.Problem) as info:
        job.run(setup(), loader(upscale=broken))
    assert info.value.code == 5 and str(info.value) == "Enlarging failed: ValueError: the tiles do not line up"


def test_a_model_that_returns_the_wrong_size_is_a_failure_not_a_bad_file(setup):
    args = setup()
    with pytest.raises(job.Problem) as info:
        job.run(args, loader(upscale=lambda image, passes: image))
    assert info.value.code == 5 and "should be" in str(info.value) and not args.dst.exists()


def test_a_copy_that_cannot_be_written_exits_6(setup, tmp_path):
    args = setup()
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    args.dst = blocker / "0-4k-enlarged.png"  # its folder is a file: mkdir fails
    with pytest.raises(job.Problem) as info:
        job.run(args, loader())
    assert info.value.code == job.EXIT_OUTPUT == 6 and "could not be saved" in str(info.value)


def test_the_exit_codes_are_the_documented_ones():
    assert (job.EXIT_OK, job.EXIT_NOT_ELIGIBLE, job.EXIT_ENVIRONMENT, job.EXIT_MODEL, job.EXIT_FAILED, job.EXIT_OUTPUT, job.EXIT_GONE, job.EXIT_UNREADABLE,
            job.EXIT_OUT_OF_MEMORY) == (0, 2, 3, 4, 5, 6, 7, 8, 9)


# ------------------------------------------------------------------ the command line
def run_main(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = job.main(argv)
    return code, out.getvalue(), err.getvalue()


def test_main_exits_with_the_problems_code_and_a_last_stderr_line_the_server_can_read(setup, monkeypatch):
    args = setup(size=(512, 512))
    code, out, err = run_main(["--model", str(args.model), "--device", "cpu", "--src", str(args.src), "--dst", str(args.dst)])
    assert code == 2 and out == ""
    assert err.strip().splitlines()[-1].startswith("ENLARGE FAILED: This picture (512×512) is too small")


def test_main_prints_one_line_and_exits_0_on_success(setup, monkeypatch):
    args = setup()
    monkeypatch.setattr(job, "load_engine", loader())
    code, out, err = run_main(["--model", str(args.model), "--device", "cpu", "--src", str(args.src), "--dst", str(args.dst)])
    assert code == 0 and err == ""
    assert out.startswith("ENLARGED 3840x2160 in ") and out.count("\n") == 1
    assert fourk.file_size(args.dst) == (3840, 2160)


def test_an_unexpected_exception_is_reported_and_exits_5(setup, monkeypatch):
    args = setup()

    def explode(a):
        raise ZeroDivisionError("oops")

    monkeypatch.setattr(job, "run", explode)
    code, out, err = run_main(["--model", str(args.model), "--src", str(args.src), "--dst", str(args.dst)])
    assert code == 5 and err.strip() == "ENLARGE FAILED: unexpected ZeroDivisionError: oops"


def test_ctrl_c_exits_130(setup, monkeypatch):
    args = setup()

    def interrupted(a):
        raise KeyboardInterrupt

    monkeypatch.setattr(job, "run", interrupted)
    code, _, err = run_main(["--model", str(args.model), "--src", str(args.src), "--dst", str(args.dst)])
    assert code == 130 and "interrupted" in err


def test_the_arguments_are_required_and_the_device_is_checked():
    with pytest.raises(SystemExit):
        job.parser().parse_args(["--model", "m.pth"])
    with pytest.raises(SystemExit):
        job.parser().parse_args(["--model", "m", "--src", "s", "--dst", "d", "--device", "tpu"])
    parsed = job.parser().parse_args(["--model", "m", "--src", "s", "--dst", "d"])
    assert parsed.device == "auto" and isinstance(parsed.model, Path)
