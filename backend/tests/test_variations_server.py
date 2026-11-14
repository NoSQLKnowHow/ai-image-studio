"""More like this, the server's side (DESIGN.md §43.3 to §43.6; criteria 335 to 340): a run can be made as a variation of another, the link and the family it
belongs to are stored and shown, a family can be listed, and the two kinds of variation have the rules the spec gives them.

What these protect: a variation always knows where it came from, however deep the chain goes; a missing source, a source in the bin, a picture that is not the
source's, and a kind of run that cannot have variations are each refused in words and with the right status (the page shows the words beside the dialog); *Variations
of this picture* cannot be made until the machine has been told it works; and nothing that is not a variation is changed (its payload says so, plainly).

All of it runs on the fake pipeline (no GPU), like the other server tests."""

from __future__ import annotations

import pytest
from conftest import create_edit, create_run, wait_for

from studio.config import Settings
from studio.runfilter import RunFilter
from studio.runspec import RunCreate, RunRequestError, resolve_run

NO_RUN = "0" * 32  # a well-formed id that no run has


def finished(client, prompt="a lighthouse at dusk", **options):
    """A finished picture run, made the way the page makes one."""
    run = create_run(client, prompt, **options)
    return wait_for(client, run["id"])


def variation_body(source, how="seeds", image_id=None, mode="generate", **extra):
    """The request More like this sends: the source run's own words and a `variation_of`. The page builds it as it builds Regenerate larger; the server only checks it."""
    body = {"mode": mode, "prompt": source["prompt"], "options": {"width": 256, "height": 256, "steps": 3}, "variation_of": {"run_id": source["id"], "image_id": image_id, "how": how}}
    body.update(extra)
    return body


def make_variation(client, source, expect=201, **kwargs):
    response = client.post("/api/runs", json=variation_body(source, **kwargs))
    assert response.status_code == expect, response.text
    return response.json()


def run_ids(client, **query):
    return [run["id"] for run in client.get("/api/runs", params={"limit": 50, **query}).json()["runs"]]


# ---- what a run says about itself ---------------------------------------------------------------------------------------------------
def test_a_run_that_is_not_a_variation_says_so_plainly(client):
    """Three keys on every picture run (§43.6): no link, no family, no variations. A page that reads them never has to guess what a missing key means."""
    run = finished(client)
    assert run["variation_of"] is None and run["family_id"] is None and run["variation_count"] == 0


def test_music_drawings_and_the_other_tabs_have_none_of_the_three_keys(client):
    """Their payloads are the contracts of 1.17 to 1.19 byte for byte (the same reason the Speech keys are only on Speech runs)."""
    music = client.post("/api/runs", json={"mode": "music", "prompt": "Genre: ambient.", "options": {"steps": 20}})
    assert music.status_code == 201, music.text
    assert not {"variation_of", "family_id", "variation_count"} & set(music.json())
    wait_for(client, music.json()["id"])


def test_a_variation_records_where_it_came_from_and_the_family_it_belongs_to(client):
    source = finished(client)
    picture = source["images"][0]["id"]
    variation = make_variation(client, source, image_id=picture)
    assert variation["variation_of"] == {"run_id": source["id"], "image_id": picture, "how": "seeds",
                                         "source": {"prompt": source["prompt"], "state": "present"}}
    assert variation["family_id"] == source["id"]
    # it is stored, not made up when asked: a fresh read says the same, and so does the list
    again = client.get(f"/api/runs/{variation['id']}").json()
    assert again["variation_of"] == variation["variation_of"] and again["family_id"] == source["id"]
    # and the source now has one variation
    wait_for(client, variation["id"])
    assert client.get(f"/api/runs/{source['id']}").json()["variation_count"] == 1


def test_a_variation_from_a_card_has_no_picture(client):
    """The button on a card means the run, not a picture (§43.3): `image_id` is null, and the link is still made."""
    source = finished(client)
    variation = make_variation(client, source)
    assert variation["variation_of"]["image_id"] is None and variation["variation_of"]["run_id"] == source["id"]


def test_a_variation_of_a_variation_belongs_to_the_first_runs_family(client):
    """The family is flat however deep the chain goes (§43.4): the first variation's link says which run it is More like this of, and the second's `family_id` is
    still the first run, so that the family view shows the whole tree and only the first run has a count."""
    root = finished(client)
    first = wait_for(client, make_variation(client, root)["id"])
    second = make_variation(client, first)
    assert second["variation_of"]["run_id"] == first["id"] and second["family_id"] == root["id"]
    wait_for(client, second["id"])
    assert client.get(f"/api/runs/{root['id']}").json()["variation_count"] == 2
    assert client.get(f"/api/runs/{first['id']}").json()["variation_count"] == 0


def test_a_variation_of_an_edit_run_is_more_takes_of_the_edit(client):
    """More takes of an edit is the edit again with new seeds (§43.3): an edit run can be a source, and its variation is an edit run of the same kind."""
    start = finished(client)
    edit = create_edit(client, "make it blue", [{"image_id": start["images"][0]["id"]}])
    edit = wait_for(client, edit["id"])
    variation = client.post("/api/runs", json={"mode": "edit", "prompt": "make it blue", "input_images": [{"image_id": start["images"][0]["id"]}],
                                                "options": {"steps": 3}, "variation_of": {"run_id": edit["id"], "image_id": None, "how": "seeds"}})
    assert variation.status_code == 201, variation.text
    assert variation.json()["mode"] == "edit" and variation.json()["variation_of"]["run_id"] == edit["id"]


# ---- families -----------------------------------------------------------------------------------------------------------------------
def test_the_family_filter_lists_the_first_run_and_everything_made_from_it(client):
    root = finished(client)
    first = wait_for(client, make_variation(client, root)["id"])
    second = wait_for(client, make_variation(client, first)["id"])
    other = finished(client, "something else entirely")  # not in the family
    assert sorted(run_ids(client, family=root["id"])) == sorted([root["id"], first["id"], second["id"]])
    assert other["id"] not in run_ids(client, family=root["id"])
    assert run_ids(client, family=NO_RUN) == []  # an id that is not a run matches nothing, and is not an error


def test_the_family_filter_has_the_newest_run_first_like_every_other_list(client):
    root = finished(client)
    first = wait_for(client, make_variation(client, root)["id"])
    second = wait_for(client, make_variation(client, root)["id"])
    assert run_ids(client, family=root["id"]) == [second["id"], first["id"], root["id"]]


def test_a_family_id_that_is_not_an_id_is_refused(client):
    assert client.get("/api/runs", params={"family": "not-an-id"}).status_code == 422
    assert client.get("/api/runs", params={"family": "none"}).status_code == 422  # unlike `project`, there is no "none" for a family


def test_the_filter_is_one_bound_condition_on_both_columns():
    """The SQL, as a unit: the first run's own id, or any run whose family is it. The id is bound twice (never pasted into the text), after the bin rule."""
    clauses, values = RunFilter(family=NO_RUN).conditions()
    assert clauses == ["deleted_at IS NULL", "(id = ? OR family_id = ?)"] and values == [NO_RUN, NO_RUN]
    assert RunFilter().conditions() == (["deleted_at IS NULL"], [])  # no family means no condition at all


def test_a_variation_in_the_bin_is_not_counted_and_not_listed(client):
    """The count and the family view are of the history (§43.4): a variation sent to the bin is in the Deleted view, not here, and comes back when restored."""
    root = finished(client)
    first = wait_for(client, make_variation(client, root)["id"])
    second = wait_for(client, make_variation(client, root)["id"])
    assert client.get(f"/api/runs/{root['id']}").json()["variation_count"] == 2
    assert client.post(f"/api/runs/{first['id']}/bin").status_code == 200
    assert client.get(f"/api/runs/{root['id']}").json()["variation_count"] == 1
    assert run_ids(client, family=root["id"]) == [second["id"], root["id"]]
    assert client.post(f"/api/runs/{first['id']}/restore").status_code == 200
    assert client.get(f"/api/runs/{root['id']}").json()["variation_count"] == 2


# ---- every page is told when a run's variations change ------------------------------------------------------------------------------------
def told_about(client, run_id, action):
    """Run `action` and return, in order, the `variation_count` that every `run.updated` event for `run_id` carried: what an open page would draw on that run's card."""
    sub = client.app.state.bus.subscribe()
    try:
        action()
        counts = []
        while not sub.queue.empty():
            name, data = sub.queue.get_nowait()
            if name == "run.updated" and data["id"] == run_id:
                counts.append(data["variation_count"])
        return counts
    finally:
        client.app.state.bus.unsubscribe(sub)


def test_the_first_run_is_told_to_every_page_again_when_a_variation_is_made(client):
    """A card's Variations (n) chip is the first run's own number, which changes when a variation is made on ANOTHER run: without this event the chip appears only after a reload,
    which is what a browser test of the card found."""
    root = finished(client)
    assert told_about(client, root["id"], lambda: make_variation(client, root)) == [1]
    first = wait_for(client, client.get("/api/runs", params={"limit": 1}).json()["runs"][0]["id"])
    assert told_about(client, root["id"], lambda: make_variation(client, first)) == [2]  # a variation of a variation counts for the first run too (the family is flat)


def test_the_first_run_is_told_again_when_a_variation_is_binned_restored_or_deleted_for_good(client):
    root = finished(client)
    variation = wait_for(client, make_variation(client, root)["id"])
    assert told_about(client, root["id"], lambda: client.post(f"/api/runs/{variation['id']}/bin")) == [0]
    assert told_about(client, root["id"], lambda: client.post(f"/api/runs/{variation['id']}/restore")) == [1]
    assert told_about(client, root["id"], lambda: client.delete(f"/api/runs/{variation['id']}")) == [0]


def test_a_run_that_is_not_a_variation_tells_no_other_run_anything(client):
    """Nothing to refresh: making, binning or deleting an ordinary run publishes about that run only."""
    other = finished(client, "a different picture")
    run = finished(client)
    assert told_about(client, other["id"], lambda: (client.post(f"/api/runs/{run['id']}/bin"), client.delete(f"/api/runs/{run['id']}"))) == []


# ---- what becomes of the source -----------------------------------------------------------------------------------------------------
def test_a_variation_says_when_its_source_is_in_the_bin_or_gone(client):
    """§43.4, criterion 338: the link stays as it was made and the card says what has become of the source; nothing breaks and the variation keeps its words."""
    root = finished(client)
    variation = wait_for(client, make_variation(client, root)["id"])
    assert client.get(f"/api/runs/{variation['id']}").json()["variation_of"]["source"] == {"prompt": root["prompt"], "state": "present"}
    assert client.post(f"/api/runs/{root['id']}/bin").status_code == 200
    assert client.get(f"/api/runs/{variation['id']}").json()["variation_of"]["source"] == {"prompt": root["prompt"], "state": "in_bin"}
    assert client.delete(f"/api/runs/{root['id']}").status_code == 204  # for good
    after = client.get(f"/api/runs/{variation['id']}").json()
    assert after["variation_of"]["source"] == {"prompt": None, "state": "gone"}
    assert after["variation_of"]["run_id"] == root["id"] and after["family_id"] == root["id"]  # the link itself is untouched


# ---- what is refused ----------------------------------------------------------------------------------------------------------------
def test_a_source_that_is_not_there_is_a_404_with_its_own_code(client):
    body = client.post("/api/runs", json=variation_body({"id": NO_RUN, "prompt": "x"}))
    assert body.status_code == 404 and body.json()["code"] == "variation_source_not_found"


def test_a_source_in_the_bin_is_a_409_and_makes_no_run(client):
    source = finished(client)
    assert client.post(f"/api/runs/{source['id']}/bin").status_code == 200
    before = len(run_ids(client))
    response = client.post("/api/runs", json=variation_body(source))
    assert response.status_code == 409 and response.json()["code"] == "variation_source_in_bin" and "Restore it first" in response.json()["detail"]
    assert len(run_ids(client)) == before  # nothing was queued


def test_a_picture_that_is_not_one_of_the_sources_is_refused_in_words(client):
    source, other = finished(client), finished(client, "another picture")
    response = client.post("/api/runs", json=variation_body(source, image_id=other["images"][0]["id"]))
    assert response.status_code == 422
    assert [(item["loc"], item["msg"]) for item in response.json()["detail"]] == [(["body", "variation_of", "image_id"], "That picture is not one of the run's pictures.")]


def test_a_music_run_cannot_have_variations(client):
    music = client.post("/api/runs", json={"mode": "music", "prompt": "Genre: ambient.", "options": {"steps": 20}}).json()
    wait_for(client, music["id"])
    response = client.post("/api/runs", json=variation_body(music))
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "variation_of", "run_id"] and "picture run" in response.json()["detail"][0]["msg"]


@pytest.mark.parametrize("mode, options", [("music", {"steps": 20}), ("vector", {})])
def test_music_and_drawing_requests_cannot_carry_a_link(mode, options):
    """From the request alone: only picture runs are variations (the page never sends one for another tab, and the server will not store one)."""
    request = RunCreate.model_validate({"mode": mode, "prompt": "x", "options": options, "variation_of": {"run_id": NO_RUN, "how": "seeds"}})
    with pytest.raises(RunRequestError) as refused:
        resolve_run(request, Settings())
    assert [(loc, msg) for loc, msg in refused.value.errors][0] == (("variation_of",), "Only used for pictures.")


def test_a_malformed_link_is_refused_by_the_framework(client):
    for link in ({"run_id": "nope", "how": "seeds"}, {"run_id": NO_RUN, "how": "twins"}, {"run_id": NO_RUN}, {"run_id": NO_RUN, "how": "seeds", "extra": 1}):
        body = {"mode": "generate", "prompt": "x", "options": {"steps": 3}, "variation_of": link}
        assert client.post("/api/runs", json=body).status_code == 422, link


# ---- Variations of this picture: off until switched on -------------------------------------------------------------------------------
def picture_body(source, **extra):
    return {"mode": "edit", "prompt": source["prompt"], "input_images": [{"image_id": source["images"][0]["id"]}], "options": {"steps": 3},
            "variation_of": {"run_id": source["id"], "image_id": source["images"][0]["id"], "how": "picture"}, **extra}


def test_variations_of_a_picture_are_refused_until_the_machine_is_told_they_work(client):
    """§43.3, criterion 335: the editing model has never been run on the Spark for this. The refusal is in words and names the field the dialog shows it beside."""
    source = finished(client)
    response = client.post("/api/runs", json=picture_body(source))
    assert response.status_code == 422
    assert [(item["loc"], item["msg"]) for item in response.json()["detail"]] == [
        (["body", "variation_of", "how"], "Variations of a picture have not been switched on on this machine.")]


def test_variations_of_a_picture_are_made_when_switched_on_and_are_edit_runs(client_factory):
    client = client_factory(variations_from_picture=True)
    source = finished(client)
    response = client.post("/api/runs", json=picture_body(source))
    assert response.status_code == 201, response.text
    made = response.json()
    assert made["mode"] == "edit" and made["variation_of"]["how"] == "picture" and made["family_id"] == source["id"]
    assert [item["id"] for item in made["inputs"]] != [] and len(made["inputs"]) == 1  # the picked picture went in as the input


def test_a_picture_variation_that_is_not_an_edit_is_refused_even_when_switched_on(client_factory):
    client = client_factory(variations_from_picture=True)
    source = finished(client)
    body = picture_body(source)
    body.update(mode="generate", input_images=None)
    response = client.post("/api/runs", json=body)
    assert response.status_code == 422 and "edit of it" in response.json()["detail"][0]["msg"]


def test_more_takes_needs_no_switch(client):
    """The other kind works on every machine: it is the model that is already loaded, with new seeds."""
    source = finished(client)
    assert make_variation(client, source, how="seeds")["variation_of"]["how"] == "seeds"


# ---- the capability -----------------------------------------------------------------------------------------------------------------
def test_the_capability_says_whether_a_picture_variation_can_be_made_and_why_not(client_factory):
    off = client_factory().get("/api/capabilities").json()["variations"]
    assert off == {"from_picture": False, "reason": "Not tried on this machine yet."}
    on = client_factory(variations_from_picture=True).get("/api/capabilities").json()["variations"]
    assert on == {"from_picture": True, "reason": None}


def test_the_setting_is_read_from_the_environment():
    assert Settings.from_env({}).variations_from_picture is False
    assert Settings.from_env({"STUDIO_VARIATIONS_FROM_PICTURE": "1"}).variations_from_picture is True
    assert Settings.from_env({"STUDIO_VARIATIONS_FROM_PICTURE": "false"}).variations_from_picture is False
