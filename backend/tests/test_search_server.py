"""Search and the Model, Size and Date filters, through the API (DESIGN.md §45.4, §45.5, §45.7; criteria 409 to 417, 424): that no text a person can type is an error, that the limits are
said in 422s, that paging and the counts work inside a search, what the drop-downs are offered, and how an instant, a size and a model are read.

The words rule itself is the shared table's (`filter_cases.json`, run through the SQL in `test_runfilter.py` and through the page's `matches()`); here the questions are about the
requests: what a route accepts, refuses, and answers."""

from __future__ import annotations

import json

import pytest
from test_db import run_row

from studio.runsearch import clean_notes, clean_tags, fold, index_fields, match_string, parse_size, search_terms, words_of

NOW = "2026-10-02T00:00:00.000Z"


def put(client, n: int, **fields) -> str:
    """Insert run `n` straight into the database the server uses, with the fields given (a prompt, a model, a width and height, a moment made...). Straight in, so that a test can say exactly
    when a run was made and what size it is, which a run made through the fake pipeline cannot be told. Returns its id."""
    row = {**run_row(n, "done"), **fields}
    assert client.app.state.db.insert_run_if_capacity(row, cap=1000)
    return row["id"]


def listing(client, **params) -> tuple[int, list[str]]:
    response = client.get("/api/runs", params={"limit": 100, **params})
    return response.status_code, [run["prompt"] for run in response.json()["runs"]] if response.status_code == 200 else []


# ------------------------------------------------------------------------------------------------- the words rule, as functions
def test_a_search_is_cut_into_folded_distinct_words():
    assert search_terms("Fox  Winter-snow") == ["fox", "winter", "snow"]  # punctuation separates, case is folded
    assert search_terms("café CAFE Cafe") == ["cafe"]  # accents and case are not told apart, so it is one word
    assert search_terms("fox fox FOX") == ["fox"]
    assert search_terms('"fox"* AND -winter: (hen)') == ["fox", "and", "winter", "hen"]  # an operator is a word, as in the Prompt builder's search
    assert search_terms("snake_case under_score") == ["snake", "case", "under", "score"]  # the underscore separates, as the index's own tokenizer does
    assert search_terms("a1 b2 123") == ["a1", "b2", "123"]  # digits are letters of a word
    assert search_terms("") == [] and search_terms(None) == [] and search_terms('"*-:()') == []  # no word: no search


def test_at_most_ten_words_are_used():
    text = " ".join(f"w{n}" for n in range(30))
    assert search_terms(text) == [f"w{n}" for n in range(10)]
    assert search_terms("a a a a a a a a a a b") == ["a", "b"]  # ten DISTINCT words: a repeated word does not use one up


def test_the_match_string_holds_nothing_but_quoted_words():
    assert match_string(["fox", "winter"]) == '"fox"* "winter"*'
    assert match_string([]) == ""
    # whatever is typed, what reaches the match string is letters and digits in quotes: no character that FTS5 reads as syntax
    for hostile in ('fox" OR "1"="1', "fox*", "NEAR(a b)", "col:fox", "^fox", "fox -hen", "{a b}", "'; DROP TABLE runs; --", "\x00fox"):
        assert all(ch.isalnum() or ch in ' "*' for ch in match_string(search_terms(hostile))), hostile


def test_folding_takes_accents_off_and_leaves_what_has_no_accent_to_take():
    assert fold("Café Ünïcode") == "cafe unicode" and fold("ÀÉÎÕÜ") == "aeiou"
    assert fold("ø") == "ø" and fold("ß") == "ß"  # no decomposition: the index leaves them as they are, so the rule must too (casefold would make ß into ss)
    assert words_of("a_b c-d") == ["a", "b", "c", "d"]


def test_a_note_and_tags_are_cleaned_by_the_functions_the_routes_use():
    assert clean_notes("  a\r\nb\rc\td\x00e\x1b  ") == "a\nb\nc\tde"
    assert clean_notes("​hidden​") == "hidden"  # an invisible format character is removed too
    assert clean_tags(["Fox", "fox", " ", "a,b", "x" * 40, 5, None]) == ["fox", "a b", "x" * 30]  # not text, and nothing usable, are dropped


def test_what_a_run_puts_in_the_index():
    # a picture: its prompt; a music run: its prompt and its lyrics (which are NOT in the prompt column); a reading: its title and where its text came from; a vector run: its idea
    assert index_fields("generate", "a fox", None, "{}") == ("a fox", "")
    assert index_fields("music", "calm piano", "twinkle twinkle", "{}") == ("calm piano", "twinkle twinkle")
    file_source = json.dumps({"source": {"kind": "file", "name": "whale-book.epub"}})
    assert index_fields("speech", "Moby Dick", None, file_source) == ("Moby Dick", "whale-book.epub")
    web_source = json.dumps({"source": {"kind": "url", "url": "https://example.org/a-page", "host": "example.org"}})
    assert index_fields("speech", "A page", None, web_source)[1] == "example.org https://example.org/a-page"
    assert index_fields("speech", "Pasted", None, json.dumps({"source": {"kind": "text"}})) == ("Pasted", "")
    # damaged options never break indexing
    assert index_fields("speech", "x", None, "not json") == ("x", "") and index_fields("speech", "x", None, None) == ("x", "")
    assert index_fields("speech", "x", None, json.dumps({"source": "a string"})) == ("x", "")
    assert index_fields("vector", "a fox head", None, json.dumps({"recipe": "flat colour artwork gradient"})) == ("a fox head", "")  # the recipe's words are not searched


def test_a_size_is_two_whole_numbers():
    assert parse_size("2752x1536") == (2752, 1536) and parse_size("1x1") == (1, 1) and parse_size("99999x99999") == (99999, 99999)
    for bad in (None, "", "2752", "2752x", "x1536", "2752X1536", "2752 x 1536", "2752x1536x1", "-1x5", "1.5x2", "100000x1", "1x100000", "２７×１５"):
        assert parse_size(bad) is None, bad


# ------------------------------------------------------------------------------------------------- no text is an error
HOSTILE = [
    '"', '""', '"fox', 'fox"', "'", "''", "*", "**", "fox*", "*fox", "AND", "OR", "NOT", "NEAR", "fox AND", "AND fox", "fox OR hen", "fox NOT hen", "-", "--", "-fox", "fox -", "fox:", ":fox",
    "a:b", "prompt:fox", "notes:poster", "tags:x", "^", "^fox", "(", ")", "(fox", "fox)", "()", "{", "}", "[", "]", "fox\\", "\\", "%", "_", "%_%", "'; DROP TABLE runs; --",
    "' OR 1=1 --", '" OR "1"="1', "fox' OR '1'='1", "\x00", "fox\x00hen", "\n", "\t", "   ", "ünï", "日本語", "😀", "fox 😀 hen", "a" * 200, " ".join(["fox"] * 50), "NEAR(fox hen, 2)",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_no_text_a_person_can_type_is_an_error_a_search_gives_a_list(client, text):
    """The list and the counts, with the text as a search: every answer is a 200 with a list (maybe empty), never a 500 and never FTS5's own error. (Over the length limit is a 422, below.)"""
    put(client, 1, prompt="a red fox in the snow")
    put(client, 2, prompt="a lighthouse")
    assert len(text) <= 200
    status, _prompts = listing(client, q=text)
    assert status == 200, text
    assert client.get("/api/runs/counts", params={"q": text}).status_code == 200, text
    assert client.get("/api/runs", params={"q": text, "deleted": "true"}).status_code == 200  # the bin too
    # whatever was typed, the database is as it was: nothing was run as SQL
    assert listing(client)[1] == ["p2", "p1"] or listing(client)[1] == ["a lighthouse", "a red fox in the snow"]


def test_a_search_with_no_word_in_it_is_no_search(client):
    put(client, 1, prompt="a red fox")
    put(client, 2, prompt="a lighthouse")
    everything = listing(client)[1]
    for text in ('"', "*", "-:", "()", "   ", "", "…", "—"):
        assert listing(client, q=text)[1] == everything, repr(text)


def test_the_limits_are_said_in_a_422(client):
    put(client, 1, prompt="a red fox")
    assert listing(client, q="a" * 200)[0] == 200  # exactly the limit
    too_long = client.get("/api/runs", params={"q": "a" * 201})
    assert too_long.status_code == 422 and "q" in json.dumps(too_long.json())
    assert client.get("/api/runs/counts", params={"q": "a" * 201}).status_code == 422
    assert client.get("/api/runs", params={"model": "m" * 201}).status_code == 422
    for bad in ("2752", "2752x", "axb", "2752x1536 ", "-1x5", "123456x1"):
        assert client.get("/api/runs", params={"size": bad}).status_code == 422, bad
        assert client.get("/api/runs/counts", params={"size": bad}).status_code == 422, bad


def test_the_capabilities_name_the_limits_and_say_how_search_works(client):
    caps = client.get("/api/capabilities").json()
    assert caps["annotations"]["search"] == "words"
    assert caps["limits"]["annotations"] == {"tags_max": 12, "tag_chars": 30, "notes_chars": 2000, "search_chars": 200, "search_words": 10, "suggest_max": 5}  # suggest_max is the setting's default (5)


# ------------------------------------------------------------------------------------------------- paging and counts inside a search
def test_paging_continues_among_the_matches_and_never_repeats_or_skips(client):
    for n in range(1, 13):
        put(client, n, prompt=f"fox number {n}" if n % 2 else f"hen number {n}")
    # the odd ones are the foxes: 6 of 12. Page them two at a time, by the cursor the server gives
    seen, before = [], None
    for _ in range(10):
        params = {"q": "fox", "limit": 2, **({"before": before} if before else {})}
        page = client.get("/api/runs", params=params).json()
        seen += [run["prompt"] for run in page["runs"]]
        before = page["next_before"]
        if before is None:
            break
    assert seen == [f"fox number {n}" for n in (11, 9, 7, 5, 3, 1)]  # newest first, all of them, none twice
    # a cursor may be a run the search does not show (one that was edited since the page was drawn): it only says where to start
    last = client.get("/api/runs", params={"limit": 1}).json()["runs"][0]
    assert last["prompt"] == "hen number 12"
    page = client.get("/api/runs", params={"q": "fox", "limit": 2, "before": last["id"]}).json()
    assert [run["prompt"] for run in page["runs"]] == ["fox number 11", "fox number 9"]


def test_the_counts_follow_the_search_the_filters_and_the_project(client):
    put(client, 1, prompt="a red fox", model_id="model-a", pinned=1)
    put(client, 2, prompt="a grey fox", model_id="model-b")
    put(client, 3, prompt="a lighthouse", model_id="model-a", pinned=1)
    whole = client.get("/api/runs/counts").json()["image"]
    assert (whole["all"], whole["kept"]) == (3, 2)
    assert client.get("/api/runs/counts", params={"q": "fox"}).json()["image"]["all"] == 2  # the number beside All is how many runs match
    assert client.get("/api/runs/counts", params={"q": "fox"}).json()["image"]["kept"] == 1  # Kept is the matches that are kept
    both = client.get("/api/runs/counts", params={"q": "fox", "model": "model-a"}).json()["image"]
    assert (both["all"], both["kept"]) == (1, 1)
    assert client.get("/api/runs/counts", params={"model": "model-z"}).json()["image"]["all"] == 0
    # with a project chosen too: the project narrows as well
    assert client.get("/api/runs/counts", params={"q": "fox", "project": "none"}).json()["image"]["all"] == 2
    assert client.get("/api/runs/counts", params={"q": "fox", "project": "a" * 32}).json()["image"]["all"] == 0
    # the other tabs' numbers are for the same search
    assert client.get("/api/runs/counts", params={"q": "fox"}).json()["music"]["all"] == 0


def test_the_bin_counts_follow_the_search_and_count_things(client):
    first = put(client, 1, prompt="a red fox")
    second = put(client, 2, prompt="a lighthouse")
    for run_id in (first, second):
        assert client.post(f"/api/runs/{run_id}/bin").status_code == 200
    assert client.get("/api/runs/counts").json()["image"]["deleted"] == 2
    assert client.get("/api/runs/counts", params={"q": "fox"}).json()["image"]["deleted"] == 1
    assert client.get("/api/runs", params={"q": "fox", "deleted": "true"}).json()["runs"][0]["prompt"] == "a red fox"


# ------------------------------------------------------------------------------------------------- the Model and Size drop-downs
def test_the_facets_list_the_models_and_sizes_of_a_tab_with_their_counts_most_first(client):
    put(client, 1, model_id="model-a", width=2752, height=1536)
    put(client, 2, model_id="model-a", width=2752, height=1536)
    put(client, 3, model_id="model-b", width=1024, height=1024)
    put(client, 4, model_id="music-model", mode="music", width=None, height=None)
    put(client, 5, model_id="vec-model", mode="vector", width=1024, height=1024)  # a Vector run has a width and height in the database; they are not a picture's size
    facets = client.get("/api/runs/facets", params={"kind": "image"}).json()
    assert facets["models"] == [{"value": "model-a", "count": 2}, {"value": "model-b", "count": 1}]
    assert facets["sizes"] == [{"width": 2752, "height": 1536, "count": 2}, {"width": 1024, "height": 1024, "count": 1}]  # the Vector run's 1024 is not counted
    assert client.get("/api/runs/facets", params={"kind": "music"}).json() == {"models": [{"value": "music-model", "count": 1}], "sizes": []}  # no size on a music track
    assert client.get("/api/runs/facets", params={"kind": "vector"}).json() == {"models": [{"value": "vec-model", "count": 1}], "sizes": []}
    assert client.get("/api/runs/facets", params={"kind": "speech"}).json() == {"models": [], "sizes": []}
    assert client.get("/api/runs/facets", params={"kind": "nope"}).status_code == 422 and client.get("/api/runs/facets").status_code == 422


def test_the_facets_count_the_bin_too_and_never_a_hidden_job(client):
    one = put(client, 1, model_id="model-a", width=512, height=512)
    put(client, 2, model_id="model-hidden", mode="speech", hidden=1, options_json=json.dumps({"task": "sample"}))
    client.post(f"/api/runs/{one}/bin")  # a model that only the bin holds can still be chosen in the Deleted view
    assert client.get("/api/runs/facets", params={"kind": "image"}).json()["models"] == [{"value": "model-a", "count": 1}]
    assert client.get("/api/runs/facets", params={"kind": "speech"}).json()["models"] == []


def test_a_model_filter_is_exact_and_a_value_that_looks_like_sql_is_only_a_value(client):
    put(client, 1, prompt="one", model_id="model-a")
    put(client, 2, prompt="two", model_id="model-a2")
    put(client, 3, prompt="three", model_id="x' OR '1'='1")
    assert listing(client, model="model-a") == (200, ["one"])  # not a prefix: model-a2 is another model
    assert listing(client, model="x' OR '1'='1") == (200, ["three"]) and listing(client, model="' OR 1=1 --") == (200, [])
    assert listing(client)[1] == ["three", "two", "one"]  # still there, still all of them


def test_a_size_is_the_first_picture_the_card_shows_else_the_size_that_was_asked_for(client):
    """The rule of `sizeText` in the page: 'from the finished images, else from the requested size' (DESIGN.md §45.4 rule 5)."""
    db = client.app.state.db
    asked = put(client, 1, prompt="asked only", width=640, height=480)  # a run with no picture yet: the size asked for
    auto = put(client, 2, prompt="edit on auto", mode="edit", width=None, height=None)  # an edit with Size on Auto asks for no size: the picture says it
    db.add_image({"id": f"{99:032x}", "run_id": auto, "kind": "output", "idx": 0, "seed": 1, "width": 1344, "height": 896, "has_alpha": 0, "bytes": 1, "path": "x", "thumb_path": None, "created_at": NOW})
    both = put(client, 3, prompt="asked and made", width=2752, height=1536)  # the picture that was made wins over what was asked
    db.add_image({"id": f"{98:032x}", "run_id": both, "kind": "output", "idx": 0, "seed": 1, "width": 2400, "height": 1792, "has_alpha": 0, "bytes": 1, "path": "x", "thumb_path": None, "created_at": NOW})
    assert listing(client, size="640x480")[1] == ["asked only"]
    assert listing(client, size="1344x896")[1] == ["edit on auto"]
    assert listing(client, size="2400x1792")[1] == ["asked and made"] and listing(client, size="2752x1536")[1] == []
    # the facets read it the same way
    sizes = client.get("/api/runs/facets", params={"kind": "image"}).json()["sizes"]
    assert sorted((s["width"], s["height"]) for s in sizes) == [(640, 480), (1344, 896), (2400, 1792)]
    # a picture that is in the bin on its own is not one the card shows, so the next picture is the first (two pictures, one of them in the bin: the size is the other's)
    db.add_image({"id": f"{97:032x}", "run_id": both, "kind": "output", "idx": 1, "seed": 2, "width": 1696, "height": 2528, "has_alpha": 0, "bytes": 1, "path": "x", "thumb_path": None, "created_at": NOW})
    assert db.bin_picture("image", f"{98:032x}", NOW)[0] == "picture"  # the first picture goes to the bin; the run stays in the history
    assert listing(client, size="1696x2528")[1] == ["asked and made"] and listing(client, size="2400x1792")[1] == []


# ------------------------------------------------------------------------------------------------- the date range
def test_the_range_is_instants_inclusive_at_the_start_and_exclusive_at_the_end(client):
    put(client, 1, prompt="before", created_at="2026-10-01T23:59:59.999Z")
    put(client, 2, prompt="at the start", created_at="2026-10-02T00:00:00.000Z")
    put(client, 3, prompt="inside", created_at="2026-10-02T12:00:00.000Z")
    put(client, 4, prompt="last moment", created_at="2026-10-02T23:59:59.999Z")
    put(client, 5, prompt="at the end", created_at="2026-10-03T00:00:00.000Z")
    day = {"since": "2026-10-02T00:00:00.000Z", "until": "2026-10-03T00:00:00.000Z"}
    assert listing(client, **day)[1] == ["last moment", "inside", "at the start"]  # the first instant is in, the second is out
    assert listing(client, since=day["since"])[1] == ["at the end", "last moment", "inside", "at the start"]
    assert listing(client, until=day["until"])[1] == ["last moment", "inside", "at the start", "before"]


def test_an_instant_without_milliseconds_is_read_as_the_same_instant_not_a_later_one(client):
    """`created_at` has milliseconds, and the range is compared as text: `…:00Z` sorts AFTER `…:00.123Z`, so an instant sent without them must be rewritten, or a run made a moment
    after the start would be left out (the mistake this test is for)."""
    put(client, 1, prompt="a moment after", created_at="2026-10-02T00:00:00.123Z")
    put(client, 2, prompt="a moment before", created_at="2026-10-01T23:59:59.999Z")
    assert listing(client, since="2026-10-02T00:00:00Z")[1] == ["a moment after"]
    assert listing(client, until="2026-10-02T00:00:00Z")[1] == ["a moment before"]
    assert listing(client, since="2026-10-02T00:00:00.1Z")[1] == ["a moment after"]  # one digit of a second is read too
    assert client.get("/api/runs/counts", params={"since": "2026-10-02T00:00:00Z"}).json()["image"]["all"] == 1


def test_a_range_that_is_not_one_is_refused_in_words(client):
    put(client, 1)
    for params in ({"since": "2026-13-01T00:00:00Z"}, {"until": "2026-02-30T00:00:00.000Z"}, {"since": "2026-10-03T00:00:00Z", "until": "2026-10-02T00:00:00Z"},
                   {"since": "2026-10-02T00:00:00Z", "until": "2026-10-02T00:00:00.000Z"}):  # ends where it begins: nothing could be in it
        for route in ("/api/runs", "/api/runs/counts"):
            response = client.get(route, params=params)
            assert response.status_code == 422 and response.json()["code"] == "bad_range", (route, params)
    # not an instant at all (no zone, a date alone, text): refused by the shape of the parameter
    for bad in ("2026-10-02", "2026-10-02T00:00:00", "2026-10-02T00:00:00+02:00", "yesterday", "'; --"):
        assert client.get("/api/runs", params={"since": bad}).status_code == 422, bad


def test_the_range_narrows_the_counts_too(client):
    put(client, 1, created_at="2026-10-01T10:00:00.000Z")
    put(client, 2, created_at="2026-10-02T10:00:00.000Z", pinned=1)
    day = {"since": "2026-10-02T00:00:00.000Z", "until": "2026-10-03T00:00:00.000Z"}
    counts = client.get("/api/runs/counts", params=day).json()["image"]
    assert (counts["all"], counts["kept"]) == (1, 1)


# ------------------------------------------------------------------------------------------------- everything together
def test_a_search_and_all_the_filters_narrow_each_other_and_show_older_still_works(client):
    for n in range(1, 9):
        put(client, n, prompt=f"fox {n}", model_id="model-a" if n % 2 else "model-b", width=1024 if n < 5 else 2048, height=1024, created_at=f"2026-10-0{n}T12:00:00.000Z")
    params = {"q": "fox", "model": "model-a", "size": "2048x1024", "since": "2026-10-01T00:00:00Z", "until": "2026-10-07T00:00:00Z"}
    assert listing(client, **params)[1] == ["fox 5"]
    page = client.get("/api/runs", params={**params, "limit": 1}).json()
    assert [run["prompt"] for run in page["runs"]] == ["fox 5"] and page["next_before"] is None
    assert client.get("/api/runs/counts", params=params).json()["image"]["all"] == 1
