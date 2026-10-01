"""Run the server: `python -m studio` (configuration via environment variables, DESIGN.md §13)."""

from __future__ import annotations

import logging
import sys

from .config import ConfigError, Settings


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s", stream=sys.stderr)
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    import uvicorn

    from .api import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
        proxy_headers=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
