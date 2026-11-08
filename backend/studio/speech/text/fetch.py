"""Fetching a web page safely (DESIGN.md §38.5 item 2, criterion 272).

This is the one request the studio makes to a place a person using the page names, and the studio has no login (§11), so anyone on the network can ask it
to fetch something. The rules exist so that cannot be used to look at the owner's own network (their Ollama, their router's admin page, a cloud's metadata
service):
  * `http` and `https` only, no user name or password in the address, only the standard ports 80 and 443;
  * the host name is looked up, and **every** address it gives must be a public one (not private, loopback, link-local, multicast, reserved, carrier-grade
    NAT, or a cloud metadata address; an IPv6 address that wraps an IPv4 one is judged by the IPv4 address);
  * the connection is made **to the address that was checked**, not to a second lookup of the name, which defeats a name that gives a different answer the
    second time ("DNS rebinding"); TLS still checks the certificate against the name;
  * redirects are followed here, not by the library, at most 5, and each one is checked in full again;
  * the size is capped while it streams in, with a time limit on every step and on the whole; only text, HTML and PDF are accepted;
  * no cookies, no login, nothing sent but a plain client name.
`resolve` and `connector` can be replaced, which is how the tests show the rules hold without a network.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urljoin, urlsplit

from .errors import PASTE_HINT, PrepareError

MAX_REDIRECTS = 5
MAX_URL_CHARS = 2048
ALLOWED_PORTS = (80, 443)
ACCEPTED_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/pdf")
USER_AGENT = "Mozilla/5.0 (compatible; ReadAloudStudio/1.20)"
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_6TO4 = ipaddress.ip_network("2002::/16")
_ALWAYS_REFUSED = tuple(ipaddress.ip_network(n) for n in ("169.254.169.254/32", "fd00:ec2::254/128"))  # cloud metadata services

Resolver = Callable[[str, int], list[str]]
Connector = Callable[[str, int, float], socket.socket]


@dataclass
class Fetched:
    """A page as it came: its bytes, its media type and charset (if the server named one), and the address it was finally fetched from."""

    body: bytes
    content_type: str
    charset: Optional[str]
    url: str


def is_public(address: str) -> bool:
    """Whether an IP address is one on the public internet (see the module's description)."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:  # a gateway to IPv4: judged by the IPv4 address inside it
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        elif ip in _6TO4:
            ip = ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    if any(ip in net for net in _ALWAYS_REFUSED):
        return False
    return ip.is_global and not ip.is_multicast


def default_resolver(host: str, port: int) -> list[str]:
    """The addresses a host name stands for (a literal address stands for itself; the odd spellings of IPv4, decimal and hex, are the resolver's to expand)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise PrepareError("unreachable", f"The address {host!r} could not be found.", PASTE_HINT) from exc
    return list(dict.fromkeys(info[4][0] for info in infos))


def default_connector(address: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((address, port), timeout)


def check_url(url: str) -> tuple[str, str, int, str]:
    """(scheme, host, port, path-and-query) of a web address, or a PrepareError saying what is wrong with it. An address with no scheme is taken as https."""
    url = url.strip()
    if not url or len(url) > MAX_URL_CHARS:
        raise PrepareError("bad_address", "Give a web address of up to 2,000 characters.")
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise PrepareError("bad_address", "That does not look like a web address.") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PrepareError("bad_address", "Only web addresses that start with http:// or https:// can be read.")
    if parts.username is not None or parts.password is not None:
        raise PrepareError("bad_address", "Web addresses with a user name or password in them are not used.", PASTE_HINT)
    port = port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise PrepareError("bad_address", "Only the standard web ports (80 and 443) are used.", PASTE_HINT)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, parts.hostname, port, path


def _pin(host: str, port: int, resolve: Resolver) -> str:
    """The address to connect to for `host`: the first of its addresses, after checking that all of them are public."""
    addresses = resolve(host, port)
    if not addresses:
        raise PrepareError("unreachable", f"The address {host!r} could not be found.", PASTE_HINT)
    for address in addresses:
        if not is_public(address):
            raise PrepareError("private_address", "That address is on a private or internal network, so the studio will not fetch it.",
                               "Only public websites can be read from a web address. " + PASTE_HINT)
    return addresses[0]


class _Http(http.client.HTTPConnection):
    """A plain connection that goes to the address that was checked."""

    def __init__(self, host: str, port: int, address: str, timeout: float, connector: Connector):
        super().__init__(host, port, timeout=timeout)
        self._address, self._connector = address, connector

    def connect(self) -> None:
        self.sock = self._connector(self._address, self.port, self.timeout)  # type: ignore[arg-type]


class _Https(http.client.HTTPSConnection):
    """A TLS connection that goes to the address that was checked but proves the certificate is the host's."""

    def __init__(self, host: str, port: int, address: str, timeout: float, connector: Connector):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address, self._connector = address, connector

    def connect(self) -> None:
        raw = self._connector(self._address, self.port, self.timeout)  # type: ignore[arg-type]
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)  # type: ignore[attr-defined]


def _decoder(encoding: str):
    """A decompressor for a body a server compressed although it was asked not to, or None for an uncompressed one. Raises for an encoding we do not read."""
    encoding = encoding.strip().lower()
    if encoding in ("", "identity"):
        return None
    if encoding in ("gzip", "x-gzip"):
        return zlib.decompressobj(16 + zlib.MAX_WBITS)
    if encoding == "deflate":
        return zlib.decompressobj()
    raise PrepareError("unsupported", f"The site answered in a format ({encoding}) that is not read.", PASTE_HINT)


def fetch_page(url: str, *, max_bytes: int, step_timeout: float, total_timeout: float = 60.0, resolve: Optional[Resolver] = None,
               connector: Optional[Connector] = None, clock: Callable[[], float] = time.monotonic) -> Fetched:
    """The page at `url`, or a PrepareError. See the module's description for every rule that is kept on the way."""
    resolve = resolve or default_resolver
    connector = connector or default_connector
    deadline = clock() + total_timeout
    current = url
    for _redirect in range(MAX_REDIRECTS + 1):
        scheme, host, port, path = check_url(current)
        address = _pin(host, port, resolve)  # judged again for every hop
        remaining = deadline - clock()
        if remaining <= 0:
            raise PrepareError("timeout", "The page took too long to fetch.", PASTE_HINT)
        connection = (_Https if scheme == "https" else _Http)(host, port, address, min(step_timeout, remaining), connector)
        try:
            try:
                connection.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,application/pdf;q=0.9,*/*;q=0.1",
                                                          "Accept-Encoding": "identity", "Connection": "close"})
                response = connection.getresponse()
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                kind = "timeout" if isinstance(exc, (socket.timeout, TimeoutError)) else "unreachable"
                raise PrepareError(kind, "The page could not be fetched: " + (str(exc) or type(exc).__name__)[:120], PASTE_HINT) from exc
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                response.read(1024)
                if not location:
                    raise PrepareError("unreachable", "The site sent the studio somewhere but did not say where.", PASTE_HINT)
                current = urljoin(current if "://" in current else "https://" + current, location)
                continue
            if response.status != 200:
                reasons = {401: "needs a login", 403: "refused the request", 404: "has no such page", 410: "no longer has that page",
                           429: "is limiting requests", 451: "cannot show that page"}
                raise PrepareError("unreachable", f"The site {reasons.get(response.status, 'answered with an error')} ({response.status}).", PASTE_HINT)
            content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
            charset = None
            for piece in (response.getheader("Content-Type") or "").split(";")[1:]:
                key, _, value = piece.strip().partition("=")
                if key.lower() == "charset":
                    charset = value.strip().strip("\"'") or None
            if content_type not in ACCEPTED_TYPES:
                raise PrepareError("unsupported", f"The address leads to a {content_type or 'file of unknown type'}, not a page of text.", PASTE_HINT)
            declared = response.getheader("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise PrepareError("too_large", f"The page is larger than the {max_bytes // (1024 * 1024)} MB that is read.", PASTE_HINT)
            decoder = _decoder(response.getheader("Content-Encoding") or "")
            body = bytearray()
            while True:
                if clock() > deadline:
                    raise PrepareError("timeout", "The page took too long to fetch.", PASTE_HINT)
                try:
                    chunk = response.read(65536)
                except (OSError, http.client.HTTPException) as exc:
                    kind = "timeout" if isinstance(exc, (socket.timeout, TimeoutError)) else "unreachable"
                    raise PrepareError(kind, "The page stopped arriving part-way.", PASTE_HINT) from exc
                if not chunk:
                    break
                if decoder is not None:
                    try:
                        chunk = decoder.decompress(chunk, max_bytes + 1 - len(body))  # never more than the cap, whatever the compression ratio
                    except zlib.error as exc:
                        raise PrepareError("unreachable", "The page arrived damaged.", PASTE_HINT) from exc
                body += chunk
                if len(body) > max_bytes:
                    raise PrepareError("too_large", f"The page is larger than the {max_bytes // (1024 * 1024)} MB that is read.", PASTE_HINT)
            return Fetched(bytes(body), content_type, charset, current)
        finally:
            connection.close()
    raise PrepareError("unreachable", f"The address sends the studio around in circles (more than {MAX_REDIRECTS} redirects).", PASTE_HINT)
