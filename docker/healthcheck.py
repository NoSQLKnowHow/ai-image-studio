"""Container healthcheck: is the web server answering? (Not whether the model is loaded: it
loads on demand and unloads when idle, and neither makes the container unhealthy.)"""

import os
import sys
import urllib.request

port = os.environ.get("STUDIO_PORT", "8080")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=4) as response:
        sys.exit(0 if response.status == 200 else 1)
except Exception as exc:  # noqa: BLE001 - any failure means unhealthy
    print(f"unhealthy: {exc}", file=sys.stderr)
    sys.exit(1)
