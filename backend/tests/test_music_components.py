"""Which parts of the music model did not load (backend/studio/pipelines/real_music.py).

`diffusers` turns a part that fails to load into a log warning and carries on, so the loader looks for the parts itself.
This needs no PyTorch: it reads the pipeline's table of parts, so a stand-in object does for the pipeline here. The
real pipeline, with a part removed from a folder, is checked in test_real_music.py (where PyTorch and the second
`diffusers` are installed).
"""

from __future__ import annotations

from types import SimpleNamespace

from studio.pipelines.hub import is_cache_miss
from studio.pipelines.real_music import MissingComponents, unloaded_components


def spec(method: str = "from_pretrained", path: str | None = "some/repo"):
    return SimpleNamespace(default_creation_method=method, pretrained_model_name_or_path=path)


def pipe(specs: dict, **parts):
    return SimpleNamespace(_component_specs=specs, **parts)


def test_a_part_that_loaded_is_not_reported():
    assert unloaded_components(pipe({"transformer": spec()}, transformer=object())) == []


def test_a_part_that_did_not_load_is_reported_and_the_names_come_sorted():
    found = unloaded_components(pipe({"vocoder": spec(), "condition_encoder": spec(), "scheduler": spec()},
                                     vocoder=None, condition_encoder=None, scheduler=object()))
    assert found == ["condition_encoder", "vocoder"]  # not in the order the table lists them


def test_a_part_the_pipeline_has_no_attribute_for_counts_as_not_loaded():
    assert unloaded_components(pipe({"transformer": spec()})) == ["transformer"]


def test_a_part_that_load_components_would_never_load_is_not_reported_as_missing():
    # made from a config rather than loaded, so its absence is not a failed load
    assert unloaded_components(pipe({"scheduler": spec(method="from_config")}, scheduler=None)) == []
    # no place to load it from, so it is not one of the parts that load_components fetches
    assert unloaded_components(pipe({"extra": spec(path=None)}, extra=None)) == []


def test_a_pipeline_with_no_table_of_parts_has_nothing_missing():
    assert unloaded_components(SimpleNamespace()) == []
    assert unloaded_components(SimpleNamespace(_component_specs=None)) == []


def test_the_error_names_the_parts_and_counts_as_files_missing_for_the_cache_first_logic():
    error = MissingComponents(["transformer", "vocoder"])
    assert error.names == ["transformer", "vocoder"]
    assert "transformer, vocoder" in str(error)
    assert isinstance(error, FileNotFoundError) and is_cache_miss(error)
