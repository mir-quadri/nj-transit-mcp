# NJ Transit + PATH MCP Server — v1

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

- `search_stations(query, system="all")` — find station IDs.
  `system`: `nj_transit`, `path`, `all`.
- `next_departures(station, limit=10, system="nj_transit", line?, towards?)`
  — next departures. `station` can be a name or stop ID. Ambiguous
  names (e.g. "Secaucus" = Upper vs Lower Level) return candidates
  instead of guessing.
- `trip_between(origin, destination, limit=5)` — next same-trip
  NJ Transit trains, with departure, arrival, duration.
- `lines(system="all")` — all routes.
- `service_alerts(system="all")` — NJ alerts when credentials are
  set; otherwise an honest "not available" note (never invented).
- `server_status()` — GTFS date range/counts, whether live NJ data
  is on, PATH feed age. Ask this when you need to know if an
  answer is live or scheduled.

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

ChatGPT cannot use a local stdio server. It needs:

1. This server deployed at a public HTTPS URL with Streamable HTTP
   (`--transport http`), with auth: set `MCP_BEARER_TOKEN` in the
   host's env vars. `/mcp` then requires
   `Authorization: Bearer <that token>` (verified 2026-10-28:
   401 without/wrong token, 200 with it; `/health` stays public).
   Recommended host: **Railway** (long-lived service, `Dockerfile`
   + `railway.toml` included; set `PORT` automatically, plus
   `MCP_BEARER_TOKEN` and optionally `NJT_USERNAME`/`NJT_PASSWORD`).
   Alternative, untested on Vercel's function limits: **Vercel**
   serverless (`api/index.py` + `vercel.json`, stateless HTTP).
2. ChatGPT Developer Mode (Pro / Team / Enterprise / Edu):
   Settings → Apps & Connectors → Create → enter
   `https://your-server/mcp` and the bearer token as its auth header.

Public deployment status (2026-10-28, 09:34 ET): **prepared, not
yet live**. Auth, Dockerfile, `railway.toml`, and the Vercel
adapter are done and auth-tested locally. A public deploy still
needs Mir's approval to (a) create a GitHub repo for this code and
(b) deploy it on his Railway account (which may use his existing
plan's resources).

## What is NOT in v1

- **NJ Transit bus**: separate `bus_data` GTFS + BUSDATA API, not
  verified in this build. Next provider to add.
- **NYC Subway, SEPTA, Amtrak, ferries**: not in v1. The provider
  shape in `server.py` is designed so each is one new provider
  (GTFS + GTFS-Realtime URLs + station map), not a rewrite.
- **Transfers / multi-leg trips**: `trip_between` is same-trip only.
- **Fares, parking, accessibility**: not in the feeds used.
- **Auth on HTTP**: add a bearer-token check before public deploy.

## Next steps (in order)

1. Mir registers for NJ Transit developer credentials → live NJ
   delays verified end-to-end (the one unverified piece above).
2. Deploy the HTTP version — auth + Railway config DONE 2026-10-28;
   public deploy pending Mir's approval for a GitHub repo + Railway
   deploy (see ChatGPT section above).
3. Add NJ Transit bus, then MTA Subway as provider #3, reusing the
   same 6 tools.

## Sources (checked 2026-10-28)

- NJ Transit GTFS-RT docs: `developer.njtransit.com/doc/NJTRANSIT_Rail_GTFSRT_V1.pdf`
  — getToken / getGTFS / getAlerts / getTripUpdates /
  getVehiclePositions; 10 token calls/day, RT calls unlimited.
- NJ Transit developer portal: `datasource.njtransit.com` —
  BUSDATA, RailData, GTFS-BUS, GTFS-RAIL catalog.
- NJ Transit public GTFS: `https://www.njtransit.com/rail_data.zip`
  (also listed by Mobility Database, Sep 15 2026 – Mar 13 2027).
- PATH live GTFS-Realtime: `https://path.transitdata.nyc/gtfsrt`
  (community feed, ~5 s refresh; station IDs verified against the
  official PATH GTFS static feed).
