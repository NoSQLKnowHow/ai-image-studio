"""HTTP hardening for an open-LAN, no-login app (DESIGN.md §11).

- Mutating /api requests must carry `X-Studio-Client: 1`. Browsers cannot send that
  header cross-site without a CORS preflight, and this server answers no preflight,
  so a web page you happen to visit cannot queue jobs or delete history here.
- Optional Host allow-list (STUDIO_ALLOWED_HOSTS) against DNS-rebinding tricks; loopback
  names always pass.
- Security headers on every response (strict CSP, nosniff, no framing).

Written as plain ASGI middleware so streaming responses (server-sent events) pass
through untouched.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Iterable

CLIENT_HEADER = "x-studio-client"
# Always accepted by the Host check: DNS rebinding works through the attacker's own host name, so a
# loopback name only arrives from the machine itself (the container healthcheck, curl on the Spark).
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})  # as hostname() returns them
SAFE_METHODS = frozenset({"GET", "HEAD"})

CSP = "; ".join([
    "default-src 'self'",
    "img-src 'self' data: blob:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    "connect-src 'self'",
    "font-src 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])

SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"content-security-policy", CSP.encode("latin-1")),
]

Scope = dict[str, Any]
Message = dict[str, Any]
Send = Callable[[Message], Awaitable[None]]


def hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):  # IPv6 literal, e.g. [::1]:8080
        end = host.find("]")
        return host[1:end] if end != -1 else host
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host


async def _reject(send: Send, status: int, detail: str, code: str) -> None:
    body = json.dumps({"detail": detail, "code": code}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), *SECURITY_HEADERS],
    })
    await send({"type": "http.response.body", "body": body})


class SecurityMiddleware:
    def __init__(self, app: Any, allowed_hosts: Iterable[str] = ()):
        self.app = app
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)

    async def __call__(self, scope: Scope, receive: Any, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = hostname(headers.get("host", ""))
        if self.allowed_hosts and host not in self.allowed_hosts and host not in LOOPBACK:
            await _reject(send, 400, "This host name is not allowed (STUDIO_ALLOWED_HOSTS).", "host_not_allowed")
            return
        if (scope["path"].startswith("/api/") and scope["method"] not in SAFE_METHODS
                and headers.get(CLIENT_HEADER) != "1"):
            await _reject(send, 403, "Missing X-Studio-Client header.", "missing_client_header")
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                message = dict(message)
                message["headers"] = [*message.get("headers", []), *((k, v) for k, v in SECURITY_HEADERS if k not in existing)]
            await send(message)

        await self.app(scope, receive, send_with_headers)
