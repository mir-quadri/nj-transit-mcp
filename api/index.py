# Vercel serverless adapter (alternative to Railway).
# Vercel serves this file at /api/index.py; the MCP endpoint itself is
# at /mcp via the app returned here, mounted by vercel.json rewrites.
# Requires: MCP_BEARER_TOKEN set in Vercel env vars.
# Vercel's filesystem is read-only except /tmp, so GTFS is cached there
# (re-downloaded on cold starts; ~6 MB, parsed in seconds).
import os
import sys
from pathlib import Path

os.environ.setdefault("TRANSIT_DATA_DIR", "/tmp/transit-data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import build_http_app  # noqa: E402

app = build_http_app()
