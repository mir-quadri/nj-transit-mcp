# Vercel serverless adapter.
# Vercel serves this file at /api/index.py; the MCP endpoint itself is
# at /mcp via vercel.json rewrites.
# Requires: MCP_BEARER_TOKEN set in Vercel env vars.
# Vercel's filesystem is read-only except /tmp, so GTFS is cached there
# (re-downloaded on cold starts; ~6 MB, parsed in seconds).
#
# BUG FOUND 2026-10-31 (simulation test, not a ChatGPT client):
# Vercel rewrites /mcp -> /api/index.py, so the ASGI scope path the
# app sees is /api/index.py, NOT /mcp. FastMCP's http_app is mounted
# at /mcp, so it answered 404 to the rewritten request, and the
# bearer middleware (which only checked paths starting with /mcp)
# never ran — a no-token POST /mcp got "Not Found" after ~15 s
# instead of 401. The wrapper below restores /mcp before the inner
# app (including its auth middleware) sees the request. Without it,
# a ChatGPT connector pointed at .../mcp would fail the same way.
import os
import sys
from pathlib import Path

os.environ.setdefault("TRANSIT_DATA_DIR", "/tmp/transit-data")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import build_http_app  # noqa: E402

_inner_app = build_http_app()


class VercelPathFix:
    """Restore the public /mcp path after Vercel's rewrite."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and (
            scope.get("path") == "/.well-known/openai-apps-challenge"
            or b"challenge=openai" in scope.get("query_string", b"")
        ):
            # OpenAI domain-verification challenge, issued in the
            # Plugins portal for the nj-transit-mcp draft (PRIVILEGE
            # SOLUTIONS org). This token is public by design: OpenAI
            # requires it served as plain text at this exact URL.
            # It is not an auth credential and grants no access.
            # NOTE: Vercel rewrites both /mcp and the challenge URL to
            # /api/index.py, so the rewritten scope path alone cannot
            # tell them apart — the challenge rewrite therefore carries
            # ?challenge=openai (see vercel.json), which is checked
            # here BEFORE the /mcp path fix below.
            body = b"T8JqPc28gYKlkbKn9GtdqbCyT24t7PenqSVdAFc7Rns"
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {"type": "http.response.body", "body": body, "more_body": False}
            )
            return
        if scope.get("type") == "http" and scope.get("path") in (
            "/api/index.py",
            "/api/index",
        ):
            scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
        await self.app(scope, receive, send)


app = VercelPathFix(_inner_app)
