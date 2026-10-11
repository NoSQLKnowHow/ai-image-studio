"""The Vector request (DESIGN.md §31.12.5): `mode: "vector"` and its options, their limits and what is refused, the words the studio sends, the seeds, the size
of the drawing, and `POST /api/vector/recipe`. What these protect: a bad request is refused with a `422` that names the field (so the form can show the
message beside the control), a good one is stored as the contract's `run.options`, and nothing that means nothing for a Vector run gets through.

Most of it is checked on `resolve_run` itself (no server, quick); the HTTP-level tests are for what only the framework does (the types, the unknown keys, the
answers).
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from conftest import create_run, wait_for
from vector_server_support import CONTRACT, create_vector, problems, vector_body

from studio import vector as V
from studio.config import Settings
from studio.runspec import RunCreate, RunRequestError, resolve_run

SETTINGS = Settings()


def resolve(prompt: str = "a fox head", mode: str = "vector", settings: Settings = SETTINGS, **options):
    """`resolve_run` on a body made like the page's. Raises `RunRequestError`."""
    return resolve_run(RunCreate.model_validate({"mode": mode, "prompt": prompt, "options": options}), settings)


def refused(**options) -> dict[str, str]:
    """The field errors of a request that is not allowed, as {"options.colours": "message"}."""
    with pytest.raises(RunRequestError) as caught:
        resolve(**options)
    return {".".join(str(p) for p in loc): message for loc, message in caught.value.errors}


# ------------------------------------------------------------------ the request of the contract, and the defaults
def test_the_contracts_request_is_accepted_as_it_is(client):
    body = json.loads(json.dumps(CONTRACT["create_request"]))
    body["options"]["steps"] = 3  # the same request, with the fake model's few steps
    response = client.post("/api/runs", json=body)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["mode"] == "vector" and run["prompt"] == "a fox head" and run["status"] in ("queued", "running", "done")
    wait_for(client, run["id"])


def test_every_option_has_its_default(client):
    run = resolve()
    options = run.options_snapshot()
    recipe = V.build_recipe("a fox head", "silhouette", 2, ())
    assert options == {
        "colours": 2, "palette": [], "kind": "silhouette", "width_mm": 100.0, "height_mm": 100.0, "layers_by": "colour", "arrangement": "side_by_side",
        "bleed_mm": 0.5, "min_feature_mm": 0.5, "kerf_mm": 0.0, "steps": 40, "seed": options["seed"], "num_images": 1, "recipe": recipe.positive,
        "seed_was_random": True, "negative": recipe.negative, "draw_width": 1536, "draw_height": 1536}
    assert 0 <= options["seed"] <= 2**32 - 1


def test_a_vector_run_is_an_image_run_for_the_worker_with_the_studios_own_words_and_guidance():
    run = resolve("  a fox head  ", seed=9, num_images=3)
    recipe = V.build_recipe("a fox head", "silhouette", 2, ())
    assert run.mode == "vector" and run.prompt == "a fox head"  # the idea, trimmed: it names the files and the card
    assert run.effective_prompt == recipe.positive and run.negative_prompt == recipe.negative  # what the model is sent
    assert run.cfg_scale == V.RECIPE_CFG_SCALE == 4.0  # guidance is always sent: without it the negative text counts for nothing
    assert (run.width, run.height) == (1536, 1536) and run.transparent is False and run.inputs == ()
    assert run.seeds == [9, 10, 11] and run.num_images == 3 and run.steps == 40


def test_the_options_are_the_ones_sent_and_the_snapshot_is_the_contracts_run_options():
    run = resolve(**{**CONTRACT["create_request"]["options"], "recipe": None})
    options = run.options_snapshot()
    assert list(options) == list(CONTRACT["run"]["options"])  # the same keys in the same order
    sent = CONTRACT["create_request"]["options"]
    for key, value in sent.items():
        if key != "recipe":
            assert options[key] == value, key
    assert options["seed_was_random"] is False and options["draw_width"] == 1536 and options["draw_height"] == 1536
    assert type(options["width_mm"]) is float and type(options["palette"]) is list  # a list, as JSON has no tuple, and a real number


def test_the_size_of_the_drawing_follows_the_shape_of_the_sheet():
    """The long side of the drawing is 1536 pixels and the other follows the sheet: width and height must not be swapped on the way."""
    assert (lambda r: (r.width, r.height))(resolve(width_mm=200, height_mm=100)) == V.draw_size(200, 100) == (1536, 768)
    assert (lambda r: (r.width, r.height))(resolve(width_mm=100, height_mm=200)) == V.draw_size(100, 200) == (768, 1536)
    run = resolve(width_mm=600, height_mm=307)  # the xTool P2 bed
    assert (run.width, run.height) == V.draw_size(600, 307)
    assert run.options_snapshot()["draw_width"] == run.width and run.options_snapshot()["draw_height"] == run.height


def test_a_whole_number_of_millimetres_is_kept_as_a_real_number():
    options = resolve(width_mm=150, height_mm=75, bleed_mm=1, min_feature_mm=1, kerf_mm=1).options_snapshot()
    assert [type(options[k]) for k in ("width_mm", "height_mm", "bleed_mm", "min_feature_mm", "kerf_mm")] == [float] * 5


# ------------------------------------------------------------------ the limits: each edge, in and out
# (option, a value just inside each end, a value just outside each end): the numbers are the constants of `studio/vector/__init__.py`
EDGES = [
    ("colours", (2, 8), (1, 9)),
    ("width_mm", (5, 1200), (4.99, 1200.01)),
    ("height_mm", (5, 1200), (4.99, 1200.01)),
    ("min_feature_mm", (0.05, 10), (0.04, 10.01)),
    ("kerf_mm", (0, 1), (-0.01, 1.01)),
    ("bleed_mm", (0, 5), (-0.01, 5.01)),
    ("steps", (1, 100), (0, 101)),
    ("num_images", (1, 4), (0, 5)),
    ("seed", (0, 2**32 - 1), (-1, 2**32)),
]


@pytest.mark.parametrize("option, inside, outside", EDGES, ids=[edge[0] for edge in EDGES])
def test_each_limit_accepts_its_edges_and_refuses_what_is_just_past_them(option, inside, outside):
    for value in inside:
        resolve(**{option: value})  # does not raise
    for value in outside:
        found = refused(**{option: value})
        assert list(found) == [f"options.{option}"], (option, value, found)  # the field is named, and nothing else is blamed
        assert "Must be between" in found[f"options.{option}"]


def test_the_wording_of_a_refused_number_is_the_engines_own():
    """The words come from `vector.check_options`, so that the page and the engine say the same thing wherever the problem is found."""
    found = refused(colours=9, width_mm=2000.0, kerf_mm=2.0)
    assert found == {"options.colours": "Must be between 2 and 8.", "options.width_mm": "Must be between 5 and 1200.",
                     "options.kerf_mm": "Must be between 0 and 1."}
    expected = dict(V.check_options(V.VectorOptions(colours=9, width_mm=2000.0, kerf_mm=2.0)))
    assert {key.removeprefix("options."): message for key, message in found.items()} == expected


def test_the_number_of_versions_is_one_to_four_whatever_the_picture_limit_is():
    """A Vector run's versions have their own limit (the picture limit, STUDIO_MAX_IMAGES_PER_RUN, is for Generate and Edit)."""
    assert resolve(num_images=4, settings=Settings(max_images_per_run=2)).num_images == 4  # more than the picture limit allows: fine
    assert resolve(num_images=1, settings=Settings(max_images_per_run=8)).num_images == 1
    assert "Must be between 1 and 4." == refused(num_images=5)["options.num_images"]  # and fewer than the picture limit allows is no longer an excuse


def test_every_problem_is_reported_at_once():
    found = refused(colours=1, steps=0, num_images=9, seed=-5, kind="blob", layers_by="stripe", arrangement="pile", palette=["red"])
    assert set(found) == {"options.colours", "options.steps", "options.num_images", "options.seed", "options.kind", "options.layers_by",
                          "options.arrangement", "options.palette"}


def test_the_text_options_must_be_one_of_the_known_words():
    for name in ("kind", "layers_by", "arrangement"):
        assert "Must be one of" in refused(**{name: "nonsense"})[f"options.{name}"]
    for kind in V.KINDS:
        assert resolve(kind=kind).options_snapshot()["kind"] == kind
    for by in V.LAYERS_BY:
        assert resolve(layers_by=by).options_snapshot()["layers_by"] == by
    for arrangement in V.ARRANGEMENTS:
        assert resolve(arrangement=arrangement).options_snapshot()["arrangement"] == arrangement


# ------------------------------------------------------------------ the palette
def test_the_palette_lists_the_inks_and_white_counts_as_a_colour():
    assert resolve(colours=2, palette=["#FF0000"]).options_snapshot()["palette"] == ["#FF0000"]  # 2 colours = white and one ink
    assert resolve(colours=2, palette=[]).options_snapshot()["palette"] == []  # fewer than the room there is: the picture chooses
    assert resolve(colours=8, palette=[f"#{n}{n}0000" for n in "123456A"]).options_snapshot()["palette"][-1] == "#AA0000"  # seven inks and white
    found = refused(colours=2, palette=["#FF0000", "#00FF00"])  # two inks and white is three colours
    assert list(found) == ["options.palette"] and "At most 1 colour" in found["options.palette"]
    assert "At most 7 colours" in refused(colours=8, palette=[f"#{n}00000" for n in "12345678"])["options.palette"]


@pytest.mark.parametrize("colour, why", [("#ff0000", "not a colour"), ("red", "not a colour"), ("#FF00", "not a colour"), ("#GG0000", "not a colour"),
                                         ("#FFFFFF", "White is always included"), ("", "not a colour")])
def test_a_palette_colour_must_be_an_uppercase_hex_colour_and_never_white(colour, why):
    found = refused(colours=3, palette=[colour])
    assert list(found) == ["options.palette"] and why in found["options.palette"]


def test_a_colour_listed_twice_is_refused():
    assert "listed twice" in refused(colours=3, palette=["#FF0000", "#FF0000"])["options.palette"]


def test_the_palette_is_checked_against_the_number_of_colours_given_in_the_same_request():
    assert resolve(colours=3, palette=["#FF0000", "#00FF00"]).num_images == 1
    assert "options.palette" in refused(palette=["#FF0000", "#00FF00"])  # the default is 2 colours, so one ink


# ------------------------------------------------------------------ what a Vector run does not take
NOT_FOR_VECTOR = {"width": 512, "height": 512, "negative_prompt": "blurry", "cfg_scale": 3.5, "transparent": True, "resolution": 1024, "shape_from": 1,
                  "draft": True, "full": {"width": 1024, "height": 1024, "steps": 20}, "duration": 30, "tracks": 2, "fields": {"genre": "jazz"}}


@pytest.mark.parametrize("name", sorted(NOT_FOR_VECTOR))
def test_an_option_that_means_nothing_for_vector_is_refused_and_named(name):
    assert refused(**{name: NOT_FOR_VECTOR[name]}) == {f"options.{name}": "Not used for Vector."}


def test_an_option_that_means_nothing_is_refused_even_when_it_is_null():
    """As for music: the page should not send it at all, and hears about it if it does."""
    assert refused(width=None) == {"options.width": "Not used for Vector."}


def test_lyrics_and_input_images_are_refused():
    body = {"mode": "vector", "prompt": "a fox", "lyrics": "la la", "input_images": [{"upload_id": "a" * 32}]}
    with pytest.raises(RunRequestError) as caught:
        resolve_run(RunCreate.model_validate(body), SETTINGS)
    assert {".".join(str(p) for p in loc): message for loc, message in caught.value.errors} == {
        "lyrics": "Lyrics are only used for music.", "input_images": "Images are only used in Edit mode."}


def test_an_option_of_another_kind_of_run_is_refused_by_the_vector_run_through_the_api(client):
    for name, value in (("duration", 30), ("tracks", 2)):
        found = problems(client.post("/api/runs", json=vector_body(**{name: value})))
        assert found == {f"options.{name}": "Not used for Vector."}


@pytest.mark.parametrize("name, value", [("colours", 3), ("palette", ["#FF0000"]), ("kind", "flat"), ("width_mm", 50.0), ("height_mm", 50.0),
                                         ("layers_by", "shape"), ("arrangement", "stacked"), ("bleed_mm", 1.0), ("min_feature_mm", 1.0), ("kerf_mm", 0.1),
                                         ("recipe", "words")])
@pytest.mark.parametrize("mode", ["generate", "music"])
def test_a_vector_option_sent_with_another_kind_of_run_is_refused_and_named(client, mode, name, value):
    body = {"mode": mode, "prompt": "Genre: ambient." if mode == "music" else "a fox", "options": {"steps": 20 if mode == "music" else 3, name: value}}
    assert problems(client.post("/api/runs", json=body)) == {f"options.{name}": "Only used for Vector."}


def test_a_vector_option_sent_with_an_edit_is_refused_too(client):
    body = {"mode": "edit", "prompt": "make it blue", "input_images": [{"upload_id": "a" * 32}], "options": {"colours": 3}}
    assert problems(client.post("/api/runs", json=body))["options.colours"] == "Only used for Vector."


def test_an_unknown_option_is_refused_by_the_framework_as_for_the_other_modes(client):
    """`fill_mode` and `shape_layer_cap` are the engine's, not the form's (§31.12.9 item 7): they are not request options, and neither is anything else."""
    for name in ("fill_mode", "shape_layer_cap", "negative", "draw_width", "seed_was_random", "surprise"):
        response = client.post("/api/runs", json=vector_body(**{name: 1}))
        assert response.status_code == 422 and any(item["loc"][-1] == name for item in response.json()["detail"]), name


@pytest.mark.parametrize("options", [{"colours": "4"}, {"colours": 4.0}, {"colours": True}, {"palette": "#FF0000"}, {"palette": [1]}, {"width_mm": "100"},
                                     {"kind": 3}, {"recipe": 5}, {"num_images": "2"}, {"kerf_mm": None, "bleed_mm": "x"}])
def test_the_types_are_strict(client, options):
    response = client.post("/api/runs", json=vector_body(**options))
    assert response.status_code == 422


def test_a_whole_number_is_a_good_number_of_millimetres_and_is_stored_as_a_real_one(client):
    run = create_vector(client, width_mm=150, height_mm=75)
    assert run["options"]["width_mm"] == 150.0 and run["options"]["height_mm"] == 75.0
    wait_for(client, run["id"])


# ------------------------------------------------------------------ the idea
def test_the_idea_is_required():
    for blank in ("", "   ", "\n\t"):
        assert refused(prompt=blank) == {"prompt": "Describe what to draw."}


def test_the_idea_has_the_prompt_limit_of_the_other_modes():
    settings = Settings(max_prompt_chars=300)
    assert resolve("x" * 100, settings=settings).prompt == "x" * 100
    assert resolve("x" * 300, settings=settings).prompt == "x" * 300  # exactly the limit is allowed
    with pytest.raises(RunRequestError) as caught:
        resolve("x" * 301, settings=settings)
    assert caught.value.errors == [(("prompt",), "The prompt is 301 characters; the limit is 300.")]


# ------------------------------------------------------------------ the recipe: built, or the page's edit
def test_the_recipe_is_built_from_the_idea_the_kind_the_colours_and_the_palette():
    run = resolve("a fox head", kind="flat", colours=4, palette=["#FF0000", "#00AA00"])
    assert run.effective_prompt == V.build_recipe("a fox head", "flat", 4, ("#FF0000", "#00AA00")).positive
    assert run.negative_prompt == V.build_recipe("a fox head", "flat", 4, ("#FF0000", "#00AA00")).negative


def test_an_edited_recipe_is_what_is_sent_and_the_negative_is_still_the_studios():
    run = resolve("a fox head", recipe="  My own words, flat and bold.  ", colours=3)
    assert run.effective_prompt == "My own words, flat and bold."  # trimmed
    assert run.negative_prompt == V.build_recipe("a fox head", "silhouette", 3, ()).negative
    assert run.options_snapshot()["recipe"] == "My own words, flat and bold." and run.prompt == "a fox head"  # the idea still names the files


def test_a_blank_recipe_is_no_edit():
    assert resolve("a fox head", recipe="   ").effective_prompt == resolve("a fox head").effective_prompt
    assert resolve("a fox head", recipe="").effective_prompt == resolve("a fox head").effective_prompt


def test_an_edited_recipe_may_be_as_long_as_the_limit_and_no_longer():
    assert len(resolve(recipe="x" * V.RECIPE_MAX_CHARS).effective_prompt) == V.RECIPE_MAX_CHARS == 2000
    assert refused(recipe="x" * (V.RECIPE_MAX_CHARS + 1)) == {"options.recipe": "The recipe is 2001 characters; the limit is 2000."}
    assert len(resolve(recipe=" " + "x" * V.RECIPE_MAX_CHARS + " ").effective_prompt) == 2000  # the limit is of the trimmed text


def test_a_built_recipe_is_checked_for_length_too_so_that_reuse_can_send_it_back():
    """A recipe the server builds is stored as `options.recipe`, and Reuse sends that back as an edit: it may not be longer than an edit may be."""
    overhead = len(V.build_recipe("x", "silhouette", 2, ()).positive) - 1
    longest = V.RECIPE_MAX_CHARS - overhead
    assert len(resolve("x" * longest, settings=Settings(max_prompt_chars=10_000)).effective_prompt) == V.RECIPE_MAX_CHARS
    with pytest.raises(RunRequestError) as caught:
        resolve("x" * (longest + 1), settings=Settings(max_prompt_chars=10_000))
    [((where,), message)] = caught.value.errors
    assert where == "prompt" and f"makes {V.RECIPE_MAX_CHARS + 1} characters" in message and f"limit is {V.RECIPE_MAX_CHARS}" in message


def test_a_long_idea_is_fine_when_the_page_edited_the_words_down():
    """The length of the built text is no concern when the page sent its own."""
    assert resolve("x" * 5000, recipe="short", settings=Settings(max_prompt_chars=10_000)).effective_prompt == "short"


# ------------------------------------------------------------------ the seeds
def test_the_seeds_are_the_seed_and_the_ones_after_it():
    assert resolve(seed=100, num_images=4).seeds == [100, 101, 102, 103]
    assert resolve(seed=0, num_images=1).seeds == [0]


def test_a_random_seed_leaves_room_for_every_version_under_the_limit():
    """The most a random seed can be is the one that makes the last version's seed the very largest allowed."""
    for versions in (1, 2, 4):
        with mock.patch("secrets.randbelow", side_effect=lambda n: n - 1):  # the top of whatever range it is asked for
            run = resolve(num_images=versions)
        assert run.seed_was_random and run.seeds[-1] == 2**32 - 1, versions
        with mock.patch("secrets.randbelow", side_effect=lambda n: 0):
            assert resolve(num_images=versions).seed == 0


def test_a_chosen_seed_is_not_called_random():
    run = resolve(seed=5)
    assert run.seed == 5 and run.seed_was_random is False and run.options_snapshot()["seed_was_random"] is False


# ------------------------------------------------------------------ POST /api/vector/recipe
def recipe_request(**changes):
    body = json.loads(json.dumps(CONTRACT["recipe_request"]))
    body.update(changes)
    return body


def test_the_recipe_route_answers_the_words_the_studio_would_send(client):
    response = client.post("/api/vector/recipe", json=CONTRACT["recipe_request"])
    assert response.status_code == 200
    answer = response.json()
    assert set(answer) == set(CONTRACT["recipe_response"]) == {"positive", "negative"}
    built = V.build_recipe("a fox head", "flat", 4, ("#FF0000", "#00AA00"))
    assert answer == {"positive": built.positive, "negative": built.negative}
    assert client.post("/api/vector/recipe", json=CONTRACT["recipe_request"]).json() == answer  # the same arguments, the same text


def test_the_recipe_depends_on_each_thing_it_is_given(client):
    base = client.post("/api/vector/recipe", json=recipe_request()).json()
    for changes in ({"prompt": "a wolf head"}, {"kind": "line"}, {"colours": 3}, {"palette": ["#00AA00"]}):
        assert client.post("/api/vector/recipe", json=recipe_request(**changes)).json()["positive"] != base["positive"], changes


def test_the_recipe_route_has_the_defaults_of_a_run_and_trims_the_idea(client):
    answer = client.post("/api/vector/recipe", json={"prompt": "  a fox head  "}).json()
    assert answer == {"positive": V.build_recipe("a fox head", "silhouette", 2, ()).positive, "negative": V.build_recipe("a fox head", "silhouette", 2, ()).negative}


def test_the_recipe_route_matches_what_a_run_sends(client):
    """What the form shows is what the run sends, for the same choices."""
    answer = client.post("/api/vector/recipe", json=CONTRACT["recipe_request"]).json()
    run = resolve("a fox head", **{k: v for k, v in CONTRACT["create_request"]["options"].items() if k in ("kind", "colours", "palette")})
    assert (answer["positive"], answer["negative"]) == (run.effective_prompt, run.negative_prompt)


def test_the_recipe_route_refuses_a_bad_field_naming_it(client):
    cases = [({"prompt": "   "}, "prompt", "Describe what to draw."), ({"colours": 9}, "colours", "Must be between 2 and 8."),
             ({"colours": 1, "palette": []}, "colours", "Must be between 2 and 8."), ({"kind": "blob"}, "kind", "Must be one of"),
             ({"palette": ["red"]}, "palette", "not a colour"), ({"palette": ["#FFFFFF"]}, "palette", "White"),
             ({"colours": 2, "palette": ["#FF0000", "#00FF00"]}, "palette", "At most 1 colour")]
    for changes, field, words in cases:
        found = problems(client.post("/api/vector/recipe", json=recipe_request(**changes)))
        assert list(found) == [field] and words in found[field], (changes, found)


def test_the_recipe_route_checks_what_it_is_given_and_nothing_else(client):
    """The rest of a run's options are not its business: they are not refused here, and an unknown key is."""
    assert client.post("/api/vector/recipe", json={"prompt": "x", "colours": 8, "palette": [f"#{n}00000" for n in "1234567"]}).status_code == 200
    assert client.post("/api/vector/recipe", json=recipe_request(width_mm=100)).status_code == 422
    assert client.post("/api/vector/recipe", json=recipe_request(colours="4")).status_code == 422  # strict types, as for a run
    assert client.post("/api/vector/recipe", json={"colours": 3}).status_code == 422  # the idea is required


def test_the_recipe_route_has_the_prompt_and_recipe_limits(client_factory):
    client = client_factory(max_prompt_chars=300)
    assert client.post("/api/vector/recipe", json={"prompt": "x" * 100}).status_code == 200
    assert problems(client.post("/api/vector/recipe", json={"prompt": "x" * 301})) == {"prompt": "The prompt is 301 characters; the limit is 300."}
    overhead = len(V.build_recipe("x", "silhouette", 2, ()).positive) - 1
    wide = client_factory(max_prompt_chars=10_000)
    assert wide.post("/api/vector/recipe", json={"prompt": "x" * (V.RECIPE_MAX_CHARS - overhead)}).status_code == 200
    found = problems(wide.post("/api/vector/recipe", json={"prompt": "x" * (V.RECIPE_MAX_CHARS - overhead + 1)}))
    assert list(found) == ["prompt"] and "too long" in found["prompt"]


def test_the_recipe_route_makes_no_run_and_needs_no_model(client):
    """Pure text: nothing is queued, nothing is stored, and the worker is not started for it."""
    for _ in range(3):
        assert client.post("/api/vector/recipe", json=CONTRACT["recipe_request"]).status_code == 200
    assert client.get("/api/runs").json()["runs"] == [] and client.get("/api/status").json()["queue"]["queued"] == 0
    assert client.get("/api/status").json()["worker"]["pid"] is None


def test_the_recipe_route_is_a_post_that_needs_the_client_header(client):
    assert client.post("/api/vector/recipe", json=CONTRACT["recipe_request"], headers={"X-Studio-Client": ""}).status_code == 403
    assert client.get("/api/vector/recipe").status_code in (404, 405)


# ------------------------------------------------------------------ a picture run is not changed by any of it
def test_a_picture_run_is_as_it_was(client):
    run = create_run(client)
    finished = wait_for(client, run["id"])
    assert finished["mode"] == "generate" and len(finished["images"]) == 1 and finished["vectors"] == []
    assert "colours" not in finished["options"] and finished["options"]["width"] == 256
