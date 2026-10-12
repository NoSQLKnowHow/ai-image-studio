"""What `GET /api/capabilities` says about Vector (DESIGN.md §31.12.7): the tab is offered (`modes`) only when the image pipeline can draw AND the clean-up's libraries
import, `vector` says whether and, if not, why (as `music` does), and `limits.vector` is built from the constants that the engine, the server and the page share.
What these protect: the page must not offer a tab that cannot work, must be able to explain one that is off, and must get the form's limits from one place.
"""

from __future__ import annotations

import time

import pytest
from vector_server_support import CONTRACT, drain, libraries_import, subscribe, wait_for_capability, walk_types  # noqa: F401

from studio import vector as V

EXPECTED = CONTRACT["capabilities"]


def settle(client, timeout: float = 15.0) -> None:
    """Wait for the start-up checks of both pipelines to finish (they run in the background), so that a test can change what they found."""
    jobs = client.app.state.jobs
    deadline = time.monotonic() + timeout
    while jobs._probe.get("state") not in ("done", "failed") or jobs._music_probe.get("state") not in ("done", "failed"):
        assert time.monotonic() < deadline, (jobs._probe, jobs._music_probe)
        time.sleep(0.02)


# ------------------------------------------------------------------ offered, and the contract's shape
def test_vector_is_offered_when_the_libraries_import_and_the_image_pipeline_can_draw(client_factory, libraries_import):
    client = client_factory()
    settle(client)
    caps = client.get("/api/capabilities").json()
    assert caps["modes"] == EXPECTED["modes"] == ["generate", "edit", "music", "vector"]
    assert caps["vector"] == EXPECTED["vector"] == {"available": True, "reason": None, "hint": None}
    assert wait_for_capability(client, "vector")["modes"] == caps["modes"]


def test_the_capabilities_have_the_keys_and_types_of_the_contract(client_factory, libraries_import):
    client = client_factory()
    settle(client)
    caps = client.get("/api/capabilities").json()
    assert walk_types(caps["vector"]) == walk_types(EXPECTED["vector"])
    assert caps["limits"]["vector"] == EXPECTED["limits_vector"]  # the same numbers, from the constants (5 and 5.0 are the same number)
    assert walk_types(caps["limits"]["vector"]) == walk_types(EXPECTED["limits_vector"])
    assert "vector" in caps["limits"] and {"music", "size", "steps"} <= set(caps["limits"])  # and the other limits are still there


def test_the_limits_are_built_from_the_constants_every_one_of_them(client_factory, monkeypatch):
    """Each constant is given a value of its own, so that a limit read from the wrong constant (or written as a number) cannot go unseen."""
    for name, value in dict(
            COLOURS_MIN=3, COLOURS_MAX=7, COLOURS_DEFAULT=4, SIZE_MM_MIN=6.0, SIZE_MM_MAX=1100.0, SIZE_MM_DEFAULT=120.0, VERSIONS_MIN=2, VERSIONS_MAX=3,
            MIN_FEATURE_MM_MIN=0.06, MIN_FEATURE_MM_MAX=9.0, MIN_FEATURE_MM_DEFAULT=0.7, KERF_MM_MIN=0.1, KERF_MM_MAX=0.9, KERF_MM_DEFAULT=0.2,
            BLEED_MM_MIN=0.2, BLEED_MM_MAX=4.0, BLEED_MM_DEFAULT=0.8, SHAPE_LAYER_CAP=11, RECIPE_MAX_CHARS=1234, KIND_DEFAULT="line",
            LAYERS_BY_DEFAULT="shape", ARRANGEMENT_DEFAULT="stacked", KINDS=("a", "b"), LAYERS_BY=("x",), ARRANGEMENTS=("p", "q"),
            PRESETS=({"id": "p1", "name": "P1", "width_mm": 11, "height_mm": 22},)).items():
        monkeypatch.setattr(V, name, value)
    limits = client_factory().get("/api/capabilities").json()["limits"]["vector"]
    assert limits == {
        "colours": {"min": 3, "max": 7, "default": 4}, "size_mm": {"min": 6.0, "max": 1100.0, "default": [120.0, 120.0]},
        "versions": {"min": 2, "max": 3}, "min_feature_mm": {"min": 0.06, "max": 9.0, "default": 0.7},
        "kerf_mm": {"min": 0.1, "max": 0.9, "default": 0.2}, "bleed_mm": {"min": 0.2, "max": 4.0, "default": 0.8}, "shape_layer_cap": 11,
        "kinds": ["a", "b"], "layers_by": ["x"], "arrangements": ["p", "q"], "presets": [{"id": "p1", "name": "P1", "width_mm": 11, "height_mm": 22}],
        "defaults": {"kind": "line", "layers_by": "shape", "arrangement": "stacked"}, "recipe_max_chars": 1234}


def test_the_limits_do_not_change_with_the_server_settings_that_belong_to_pictures(client_factory):
    """The number of versions is the Vector tab's own (1 to 4), not STUDIO_MAX_IMAGES_PER_RUN's."""
    limits = client_factory(max_images_per_run=2).get("/api/capabilities").json()["limits"]
    assert limits["vector"]["versions"] == {"min": 1, "max": 4} and limits["num_images"] == {"min": 1, "max": 2}


# ------------------------------------------------------------------ not offered, and why
def test_vector_is_not_offered_without_the_libraries_and_the_page_is_told_why(client_factory, monkeypatch):
    monkeypatch.setattr("studio.vector.available", lambda: (False, "The Vector libraries are not installed here (vtracer).", "pip install vtracer"))
    client = client_factory()
    settle(client)
    caps = client.get("/api/capabilities").json()
    assert "vector" not in caps["modes"] and caps["modes"] == ["generate", "edit", "music"]
    assert caps["vector"] == {"available": False, "reason": "The Vector libraries are not installed here (vtracer).", "hint": "pip install vtracer"}
    assert walk_types(caps["vector"]) == {".": "object", "available": "boolean", "reason": "string", "hint": "string"}


def test_vector_is_not_offered_when_the_image_pipeline_cannot_draw(client_factory, libraries_import):
    client = client_factory()
    settle(client)
    client.app.state.jobs._probe = {"state": "failed", "error": {"kind": "unavailable", "message": "No GPU is visible here.", "hint": "Check the container."}}
    caps = client.get("/api/capabilities").json()
    assert "vector" not in caps["modes"] and "generate" in caps["modes"]  # the page still lists Generate, as before: only the new tab depends on it
    assert caps["vector"] == {"available": False, "reason": "No GPU is visible here.", "hint": "Check the container."}


@pytest.mark.parametrize("libraries, probe, expected", [
    # both halves fine
    ((True, None, None), {"state": "done"}, {"available": True, "reason": None, "hint": None}),
    # the libraries are what is missing: their words
    ((False, "no libs", "install them"), {"state": "done"}, {"available": False, "reason": "no libs", "hint": "install them"}),
    # the image pipeline failed: its words, or ours if it gave none
    ((True, None, None), {"state": "failed", "error": {"message": "boom", "hint": "retry"}}, {"available": False, "reason": "boom", "hint": "retry"}),
    ((True, None, None), {"state": "failed", "error": {"message": "boom"}}, {"available": False, "reason": "boom", "hint": None}),
    ((True, None, None), {"state": "failed"}, {"available": False, "reason": "The image model cannot run here.", "hint": None}),
    ((True, None, None), {"state": "failed", "error": {"message": "", "hint": "h"}}, {"available": False, "reason": "The image model cannot run here.", "hint": "h"}),
    # the start-up check has not finished: not yet, and say so
    ((True, None, None), {"state": "pending"}, {"available": False, "reason": "The image model's start-up check has not finished yet.",
                                                "hint": "Try again in a moment."}),
    ((True, None, None), {"state": "running"}, {"available": False, "reason": "The image model's start-up check has not finished yet.",
                                                "hint": "Try again in a moment."}),
    # both are missing: the libraries are named, because that is the one a person can fix
    ((False, "no libs", "install them"), {"state": "failed", "error": {"message": "boom"}}, {"available": False, "reason": "no libs", "hint": "install them"}),
])
def test_the_vector_status_for_each_combination(client_factory, monkeypatch, libraries, probe, expected):
    client = client_factory()
    settle(client)
    jobs = client.app.state.jobs
    monkeypatch.setattr("studio.vector.available", lambda: libraries)
    jobs._probe = probe
    assert jobs.vector_status() == expected
    assert ("vector" in jobs.modes()) is expected["available"]


def test_vector_comes_last_among_the_modes_and_the_others_are_as_they_were(client_factory, libraries_import):
    client = client_factory()
    settle(client)
    jobs = client.app.state.jobs
    assert jobs.modes() == ["generate", "edit", "music", "vector"]
    jobs._music_probe = {"state": "failed"}
    assert jobs.modes() == ["generate", "edit", "vector"]  # each tab depends on its own half only


def test_the_pages_are_told_when_the_start_up_check_changes_what_is_offered(client_factory, libraries_import):
    client = client_factory()
    settle(client)
    sub = subscribe(client)
    client.portal.call(client.app.state.jobs._run_probe)  # the check again: it ends with the event
    [data] = [data for name, data in drain(client, sub) if name == "capabilities.updated"]
    assert "vector" in data["modes"] and data["vector"] == {"available": True, "reason": None, "hint": None}
    assert {"supports", "modes", "music", "vector"} <= set(data)
