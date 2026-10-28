"""In-memory MCP test: calls every tool through FastMCP Client, no network client needed."""
import asyncio, json
from fastmcp import Client
from server import mcp

async def main():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print("TOOLS:", [t.name for t in tools])
        for name, args in [
            ("server_status", {}),
            ("search_stations", {"query": "Newark Penn"}),
            ("search_stations", {"query": "Newark", "system": "path"}),
            ("next_departures", {"station": "Newark Penn Station", "limit": 5}),
            ("next_departures", {"station": "New York Penn Station", "limit": 5}),
            ("next_departures", {"station": "Newark", "system": "path", "limit": 5}),
            ("next_departures", {"station": "Hoboken", "system": "path", "limit": 5}),
            ("trip_between", {"origin": "Newark Penn Station", "destination": "New York Penn Station", "limit": 3}),
            ("trip_between", {"origin": "Secaucus", "destination": "New York Penn Station", "limit": 3}),
            ("lines", {"system": "nj_transit"}),
            ("service_alerts", {}),
        ]:
            r = await client.call_tool(name, args)
            # FastMCP returns content blocks; print as text
            text = "".join(getattr(b, "text", "") or "" for b in r.content) if hasattr(r, "content") else str(r)
            print(f"\n=== {name} {args} ===\n{text[:2000]}")

asyncio.run(main())
