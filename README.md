# NJ Transit + PATH + NJ Transit bus MCP Server — v1.3

A Model Context Protocol server that gives any MCP-compatible AI
(Claude, Gemini CLI, ChatGPT, Cursor, VS Code, …) real NJ Transit
and PATH schedules instead of guessed ones.

## Why Gemini couldn't do this before

The consumer Gemini app/web chat has no live-transit tool and no MCP
support — it answers from training data and web snippets, which is why
a "live schedule" request fails or goes stale. An MCP client that can
call *this* server gets the answer from a feed at query time.

Important, verified distinction in v1:

| System | v1 data | Key needed? |
|---|---|---|
| NJ Transit rail + light rail (17 routes) | **Scheduled** departures from official GTFS `rail_data.zip` | No |
| NJ Transit live delays / cancellations | GTFS-Realtime TripUpdates + Alerts via `raildata.njtransit.com` | **Yes — free registration** (see below) |
| PATH (Newark ↔ NYC) | **Live** arrivals from GTFS-Realtime, ~5 s updates | No |

Every response labels its `data_source`: `scheduled`,
`live_gtfs_rt`, or an explicit note. Nothing is ever presented as
"live" when it is only scheduled.

Note on "subways": NJ Transit has no subway. Its subway-like services
are the light-rail lines in this same file — Hudson-Bergen (HBLR),
Newark Light Rail (NLR), River Line (RVLN) — plus PATH, which is the
NJ↔NYC rapid-transit system. Both are in v1.

## Tools (6)

- `search_stations(query, system="all")` — find station/stop IDs.
  `system`: `nj_transit`, `nj_bus`, `path`, `all`.
- `next_departures(station, limit=10, system="nj_transit", line?, towards?)`
  — next departures. `station` can be a name or stop ID. Ambiguous
  names (e.g. "Secaucus" = Upper vs Lower Level, "PORT AUTHORITY
  BUS TERMINAL" = 4 stop IDs) return candidates instead of guessing.
  Use `system="nj_bus"` for bus (scheduled only in v1.3).
- `trip_between(origin, destination, limit=5, system="nj_transit")` —
  next same-trip NJ Transit trains/buses, with departure, arrival,
  duration. Add `system="nj_bus"` for bus.
- `lines(system="all")` — all routes (285 total: 17 rail + 263 bus + 5 PATH).
- `service_alerts(system="all")` — NJ rail alerts when credentials are
  set; bus and PATH alerts reported as not available, never invented.
- `server_status()` — GTFS date range/counts for rail and bus,
  whether live NJ data is on, PATH feed age. Ask this when you need
  to know if an answer is live or scheduled.

## Verified test — 2026-10-28, ~09:16 ET

Run with `python test_client.py` (in-memory FastMCP client) and over
Streamable HTTP on port 8123. Both passed, all 6 tools listed.

- `server_status`: GTFS Sep 15 2026 – Mar 13 2027, 230 stops,
  17 routes, 6,197 trips; PATH feed live at 09:16:39 ET, 54 entities.
- Newark Penn → next: 09:17 NEC to New York Penn, 09:17 RARV to
  Raritan, 09:23 NEC to New York Penn (scheduled).
- New York Penn outbound → next: 09:24 NJCL to Secaucus Upper Level,
  09:42 NEC to Trenton.
- Newark Penn → New York Penn: 09:17 → 09:39 (22 min, NEC),
  09:23 → 09:41 (18 min, NEC).
- PATH live: Newark → 09:15 (now), 09:20 to World Trade Center;
  Hoboken → 09:16 to WTC, 09:17 to 33rd St.

## Run it

```bash
cd ~/workspace/mcp-servers/nj-transit
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py            # stdio (default)
.venv/bin/python server.py --transport http --port 8000   # remote
```

GTFS is downloaded on first run to `data/rail_data.zip` and refreshed
if older than 24 h. No credentials are logged; the NJ token (if set)
is cached in `data/.njt_token.json` (0600) for the day, because
`getToken` is limited to 10 calls/day (TripUpdates itself is
unlimited once you have a token).

## Enable LIVE NJ Transit delays (free, 5 minutes)

1. Register at https://developer.njtransit.com/registration
   (also listed as https://datasource.njtransit.com). You get Test
   and Production credentials by email.
2. Set them before starting the server:

```bash
export NJT_USERNAME="your-username"
export NJT_PASSWORD="your-password"
# optional: NJT_BASE_URL=https://raildata.njtransit.com (default)
```

3. Call `server_status` — `nj_live_enabled` should be `true`, and
   departures will carry `data_source: "live_gtfs_rt"` with
   `delay_min` / `status` when the feed reports one.
   Without these variables the server still works, scheduled-only.

Caveat to verify on first live run: the GTFS-Realtime `trip_id`
matching against GTFS `trip_id` is implemented defensively (exact
match); if NJ's RT feed uses a different trip-ID scheme, live
departures will stay scheduled and `server_status` /
`live_info.live_trips_in_feed` will show the feed was reached but
did not match. That is the one unverified piece in v1, because it
cannot be tested without credentials.

## Connect it

### Gemini CLI (this is the Gemini route that supports MCP)

`~/.gemini/settings.json` (or `.gemini/settings.json` in a project):

```json
{
  "mcpServers": {
    "nj-transit": {
      "command": "/path/to/mcp-servers/nj-transit/.venv/bin/python",
      "args": ["/path/to/mcp-servers/nj-transit/server.py"],
      "env": { "NJT_USERNAME": "", "NJT_PASSWORD": "" },
      "timeout": 30000
    }
  }
}
```

Or: `gemini mcp add --transport stdio nj-transit -- /path/to/.venv/bin/python /path/to/server.py`
Then ask: *"Next NJ Transit train from Newark Penn to New York Penn?"*
— Gemini CLI will call `trip_between`.

The consumer Gemini app (gemini.google.com / mobile app) does **not**
support MCP as of this build, so this server cannot be plugged into
that app directly. Gemini CLI, Gemini API / ADK (`MCPToolset` with
`StdioServerParameters`), and third-party Gemini-API clients can.

### Claude Desktop / Claude Code / Cursor / VS Code

Stdio, same command/args as above, in `claude_desktop_config.json`
under `mcpServers` (or the client's equivalent MCP settings).

### ChatGPT

ChatGPT cannot use a local stdio server. It needs a public HTTPS
URL with Streamable HTTP. This server is now free and public, no
auth required:

- Public URL: https://nj-transit-mcp.vercel.app/mcp
- ChatGPT Developer Mode (Pro / Team / Enterprise / Edu):
  Settings → Apps & Connectors → Create → enter
  `https://nj-transit-mcp.vercel.app/mcp`, Authentication: None.

Public deployment status (2026-10-31, 17:35 ET): **LIVE and FREE
on Vercel** — the bearer token was removed, the production
deployment (main, commit af9e8a6, Public submission pack v1.3)
redeployed Ready, and POST `/mcp` with no Authorization header
was verified returning a 200 MCP initialize response.
Source: https://github.com/mir-quadri/nj-transit-mcp (public).
Official MCP Registry: `io.github.mir-quadri/nj-transit-mcp`
v1.3.0, status active (published 2026-11-01).
Note: in free mode there is no `/health` route — GET `/` and
GET `/health` return 404; GET `/mcp` returns 405 (POST only).
Rate limit: 60 requests/minute per IP per instance.

## NJ Transit bus (v1.2, system='nj_bus')

Same 6 tools, bus provider only. Source: official public GTFS
https://www.njtransit.com/bus_data.zip — 263 routes, 16,563 stops,
33,287 trips, 1,776,403 stop_times rows, service Oct 20 2026–Apr 17
2027 (calendar_dates only, same quirk as rail: a trip runs iff its
service_id is listed for that date). Use `system='nj_bus'` for
`search_stations`, `next_departures`, `trip_between` (add
`system='nj_bus'`), and `lines`. Bus stops are street stops, so
ambiguous names (e.g. "PORT AUTHORITY BUS TERMINAL" = 4 stop IDs)
return candidates instead of guessing — use a stop ID when in doubt.

Honest limits: bus departures are **scheduled only** in v1.2.
BUSDATA live delays/alerts are NOT enabled (separate API, not
verified), so nothing bus is ever labelled live. Because stop_times
is 1.7M rows, the server streams it per query instead of loading it
into memory (shapes.txt is never read): a bus departure query takes
~4–5 s locally, and the first bus query after a cold start takes
~5 s to load metadata. Verified locally 2026-10-28 ~20:48 ET:
Port Authority stop 3511 → 20:48 bus 119, 190, 320; Newark Penn bus
stop 43283 → 21:28/22:28/23:28 bus 108 to New York; trip 108
Clifton Ave at E 7th Ave → Pavillion Apartments Tower South
21:15 → 21:18. Rail tools are unchanged (17 routes, 285 lines total
across rail + bus + PATH).

## What is NOT in v1.3

- **NJ Transit bus live delays/alerts**: BUSDATA, not enabled.
- **NYC Subway, SEPTA, Amtrak, ferries**: not in v1.2. The provider
  shape in `server.py` is designed so each is one new provider
  (GTFS + GTFS-Realtime URLs + station map), not a rewrite.
- **Transfers / multi-leg trips**: `trip_between` is same-trip only.
- **Fares, parking, accessibility**: not in the feeds used.

## Next steps (in order)

1. External ChatGPT test of the public `/mcp` endpoint (definitive
   verification, see above).
2. Mir registers for NJ Transit developer credentials → live NJ
   rail delays verified end-to-end (the one unverified piece above),
   and bus BUSDATA live if wanted.
3. Add MTA Subway as provider #3, reusing the same 6 tools.

## Sources (checked 2026-10-28)

- NJ Transit GTFS-RT docs: `developer.njtransit.com/doc/NJTRANSIT_Rail_GTFSRT_V1.pdf`
  — getToken / getGTFS / getAlerts / getTripUpdates /
  getVehiclePositions; 10 token calls/day, RT calls unlimited.
- NJ Transit developer portal: `datasource.njtransit.com` —
  BUSDATA, RailData, GTFS-BUS, GTFS-RAIL catalog.
- NJ Transit public GTFS: `https://www.njtransit.com/rail_data.zip`
  (also listed by Mobility Database, Sep 15 2026 – Mar 13 2027).
- NJ Transit bus public GTFS: `https://www.njtransit.com/bus_data.zip`
  (263 routes, 16,563 stops, Oct 20 2026 – Apr 17 2027, checked 2026-10-28).
- PATH live GTFS-Realtime: `https://path.transitdata.nyc/gtfsrt`
  (community feed, ~5 s refresh; station IDs verified against the
  official PATH GTFS static feed).
