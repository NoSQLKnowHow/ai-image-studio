"""Preparing a source end to end (DESIGN.md §38.5, §38.9; criteria 268 to 274): the review that is built, the child process it is built in, and the routes.

The child process matters here as much as the review: a hostile file or paste must end in a refusal within the limits and leave the server running
(criterion 273), so those tests run real children, some of them deliberately misbehaving.
"""

from __future__ import annotations

import os
import sys
import time

import pytest
from speech_files import make_docx, make_epub, make_pdf, zip_bomb
from test_speech_fetch import PAGE, PUBLIC, Net, Site

from studio.speech import prepare_service
from studio.speech.text import isolated
from studio.speech.text.errors import PrepareError
from studio.speech.text.fetch import fetch_page
from studio.speech.text.isolated import BACKEND_ROOT, run_reader
from studio.speech.text.model import Block, Chapter, Extracted
from studio.speech.text.prepare import Limits, build_review

LIMITS = Limits(max_chars=100_000, part_max_chars=30_000, chars_per_second=14.0)
LONG = "A paragraph with enough words in it to be read aloud properly, and then a few more words after that to make it longer."
CHAPTER = " ".join([LONG] * 14)  # about 1,700 characters: a chapter big enough not to be merged into the next one (parts.MERGE_BELOW is 1,500)


def extracted(*blocks: Block, title=None) -> Extracted:
    return Extracted(title=title, chapters=[Chapter(None, list(blocks))])


# ---------------------------------------------------------------- the review (criteria 269, 271)
def test_the_review_lists_the_parts_the_cleaning_and_the_length():
    review = build_review(extracted(Block("heading", "A **Great** Title", 1), Block("paragraph", "See [the docs](https://x.org/a) for more[1].")),
                          LIMITS, {"kind": "text"})
    assert review["title"] == "A Great Title" or review["title"]  # a title comes from the first words when the source has none
    assert len(review["parts"]) == 1
    texts = [p["text"] for p in review["parts"][0]["paragraphs"]]
    assert texts == ["A Great Title", "See the docs for more."]
    assert review["cleaning"] and any("markup" in line for line in review["cleaning"]) and any("citation" in line for line in review["cleaning"])
    assert review["chars"] == sum(len(t) for t in texts) and review["minutes"] == round(review["chars"] / 14 / 60, 1)
    assert review["source"] == {"kind": "text"} and review["options"] == {"clean": True, "read_extras": False, "speakers": True}  # "speakers" is new in 1.25 (on by default)


def test_cleaning_can_be_switched_off_and_the_raw_text_is_kept():
    review = build_review(extracted(Block("paragraph", "Keep **the** [marks](http://x.y) in   here.")), LIMITS, {}, clean=False)
    assert review["parts"][0]["paragraphs"][0]["text"] == "Keep **the** [marks](http://x.y) in here."  # white space is tidied, nothing else
    assert review["cleaning"] == [] and review["options"]["clean"] is False


def test_code_and_tables_are_kept_for_the_page_but_marked_as_not_read_unless_asked_for():
    blocks = (Block("paragraph", LONG), Block("code", "print('hello')\nprint('there')"), Block("table", "a, b\nc, d"))
    review = build_review(extracted(*blocks), LIMITS, {})
    paragraphs = review["parts"][0]["paragraphs"]
    assert [(p["skipped"], p["text"][:12]) for p in paragraphs] == [(None, LONG[:12]), ("code", "print('hello"), ("table", "a, b\nc, d")]
    assert review["parts"][0]["chars"] == len(LONG)  # only what will be read is counted
    assert any("1 code block" in line for line in review["cleaning"]) and any("1 table" in line for line in review["cleaning"])
    read = build_review(extracted(*blocks), LIMITS, {}, read_extras=True)["parts"][0]["paragraphs"]
    assert [p["skipped"] for p in read] == [None, None, None] and read[1]["text"] == "print('hello') print('there')" and read[2]["text"] == "a, b. c, d"


def test_the_review_of_a_skipped_block_carries_only_the_start_of_it():
    review = build_review(extracted(Block("paragraph", LONG), Block("code", "x" * 5000)), LIMITS, {})
    shown = review["parts"][0]["paragraphs"][1]["text"]
    assert len(shown) <= 1001 and shown.endswith("\u2026")


def test_text_over_the_limit_is_refused_with_the_figures_and_so_is_a_source_with_nothing_to_read():
    with pytest.raises(PrepareError) as big:
        build_review(extracted(*[Block("paragraph", LONG)] * 40), Limits(max_chars=2000, part_max_chars=1000, chars_per_second=14), {})
    assert big.value.code == "too_large" and "4,720 characters" in big.value.message and "2,000" in big.value.message
    for empty in (extracted(), extracted(Block("paragraph", "  "), Block("code", "only code"))):
        with pytest.raises(PrepareError) as nothing:
            build_review(empty, LIMITS, {})
        assert nothing.value.code == "no_text"


def test_left_out_chapters_stay_in_the_review_but_are_not_counted():
    chapters = [Chapter("Cover", [Block("paragraph", LONG)], include=False, note="Front or back matter"), Chapter("One", [Block("heading", "One", 1), Block("paragraph", LONG)])]
    review = build_review(Extracted("T", chapters), LIMITS, {})
    assert [(p["title"], p["include"], p["note"]) for p in review["parts"]] == [("Cover", False, "Front or back matter"), ("One", True, None)]
    assert review["chars"] == review["parts"][1]["chars"] and review["chapters_found"] == 1 and review["title"] == "T"


def test_a_long_text_is_divided_into_parts_by_size():
    review = build_review(extracted(*[Block("paragraph", LONG)] * 60), Limits(100_000, 2000, 14.0), {})
    assert len(review["parts"]) > 1 and all(p["chars"] <= 2000 for p in review["parts"]) and review["parts"][0]["title"].startswith("Part 1")


# ---------------------------------------------------------------- the child process (criterion 273)
def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_a_child_that_never_finishes_is_stopped_at_the_time_limit():
    started = time.monotonic()
    with pytest.raises(PrepareError) as caught:
        run_reader([], timeout=1.0, command=python("import time; time.sleep(60)"))
    assert caught.value.code == "timeout" and time.monotonic() - started < 10


def test_a_child_that_eats_memory_dies_and_the_server_says_so():
    hog = "from studio.speech.text.reader import apply_limits; apply_limits(300 * 1024 * 1024, 30); x = bytearray(1024 * 1024 * 1024)"
    with pytest.raises(PrepareError) as caught:
        run_reader([], timeout=30, command=python(hog))
    assert caught.value.code == "extract_failed" and "memory" in caught.value.message


def test_a_child_that_says_nothing_sensible_is_a_refusal_not_a_crash():
    for code in ("print('this is not json')", "import sys; sys.exit(3)", "raise SystemExit", "print('{\"no\": \"ok key\"}')"):
        with pytest.raises(PrepareError) as caught:
            run_reader([], timeout=10, command=python(code))
        assert caught.value.code == "extract_failed", code


def test_a_child_that_reports_an_error_has_its_code_message_and_hint_passed_on():
    code = "import json; print(json.dumps({'ok': False, 'error': {'code': 'no_text_layer', 'message': 'A scan.', 'hint': 'Use OCR.'}}))"
    with pytest.raises(PrepareError) as caught:
        run_reader([], timeout=10, command=python(code))
    assert (caught.value.code, caught.value.message, caught.value.hint, caught.value.status) == ("no_text_layer", "A scan.", "Use OCR.", 422)


def test_the_last_line_of_the_childs_output_is_its_answer_whatever_came_before():
    code = "import json; print('noise'); print(json.dumps({'ok': True, 'review': {'title': 'T'}}))"
    assert run_reader([], timeout=10, command=python(code)) == {"title": "T"}


def test_more_output_than_a_review_can_be_is_refused(monkeypatch):
    monkeypatch.setattr(isolated, "MAX_OUTPUT_BYTES", 50)
    with pytest.raises(PrepareError) as caught:
        run_reader([], timeout=10, command=python("print('x' * 500)"))
    assert caught.value.code == "too_large"


def test_the_child_has_a_bare_environment_and_runs_from_the_backend_folder(monkeypatch):
    monkeypatch.setenv("STUDIO_SECRET_THING", "do not leak")
    monkeypatch.setenv("HF_TOKEN", "do not leak either")
    code = "import json, os; print(json.dumps({'ok': True, 'review': {'env': sorted(os.environ), 'cwd': os.getcwd()}}))"
    review = run_reader([], timeout=10, command=python(code))
    assert "STUDIO_SECRET_THING" not in review["env"] and "HF_TOKEN" not in review["env"] and "HOME" not in review["env"]
    assert review["cwd"] == str(BACKEND_ROOT)  # not the folder an upload sits in, where a planted module could be imported


def test_a_hostile_paste_is_contained_by_the_cpu_limit(tmp_path):
    """A text built to make a pattern take quadratic time: the real reader, with a two-second CPU limit, must give up and the parent must say why."""
    path = tmp_path / "hostile.txt"
    path.write_text("[" * 300_000)
    args = ["--kind", "txt", "--path", str(path), "--max-chars", "1000000", "--part-max-chars", "30000", "--chars-per-second", "14", "--cpu-seconds", "2"]
    started = time.monotonic()
    with pytest.raises(PrepareError) as caught:
        run_reader(args, timeout=30)
    assert caught.value.code == "extract_failed" and time.monotonic() - started < 25


def test_a_hostile_archive_is_refused_by_the_real_reader(tmp_path):
    path = tmp_path / "bomb.docx"
    path.write_bytes(zip_bomb(70))
    args = ["--kind", "docx", "--path", str(path), "--max-chars", "1000000", "--part-max-chars", "30000", "--chars-per-second", "14"]
    with pytest.raises(PrepareError) as caught:
        run_reader(args, timeout=60)
    assert caught.value.code == "too_large"


# ---------------------------------------------------------------- the service and the routes (criterion 268)
@pytest.fixture
def settings(tmp_path):
    from conftest import make_settings

    return make_settings(tmp_path)


def test_every_kind_of_file_is_prepared_through_the_child_process(settings):
    txt = prepare_service.prepare_file(settings, "notes.txt", ("CHAPTER I.\n\n" + CHAPTER + "\n\nCHAPTER II.\n\n" + CHAPTER).encode())
    assert [p["title"] for p in txt["parts"]] == ["CHAPTER I.", "CHAPTER II."] and txt["source"] == {"kind": "file", "name": "notes.txt"}
    md = prepare_service.prepare_file(settings, "notes.MD", f"# One\n\n{CHAPTER}\n\n# Two\n\n{CHAPTER}\n".encode())
    assert [p["title"] for p in md["parts"]] == ["One", "Two"]
    docx = prepare_service.prepare_file(settings, "r.docx", make_docx([("Heading1", "Intro"), CHAPTER, ("Heading1", "End"), CHAPTER]))
    assert [p["title"] for p in docx["parts"]] == ["Intro", "End"]
    epub = prepare_service.prepare_file(settings, "b.epub", make_epub([("a.xhtml", "First", f"<p>{CHAPTER}</p>"), ("b.xhtml", "Second", f"<p>{CHAPTER}</p>")]))
    assert [p["title"] for p in epub["parts"]] == ["First", "Second"]
    pdf = prepare_service.prepare_file(settings, "p.pdf", make_pdf([["The first line of a short page goes here and is long enough to count.", "and a second line that finishes it."]]))
    assert pdf["chars"] > 50


def test_pasted_text_and_files_are_refused_in_words_when_they_cannot_be_read(settings):
    for call, code in ((lambda: prepare_service.prepare_text(settings, "   "), "empty"),
                       (lambda: prepare_service.prepare_file(settings, "x.exe", b"MZ"), "unsupported"),
                       (lambda: prepare_service.prepare_file(settings, "x.txt", b""), "empty"),
                       (lambda: prepare_service.prepare_file(settings, "x.txt", b"\x00\x01\x02binary"), "bad_encoding"),
                       (lambda: prepare_service.prepare_file(settings, "x.pdf", make_pdf([[], []])), "no_text_layer"),
                       (lambda: prepare_service.prepare_file(settings, "x.docx", b"not a zip"), "extract_failed")):
        with pytest.raises(PrepareError) as caught:
            call()
        assert caught.value.code == code, (code, caught.value.message)


def test_a_file_name_is_shown_safely():
    assert prepare_service.safe_name("../../etc/passwd.txt") == "passwd.txt"
    assert prepare_service.safe_name("C:\\Users\\me\\Book.epub") == "Book.epub"
    assert prepare_service.safe_name("a\x07b\n.txt") == "ab.txt" and prepare_service.safe_name("") == "file" and len(prepare_service.safe_name("n" * 500)) == 120


def post_file(client, name: str, data: bytes, **params):
    return client.post("/api/speech/prepare/file", params={"name": name, **params}, content=data)


def test_the_route_prepares_pasted_text(client):
    response = client.post("/api/speech/prepare", json={"source": "text", "text": f"# A Title\n\n{LONG}\n\n{LONG}\n"})
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["parts"][0]["paragraphs"][0] == {"kind": "heading", "text": "A Title", "skipped": None, "speaker": None, "label": None} and review["source"] == {"kind": "text"}


@pytest.mark.parametrize("body, status, code", [
    ({"source": "text", "text": "   "}, 422, "empty"),
    ({"source": "text"}, 422, "empty"),
    ({"source": "url"}, 422, "bad_address"),
    ({"source": "url", "url": "ftp://example.com/x"}, 422, "bad_address"),
    ({"source": "url", "url": "http://127.0.0.1/"}, 422, "private_address"),
    ({"source": "url", "url": "http://localhost/admin"}, 422, "private_address"),
    ({"source": "url", "url": "http://example.com:8080/"}, 422, "bad_address"),
])
def test_the_route_refuses_what_it_should_in_the_studios_usual_shape(client, body, status, code):
    response = client.post("/api/speech/prepare", json=body)
    assert response.status_code == status and response.json()["code"] == code and response.json()["detail"]


def test_the_route_refuses_unknown_fields_and_sources(client):
    assert client.post("/api/speech/prepare", json={"source": "text", "text": "x", "bogus": 1}).status_code == 422
    assert client.post("/api/speech/prepare", json={"source": "file", "text": "x"}).status_code == 422
    assert client.post("/api/speech/prepare", json={"source": "text", "text": "x", "clean": "yes"}).status_code == 422  # strict types


def test_text_over_the_reading_limit_is_refused_with_the_figures(client_factory):
    client = client_factory(speech_max_chars=2000, speech_part_max_chars=1000)
    response = client.post("/api/speech/prepare", json={"source": "text", "text": (LONG + "\n\n") * 25})
    assert response.status_code == 413 and response.json()["code"] == "too_large" and "one reading can hold 2,000" in response.json()["detail"]
    absurd = client.post("/api/speech/prepare", json={"source": "text", "text": "x" * 4500})
    assert absurd.status_code == 413 and "make two readings" in absurd.json()["detail"]


def test_the_file_route_reads_each_kind_and_names_the_file_in_the_source(client):
    response = post_file(client, "../../etc/Book.docx", make_docx([("Heading1", "Intro"), CHAPTER, ("Heading1", "End"), CHAPTER]))
    assert response.status_code == 200 and response.json()["source"] == {"kind": "file", "name": "Book.docx"}
    assert [p["title"] for p in response.json()["parts"]] == ["Intro", "End"]
    plain = post_file(client, "n.txt", f"{LONG}\n\n```not markdown```\n".encode(), clean="false")
    assert plain.status_code == 200 and plain.json()["options"]["clean"] is False


@pytest.mark.parametrize("name, data, status, code", [
    ("virus.exe", b"MZ", 415, "unsupported"),
    ("empty.txt", b"", 422, "empty"),
    ("scan.pdf", make_pdf([[]]), 422, "no_text_layer"),
    ("broken.epub", b"not an epub", 422, "extract_failed"),
    ("bomb.docx", zip_bomb(70), 413, "too_large"),
])
def test_the_file_route_refuses_what_it_cannot_read_in_words(client, name, data, status, code):
    response = post_file(client, name, data)
    assert response.status_code == status and response.json()["code"] == code and response.json()["detail"]


def test_a_file_over_the_upload_limit_is_refused_from_its_size_and_while_it_streams(client_factory):
    client = client_factory(speech_max_upload_mb=1)
    too_big = b"x" * (1024 * 1024 + 10)
    response = post_file(client, "big.txt", too_big)
    assert response.status_code == 413 and "1 MB" in response.json()["detail"]
    chunked = client.post("/api/speech/prepare/file", params={"name": "big.txt"}, content=iter([too_big[:600_000], too_big[600_000:]]))  # no declared length
    assert chunked.status_code == 413


def test_the_file_route_needs_a_name(client):
    assert client.post("/api/speech/prepare/file", content=b"hello").status_code == 422


def test_a_web_page_is_fetched_prepared_and_described(client, monkeypatch):
    site = Site()
    try:
        site.page("/story", b"<html><head><title>Big News | Daily</title></head><body><article><h1>Big News</h1><p>" + LONG.encode() + b"</p><p>" + LONG.encode()
                  + b"</p></article></body></html>")
        net = Net(site)
        # the route asks for the real resolver and connector (None); the test's own replace them, so no network is touched
        monkeypatch.setattr(prepare_service, "fetch_page", lambda url, **kw: fetch_page(url, **{**kw, "resolve": net.resolve, "connector": net.connect}))
        response = client.post("/api/speech/prepare", json={"source": "url", "url": "http://news.example/story"})
        assert response.status_code == 200, response.text
        review = response.json()
        assert review["title"] == "Big News" and review["source"] == {"kind": "url", "url": "http://news.example/story", "host": "news.example"}
        assert [p["text"] for p in review["parts"][0]["paragraphs"]] == ["Big News", LONG, LONG] and net.connections == [(PUBLIC, 80)]
    finally:
        site.stop()


def test_preparing_changes_nothing_but_still_needs_the_studios_header_like_every_other_post(client):
    """Cross-site requests are refused by the required header (DESIGN.md §11). A prepare stores nothing, but it makes the server fetch a page and run a
    child process, which is reason enough: a web page the owner happens to visit must not be able to make their Spark do that."""
    for url, kwargs in (("/api/speech/prepare", {"json": {"source": "text", "text": "Hello there."}}),
                        ("/api/speech/prepare/file?name=a.txt", {"content": b"Hello there."})):
        refused = client.post(url, headers={"X-Studio-Client": ""}, **kwargs)
        assert refused.status_code == 403, (url, refused.status_code)
        assert client.post(url, **kwargs).status_code == 200
