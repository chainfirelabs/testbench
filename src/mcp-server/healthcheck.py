"""Container healthcheck: this server is listening, and the API it fronts answers.

Both halves matter. The MCP server is useless without the TestBench API,
and a misconfigured `TB_MCP_API_BASE` otherwise shows up only as every tool call
failing at request time.
"""

import os
import socket
import sys
import urllib.request

try:
    port = int(os.environ.get("TB_MCP_PORT", "8003"))
    socket.create_connection(("127.0.0.1", port), timeout=3).close()
    base = os.environ.get("TB_MCP_API_BASE", "http://backend:8000/api/v1").rstrip("/")
    with urllib.request.urlopen(f"{base}/health", timeout=3) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception as exc:  # noqa: BLE001
    print(exc, file=sys.stderr)
    sys.exit(1)
