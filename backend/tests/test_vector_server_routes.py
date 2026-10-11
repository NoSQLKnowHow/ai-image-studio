"""The four Vector download routes and the names they save under (DESIGN.md §31.12.7): the combined SVG (served so that it can run nothing), the ZIP of one SVG per
layer, and a PNG drawn from the SVG at a size the user chooses (off the event loop, a few at a time). What these protect: an SVG can never run a script or be
sniffed as something else, a download has the right name, the PNG's size range is enforced, and a vector that is not there is a plain 404.
"""

from __future__ import annotations

import asyncio
import io
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from vector_server_support import NO_SUCH, libraries_import, make_vector, vclient, vclient_factory  # noqa: F401

from studio import vector as V
from studio.naming import vector_filename
from studio.serialize import parse_ts

CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"a drawing made on request"


@pytest.fixture
def made(vclient):
    """A finished vector run with two versions: (client, run, the first version)."""
    run = make_vector(vclient, "A fox head!", seed=5, num_images=2, colours=3, width_mm=152.4, height_mm=100)
    return vclient, run, run["vectors"][0]


def stored(client, vector: dict, suffix: str) -> bytes:
    """A vector's file as the server stored it."""
    run_id = client.app.state.db.get_vector(vector["id"])["run_id"]
    return (client.app.state.storage.vector_dir(run_id) / f"{vector['idx']}{suffix}").read_bytes()


def expected_name(run: dict, vector: dict, suffix: str) -> str:
    """The name §31.12.7 gives, built here from the run's own time so that the test does not use the function it tests."""
    stamp = parse_ts(run["created_at"]).astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"vector_fox-head_152.4x100mm_{vector['colours']}c_s{vector['seed']}_{stamp}{suffix}"


# ------------------------------------------------------------------ the SVG
def test_the_svg_is_served_as_an_svg_that_can_run_nothing(made):
    client, run, vector = made
    response = client.get(vector["url"])
    assert response.status_code == 200 and response.content == stored(client, vector, ".svg")
    assert response.headers["content-type"] == "image/svg+xml"  # no charset or anything else added
    assert response.headers.get_list("content-security-policy") == [CSP]  # this policy, once: the page-wide one is not added beside it
    assert response.headers.get_list("x-content-type-options") == ["nosniff"]
    assert "immutable" in response.headers["cache-control"] and "max-age=31536000" in response.headers["cache-control"]
    assert "content-disposition" not in response.headers  # shown, not saved, unless it is asked to be


def test_the_svg_download_has_its_name_and_is_an_attachment(made):
    client, run, vector = made
    response = client.get(vector["download_url"])
    assert response.status_code == 200 and response.content == stored(client, vector, ".svg")
    name = expected_name(run, vector, ".svg")
    assert name.startswith("vector_fox-head_152.4x100mm_4c_s5_")  # the idea's words (no filler), the sheet, the colours the file has, the seed
    assert response.headers["content-disposition"] == f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}"
    assert response.headers["content-security-policy"] == CSP and response.headers["content-type"] == "image/svg+xml"  # a download is as safe as a view
    assert client.get(vector["url"] + "?download=0").headers.get("content-disposition") is None


def test_each_version_is_named_by_its_own_seed(made):
    client, run, _ = made
    names = [re.search(r'filename="([^"]+)"', client.get(v["download_url"]).headers["content-disposition"]).group(1) for v in run["vectors"]]
    assert [name.split("_")[-2] for name in names] == ["s5", "s6"] and len(set(names)) == 2


# ------------------------------------------------------------------ the layers
def test_the_layers_come_as_a_zip_attachment_always(made):
    client, run, vector = made
    response = client.get(vector["zip_url"])
    assert response.status_code == 200 and response.content == stored(client, vector, "-layers.zip")
    assert response.headers["content-type"] == "application/zip"
    name = expected_name(run, vector, "-layers.zip")
    assert response.headers["content-disposition"] == f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}"  # no `?download=1` needed
    assert "immutable" in response.headers["cache-control"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        assert bundle.namelist() == ["layer-1-black.svg"]  # it is the file the job made, whole


# ------------------------------------------------------------------ the PNG
@pytest.fixture
def drawn(monkeypatch):
    """Replace the engine's `render_png` with one that records how it was called (the engine is not built when the server is, and the server's
    tests must not need it). Returns the list of its calls: (svg text, long side, whether the call was made off the event loop)."""
    calls: list[tuple[str, int, bool]] = []

    def render(svg: str, size: int) -> bytes:
        try:
            asyncio.get_running_loop()
            on_the_loop = True
        except RuntimeError:  # a thread with no event loop in it, which is what `asyncio.to_thread` gives
            on_the_loop = False
        calls.append((svg, size, on_the_loop))
        return FAKE_PNG

    monkeypatch.setattr("studio.vector.render_png", render)
    return calls


def test_the_stored_preview_is_sent_as_it_is_and_nothing_is_drawn(made, drawn):
    client, run, vector = made
    for url in (vector["png_url"], vector["preview_url"], vector["png_url"] + "?size=1024"):  # the default size is the preview's
        response = client.get(url)
        assert response.status_code == 200 and response.content == stored(client, vector, ".png"), url
        assert response.headers["content-type"] == "image/png" and "immutable" in response.headers["cache-control"]
        assert "content-disposition" not in response.headers
    assert drawn == []  # the stored file served all three


def test_another_size_is_drawn_from_the_svg_off_the_event_loop(made, drawn):
    client, run, vector = made
    response = client.get(vector["png_url"] + "?size=2048")
    assert response.status_code == 200 and response.content == FAKE_PNG and response.headers["content-type"] == "image/png"
    assert drawn == [(stored(client, vector, ".svg").decode("utf-8"), 2048, False)]  # from the SVG's own shapes, in a worker thread
    assert "immutable" in response.headers["cache-control"]  # one address for one size, and the file never changes


def test_a_png_download_is_an_attachment_named_like_the_svg(made, drawn):
    client, run, vector = made
    for url in (vector["png_download_url"], vector["png_download_url"] + "&size=640"):
        response = client.get(url)
        name = expected_name(run, vector, ".png")
        assert response.status_code == 200 and response.headers["content-disposition"] == f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}"
    assert client.get(vector["png_download_url"]).content == stored(client, vector, ".png")  # the preview size is the stored file, also as a download
    assert [size for _, size, _ in drawn] == [640]


@pytest.mark.parametrize("size, status", [(15, 422), (16, 200), (17, 200), (1023, 200), (1025, 200), (8191, 200), (8192, 200), (8193, 422), (0, 422), (-1, 422),
                                          ("abc", 422), ("", 422), ("1e3", 422), ("10.5", 422)])
def test_the_size_of_a_png_is_16_to_8192(made, drawn, size, status):
    client, run, vector = made
    response = client.get(vector["png_url"], params={"size": size})
    assert response.status_code == status, (size, response.text)
    if status == 422:
        assert drawn == [] and response.json()["detail"]  # nothing is drawn for a size that is refused, and the answer says which parameter
        assert response.json()["detail"][0]["loc"] == ["query", "size"]


def test_a_picture_that_cannot_be_drawn_is_a_500_that_says_why(made, monkeypatch):
    client, run, vector = made

    def broken(svg, size):
        raise V.VectorError("The drawing library is not installed.", "pip install it")

    monkeypatch.setattr("studio.vector.render_png", broken)
    response = client.get(vector["png_url"] + "?size=2000")
    assert response.status_code == 500 and response.json() == {"detail": "The drawing library is not installed.", "code": "png_failed", "hint": "pip install it"}
    monkeypatch.setattr("studio.vector.render_png", lambda svg, size: (_ for _ in ()).throw(V.VectorError("no hint here")))
    assert client.get(vector["png_url"] + "?size=2000").json() == {"detail": "no hint here", "code": "png_failed"}  # a hint only when there is one
    assert client.get(vector["png_url"]).status_code == 200  # the stored preview does not need the engine at all


def test_only_a_few_pngs_are_drawn_at_the_same_time(made, monkeypatch):
    """An 8192 pixel PNG is a lot of memory, so the drawing is limited (to two at once) however many are asked for."""
    client, run, vector = made
    running = 0
    most = 0
    lock = threading.Lock()

    def render(svg, size):
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        time.sleep(0.3)
        with lock:
            running -= 1
        return FAKE_PNG

    monkeypatch.setattr("studio.vector.render_png", render)
    with ThreadPoolExecutor(6) as pool:
        answers = list(pool.map(lambda size: client.get(vector["png_url"], params={"size": size}).status_code, range(2000, 2006)))
    assert answers == [200] * 6 and most == 2  # six asked, never more than two at a time, and two did run together


# ------------------------------------------------------------------ what is not there
def test_a_vector_that_does_not_exist_is_a_404_on_every_route(vclient):
    for path in ("svg", "layers.zip", "png"):
        for vid in (NO_SUCH, "not-an-id", "A" * 32, "0" * 31):
            response = vclient.get(f"/api/vectors/{vid}/{path}")
            assert response.status_code == 404 and response.json() == {"detail": "Vector not found.", "code": "not_found"}, (path, vid)


def test_a_vector_whose_file_has_gone_is_a_404_not_an_error(made, drawn):
    client, run, vector = made
    folder = client.app.state.storage.vector_dir(run["id"])
    for suffix, url in ((".svg", vector["url"]), ("-layers.zip", vector["zip_url"]), (".png", vector["png_url"])):
        (folder / f"{vector['idx']}{suffix}").unlink()
        assert client.get(url).status_code == 404, suffix
    assert client.get(vector["png_url"] + "?size=2000").status_code == 404  # drawing a size needs the SVG, which is gone too


def test_a_picture_or_a_track_is_not_a_vector(vclient):
    from conftest import create_run, wait_for

    picture = wait_for(vclient, create_run(vclient)["id"])["images"][0]
    track = wait_for(vclient, vclient.post("/api/runs", json={"mode": "music", "prompt": "Genre: ambient.", "options": {"duration": 30}}).json()["id"])["tracks"][0]
    for path in ("svg", "layers.zip", "png"):
        assert vclient.get(f"/api/vectors/{picture['id']}/{path}").status_code == 404
        assert vclient.get(f"/api/vectors/{track['id']}/{path}").status_code == 404


def test_a_vector_of_a_deleted_run_is_gone_and_one_in_the_bin_is_still_served(made):
    client, run, vector = made
    client.post(f"/api/runs/{run['id']}/bin")
    assert client.get(vector["url"]).status_code == 200 and client.get(vector["zip_url"]).status_code == 200  # the bin keeps its files
    client.delete(f"/api/runs/{run['id']}")
    assert client.get(vector["url"]).status_code == 404


def test_the_routes_only_read(made):
    client, run, vector = made
    for path in ("svg", "layers.zip", "png"):
        assert client.post(f"/api/vectors/{vector['id']}/{path}").status_code == 405
        assert client.delete(f"/api/vectors/{vector['id']}/{path}").status_code == 405


# ------------------------------------------------------------------ the names
WHEN = datetime(2026, 10, 11, 3, 4, 5, tzinfo=timezone.utc)


def test_the_file_name_follows_the_scheme_of_the_contract():
    name = vector_filename(prompt="a fox head", width_mm=100.0, height_mm=100.0, colours=4, seed=1234, created_at=WHEN)
    assert name == "vector_fox-head_100x100mm_4c_s1234_20261011-030405.svg"  # `vector_<prompt-words>_<W>x<H>mm_<N>c_s<seed>_<YYYYmmdd-HHMMSS>.svg`
    assert vector_filename(prompt="a fox head", width_mm=100.0, height_mm=100.0, colours=4, seed=1234, created_at=WHEN, suffix="-layers.zip").endswith(
        "s1234_20261011-030405-layers.zip")
    assert vector_filename(prompt="x", width_mm=1.0, height_mm=1.0, colours=2, seed=0, created_at=WHEN, suffix=".png").endswith(".png")


@pytest.mark.parametrize("millimetres, written", [(100.0, "100"), (5.0, "5"), (1200.0, "1200"), (152.4, "152.4"), (307.0, "307"), (99.95, "100"),
                                                  (0.5, "0.5"), (10.04, "10")])
def test_a_size_reads_as_a_plain_number_of_millimetres(millimetres, written):
    name = vector_filename(prompt="x", width_mm=millimetres, height_mm=millimetres, colours=2, seed=1, created_at=WHEN)
    assert f"_{written}x{written}mm_" in name


def test_the_name_uses_the_width_first_and_the_colours_it_was_given():
    name = vector_filename(prompt="a stag", width_mm=600.0, height_mm=307.0, colours=8, seed=7, created_at=WHEN)
    assert "_600x307mm_8c_s7_" in name


def test_the_words_of_the_idea_are_made_safe_for_a_file_name():
    name = vector_filename(prompt="  A fox's head / with <tags> & \"quotes\"  ", width_mm=10.0, height_mm=10.0, colours=2, seed=1, created_at=WHEN)
    assert re.fullmatch(r"vector_[a-z0-9-]+_10x10mm_2c_s1_20261011-030405\.svg", name), name
    assert vector_filename(prompt="", width_mm=10.0, height_mm=10.0, colours=2, seed=1, created_at=WHEN).startswith("vector_untitled_")


def test_a_run_that_vanishes_between_the_two_lookups_costs_only_the_name(made):
    """The run is looked up after the vector (for the name). If it is gone by then (deleted at that very moment) the file is still sent, unnamed."""
    client, run, vector = made
    client.app.state.db.get_run = lambda run_id: None
    for url in (vector["download_url"], vector["zip_url"], vector["png_download_url"]):
        response = client.get(url)
        assert response.status_code == 200 and "content-disposition" not in response.headers, url
