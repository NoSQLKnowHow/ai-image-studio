"""Fetching a web page safely (DESIGN.md §38.5 item 2, criterion 272).

A real web server on this machine's loopback stands in for the internet, and the resolver and connector are replaced so that the studio believes the host
name is a public address while the connection really goes to that server. That proves every rule except the last mile (the socket to a real public
address), and it proves one thing a plain test could not: *which address the studio connects to*, which is the whole of the protection against a name that
answers differently the second time.
"""

from __future__ import annotations

import gzip
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

import pytest

from studio.speech.text import fetch as F
from studio.speech.text.errors import PrepareError
from studio.speech.text.fetch import check_url, fetch_page, is_public

PUBLIC = "93.184.216.34"
PAGE = b"<html><head><title>Hi</title></head><body><p>Hello there.</p></body></html>"


class Site:
    """A web server on the loopback that answers from a table of paths, and remembers the requests it was sent."""

    def __init__(self) -> None:
        self.routes: dict[str, Callable[[BaseHTTPRequestHandler], None]] = {}
        self.requests: list[dict] = []
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
                site.requests.append({"path": self.path, "host": self.headers.get("Host"), "agent": self.headers.get("User-Agent"),
                                      "cookie": self.headers.get("Cookie"), "encoding": self.headers.get("Accept-Encoding")})
                site.routes.get(self.path.split("?")[0], lambda h: site.reply(h, 404, b"no"))(self)

            def log_message(self, *args) -> None:  # keep the test output quiet
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def reply(handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str = "text/html; charset=utf-8", extra: Optional[dict] = None,
              length: bool = True) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        if length:
            handler.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            handler.send_header(key, value)
        handler.end_headers()
        try:
            handler.wfile.write(body)
        except OSError:  # the studio hung up (the size cap): that is the point of some tests
            pass

    def page(self, path: str, body: bytes = PAGE, **kw) -> None:
        self.routes[path] = lambda h: self.reply(h, 200, body, **kw)

    def redirect(self, path: str, to: str, status: int = 302) -> None:
        self.routes[path] = lambda h: self.reply(h, status, b"", extra={"Location": to})

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def site():
    s = Site()
    yield s
    s.stop()


class Net:
    """A resolver and a connector for one test: what each host name "resolves" to, and every connection the studio made."""

    def __init__(self, site: Site, answers: Optional[dict[str, list[str]]] = None) -> None:
        self.site, self.answers, self.connections, self.lookups = site, answers or {}, [], []

    def resolve(self, host: str, port: int) -> list[str]:
        self.lookups.append(host)
        return self.answers.get(host, [PUBLIC])

    def connect(self, address: str, port: int, timeout: float) -> socket.socket:
        self.connections.append((address, port))
        return socket.create_connection(("127.0.0.1", self.site.port), timeout)


def get(site: Site, url: str, net: Optional[Net] = None, max_bytes: int = 1_000_000, step: float = 3.0, **kw):
    net = net or Net(site)
    return fetch_page(url, max_bytes=max_bytes, step_timeout=step, resolve=net.resolve, connector=net.connect, **kw), net


def refused(code: str, *args, **kw) -> PrepareError:
    with pytest.raises(PrepareError) as caught:
        get(*args, **kw)
    assert caught.value.code == code, (caught.value.code, caught.value.message)
    return caught.value


# ---------------------------------------------------------------- the address itself
@pytest.mark.parametrize("address, public", [
    ("93.184.216.34", True), ("8.8.8.8", True), ("2606:4700:4700::1111", True),
    ("127.0.0.1", False), ("127.1.2.3", False), ("10.0.0.1", False), ("172.16.5.5", False), ("192.168.1.1", False), ("169.254.169.254", False),
    ("169.254.1.1", False), ("100.64.0.1", False), ("0.0.0.0", False), ("224.0.0.1", False), ("255.255.255.255", False), ("192.0.2.1", False),
    ("::1", False), ("fe80::1", False), ("fc00::1", False), ("fd00:ec2::254", False), ("::", False), ("ff02::1", False),
    ("::ffff:10.0.0.1", False), ("::ffff:127.0.0.1", False), ("::ffff:8.8.8.8", True),
    ("64:ff9b::a00:1", False),  # NAT64 wrapping 10.0.0.1
    ("2002:a00:1::", False),  # 6to4 wrapping 10.0.0.1
])
def test_which_addresses_count_as_public(address, public):
    assert is_public(address) is public


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "javascript:alert(1)", "gopher://example.com", "http://", "http:///path",
                                 "http://user:pass@example.com/", "http://user@example.com/", "http://example.com:8080/", "http://example.com:22/",
                                 "https://example.com:8443/", "http://[::1", "", "   ", "x" * 2100])
def test_addresses_that_are_not_plain_public_web_addresses_are_refused(url):
    with pytest.raises(PrepareError) as caught:
        check_url(url)
    assert caught.value.code == "bad_address"


def test_ordinary_addresses_are_accepted_and_a_missing_scheme_means_https():
    assert check_url("https://example.com/a/b?c=d") == ("https", "example.com", 443, "/a/b?c=d")
    assert check_url("http://example.com") == ("http", "example.com", 80, "/")
    assert check_url("  example.com/page ") == ("https", "example.com", 443, "/page")
    assert check_url("http://example.com:80/x")[2] == 80 and check_url("https://example.com:443/x")[2] == 443


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://[::1]/", "http://0.0.0.0/", "http://2130706433/", "http://10.1.2.3/",
                                 "http://192.168.0.1/admin", "http://169.254.169.254/latest/meta-data/"])
def test_private_and_loopback_addresses_are_refused_by_the_real_resolver_without_any_connection(url):
    """The real resolver, and a connector that fails the test if it is ever called: nothing may be connected to."""
    def never(*args):
        raise AssertionError("a connection was made to a private address")

    with pytest.raises(PrepareError) as caught:
        fetch_page(url, max_bytes=1000, step_timeout=1, connector=never)
    assert caught.value.code == "private_address" and "private or internal" in caught.value.message


# ---------------------------------------------------------------- a good fetch, and what is sent
def test_a_page_is_fetched_from_the_address_that_was_checked_with_the_name_in_the_host_header(site):
    site.page("/story", PAGE)
    fetched, net = get(site, "http://news.example/story?x=1")
    assert fetched.body == PAGE and fetched.content_type == "text/html" and fetched.charset == "utf-8" and fetched.url == "http://news.example/story?x=1"
    assert net.connections == [(PUBLIC, 80)]  # the studio connected to the address it had checked, and to nothing else
    request = site.requests[0]
    assert request["host"] == "news.example" and request["path"] == "/story?x=1" and "ReadAloudStudio" in request["agent"]
    assert request["cookie"] is None and request["encoding"] == "identity"  # no cookies, no compression asked for
    assert "@" not in (request["agent"] or "")  # nobody's email address in the client name


def test_the_name_is_looked_up_once_and_not_again_for_the_connection(site):
    site.page("/", PAGE)
    _, net = get(site, "http://news.example/")
    assert net.lookups == ["news.example"]  # the connector is given the address, so there is no second lookup to be fooled


def test_text_and_pdf_are_accepted_and_a_picture_is_not(site):
    site.page("/t", b"plain words", content_type="text/plain; charset=utf-8")
    site.page("/p", b"%PDF-1.4", content_type="application/pdf")
    site.page("/i", b"\x89PNG", content_type="image/png")
    assert get(site, "http://a.example/t")[0].content_type == "text/plain"
    assert get(site, "http://a.example/p")[0].content_type == "application/pdf"
    assert "image/png" in refused("unsupported", site, "http://a.example/i").message


def test_a_page_with_no_content_type_is_refused(site):
    site.routes["/n"] = lambda h: (h.send_response(200), h.send_header("Content-Length", "2"), h.end_headers(), h.wfile.write(b"hi"))
    refused("unsupported", site, "http://a.example/n")


@pytest.mark.parametrize("status, word", [(404, "has no such page"), (403, "refused"), (401, "needs a login"), (429, "limiting"), (500, "error")])
def test_a_site_that_says_no_is_reported_in_words(site, status, word):
    site.routes["/x"] = lambda h: site.reply(h, status, b"no")
    assert word in refused("unreachable", site, "http://a.example/x").message


# ---------------------------------------------------------------- the rules that make it safe (criterion 272)
def test_a_name_that_resolves_to_a_private_address_is_refused_before_any_connection(site):
    net = Net(site, {"evil.example": ["10.0.0.5"]})
    error = refused("private_address", site, "http://evil.example/", net)
    assert net.connections == [] and "private or internal" in error.message


def test_one_private_address_among_public_ones_is_enough_to_refuse(site):
    net = Net(site, {"mixed.example": [PUBLIC, "192.168.1.9"]})
    refused("private_address", site, "http://mixed.example/", net)
    assert net.connections == []


def test_a_redirect_is_followed_and_each_step_is_checked_in_full_again(site):
    site.redirect("/start", "/middle", 301)
    site.redirect("/middle", "http://other.example/final", 307)
    site.page("/final", PAGE)
    fetched, net = get(site, "http://a.example/start", Net(site))
    assert fetched.url == "http://other.example/final" and fetched.body == PAGE
    assert [r["host"] for r in site.requests] == ["a.example", "a.example", "other.example"]  # the Host header followed each hop
    assert len(net.lookups) == 3  # and so did the lookup and the check


def test_a_redirect_to_a_private_address_is_refused_and_never_connected_to(site):
    site.redirect("/start", "http://internal.example/secret")
    net = Net(site, {"internal.example": ["10.0.0.5"]})
    refused("private_address", site, "http://a.example/start", net)
    assert net.connections == [(PUBLIC, 80)] and len(site.requests) == 1  # only the first hop was ever contacted


def test_a_redirect_to_a_literal_internal_address_or_a_bad_scheme_or_port_is_refused(site):
    for target in ("http://127.0.0.1/", "ftp://a.example/x", "http://a.example:8080/x", "file:///etc/passwd"):
        site.redirect("/r", target)
        with pytest.raises(PrepareError) as caught:
            fetch_page("http://a.example/r", max_bytes=1000, step_timeout=2, connector=Net(site).connect,
                       resolve=lambda host, port: [PUBLIC] if host == "a.example" else F.default_resolver(host, port))  # the real resolver for a literal address
        assert caught.value.code in ("private_address", "bad_address"), target


def test_endless_redirects_stop_after_five(site):
    site.redirect("/loop", "/loop")
    error = refused("unreachable", site, "http://a.example/loop")
    assert "circles" in error.message and len(site.requests) == F.MAX_REDIRECTS + 1


def test_a_redirect_with_no_destination_is_refused(site):
    site.routes["/r"] = lambda h: site.reply(h, 302, b"")
    refused("unreachable", site, "http://a.example/r")


def test_a_page_larger_than_the_cap_is_refused_from_its_declared_size_and_while_it_streams(site):
    site.page("/big", b"x" * 5000)
    assert refused("too_large", site, "http://a.example/big", max_bytes=1000).message.startswith("The page is larger")
    site.routes["/stream"] = lambda h: site.reply(h, 200, b"x" * 5000, length=False)  # no Content-Length: only the streaming check can catch it
    refused("too_large", site, "http://a.example/stream", max_bytes=1000)


def test_compressed_pages_are_decoded_but_a_compression_bomb_cannot_exceed_the_cap(site):
    site.page("/gz", gzip.compress(PAGE), extra={"Content-Encoding": "gzip"})
    assert get(site, "http://a.example/gz")[0].body == PAGE
    site.page("/bomb", gzip.compress(bytes(20_000_000)), extra={"Content-Encoding": "gzip"})
    refused("too_large", site, "http://a.example/bomb", max_bytes=1_000_000)
    site.page("/br", b"whatever", extra={"Content-Encoding": "br"})
    refused("unsupported", site, "http://a.example/br")


def test_a_slow_server_is_given_up_on(site):
    site.routes["/slow"] = lambda h: (time.sleep(1.5), site.reply(h, 200, PAGE))
    started = time.monotonic()
    refused("timeout", site, "http://a.example/slow", step=0.3)
    assert time.monotonic() - started < 1.4  # it did not wait for the answer


def test_the_whole_fetch_has_a_deadline_however_many_steps_it_takes(site):
    ticks = iter([0.0, 0.0, 0.0, 1000.0, 1000.0, 1000.0, 1000.0])  # the clock jumps past the deadline after the first hop
    site.redirect("/a", "/b")
    site.page("/b", PAGE)
    refused("timeout", site, "http://a.example/a", clock=lambda: next(ticks, 1000.0))


def test_an_address_that_cannot_be_found_or_a_server_that_is_not_there_is_reported(site):
    def resolver_fails(host, port):
        raise PrepareError("unreachable", f"The address {host!r} could not be found.")

    with pytest.raises(PrepareError) as nowhere:
        fetch_page("http://nowhere.example/", max_bytes=1000, step_timeout=1, resolve=resolver_fails)
    assert nowhere.value.code == "unreachable"
    with pytest.raises(PrepareError) as empty:
        fetch_page("http://a.example/", max_bytes=1000, step_timeout=1, resolve=lambda h, p: [])
    assert empty.value.code == "unreachable"

    def refuses(address, port, timeout):
        raise ConnectionRefusedError("refused")

    with pytest.raises(PrepareError) as down:
        fetch_page("http://a.example/", max_bytes=1000, step_timeout=1, resolve=lambda h, p: [PUBLIC], connector=refuses)
    assert down.value.code == "unreachable"


# ---------------------------------------------------------------- TLS
def test_a_secure_connection_goes_to_the_checked_address_and_proves_the_certificate_is_the_names():
    """No certificate here, so the TLS layer is a recorder: it must be asked to verify the certificate against the host name, never the address."""
    calls = []
    sock = socket.socket()
    connection = F._Https("secure.example", 443, PUBLIC, 5.0, lambda address, port, timeout: calls.append((address, port)) or sock)

    class Recorder:
        def wrap_socket(self, raw, server_hostname=None):
            calls.append(("tls", server_hostname))
            return raw

    connection._context = Recorder()
    connection.connect()
    assert calls == [(PUBLIC, 443), ("tls", "secure.example")]
    sock.close()
