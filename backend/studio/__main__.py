"""Run the server: `python -m studio` (configuration via environment variables, DESIGN.md §13)."""

from __future__ import annotations

import logging
import sys

from .config import ConfigError, Settings

# Server-sent-event streams never end on their own. Normally each stream notices the shutdown
# within about a second and closes itself (see api.server_stopping). This deadline is the
# safety net for a connection that doesn't: after it, uvicorn cancels whatever is still open
# and the normal shutdown (stop the worker, record the interrupted run) proceeds. Keep it
# well under `docker stop`'s grace period.
GRACEFUL_SHUTDOWN_SECONDS = 5


def build_server(settings: Settings):  # -> uvicorn.Server (imported lazily)
    import uvicorn

    from .api import create_app

    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=False,
        server_header=False,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = uvicorn.Server(config)
    app.state.server = server  # lets event streams see that shutdown has begun
    return server


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stderr)
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    # Start-up failures (port in use, data directory not writable, ...) make uvicorn itself
    # exit with code 3 after logging the reason.
    try:
        build_server(settings).run()
    except KeyboardInterrupt:
        pass  # Ctrl-C: uvicorn re-raises it after the shutdown has already completed cleanly
    return 0


if __name__ == "__main__":
    sys.exit(main())
