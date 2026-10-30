"""Snackibella as an MCP server, so Claude (desktop or web) can ask the Instamart
ads data questions. Runs next to the data on Render; Claude only reads the short
replies. Tools: ask_snackibella, data_overview, run_sql (read-only).

  python mcp_server.py            # HTTP on $PORT (Render), needs MCP_TOKEN
  python mcp_server.py --stdio    # local, for Claude Desktop on the same machine
"""
import os
import sys

import config
import ingest
from bot import mcp_tools

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# Render redeploys wipe the DB; rebuild it from the committed .csv.gz archives.
if not config.DB_PATH.exists() and list(config.PROCESSED_DIR.glob("*.csv.gz")):
    ingest.rebuild_from_processed()

mcp = FastMCP(
    "snackibella",
    host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
_PREV = {"meta": None}


@mcp.tool()
def ask_snackibella(question: str) -> str:
    """Ask a plain-English question about the Instamart ads data (spend, ROAS, cities,
    SKUs, campaigns, keywords, search queries, trends, budget moves). Compound questions
    joined by 'and' are answered part by part, and short follow-ups ('why?', 'and roas?',
    'and for Pune?') build on the previous question. Answers are computed from the loaded
    data, lead with the date range and are labelled as fact, assumption or hypothesis."""
    text, meta = mcp_tools.compact_answer(question, prev=_PREV["meta"])
    _PREV["meta"] = meta
    return text


@mcp.tool()
def data_overview() -> str:
    """Which tables and date ranges are loaded, with column names. Call before run_sql."""
    return mcp_tools.describe_data()


@mcp.tool()
def run_sql(query: str) -> str:
    """Run one read-only SELECT over granular, search_query or summary (DuckDB SQL). Max 200 rows.
    Use for custom cuts that ask_snackibella does not cover."""
    return mcp_tools.run_sql(query)


class _Bearer:
    """ASGI wrapper: every request needs 'Authorization: Bearer <MCP_TOKEN>'."""

    def __init__(self, app, token):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            hdr = dict(scope["headers"]).get(b"authorization", b"").decode()
            if hdr != f"Bearer {self.token}":
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await self.app(scope, receive, send)


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        mcp.run()
    else:
        token = os.environ.get("MCP_TOKEN")
        if not token:
            sys.exit("Set MCP_TOKEN before serving over HTTP.")
        import uvicorn
        uvicorn.run(_Bearer(mcp.streamable_http_app(), token), host="0.0.0.0", port=mcp.settings.port)
