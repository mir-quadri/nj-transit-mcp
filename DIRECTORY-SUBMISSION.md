# Directory submission pack — NJ Transit MCP Server v1.3

Public endpoint: https://nj-transit-mcp.vercel.app/mcp (Streamable HTTP, free, no auth, rate-limited 60/min per IP per instance)
Repo: https://github.com/mir-quadri/nj-transit-mcp
Terms: https://github.com/mir-quadri/nj-transit-mcp/blob/main/TERMS.md
Privacy: https://github.com/mir-quadri/nj-transit-mcp/blob/main/PRIVACY.md
Unofficial: not affiliated with or endorsed by NJ Transit or PATH.

## Tool annotations (all 6 tools)
Every tool: readOnlyHint=true, destructiveHint=false, idempotentHint=false
(time-dependent answers), openWorldHint=true (external public feeds).
Justification: all tools only read public schedule/live feeds; none
books, buys, cancels, writes, or changes anything.

- search_stations — find station/stop IDs by name (nj_transit / nj_bus / path / all)
- next_departures — next departures; NJ rail/bus labelled scheduled, PATH labelled live_gtfs_rt
- trip_between — next same-trip trains/buses, departure/arrival/duration, no transfers
- lines — all routes (285 total: 17 rail + 263 bus + 5 PATH)
- service_alerts — NJ rail alerts when credentials set; bus/PATH reported as unavailable, never invented
- server_status — GTFS date ranges/counts, live-vs-scheduled status, PATH feed age

## Positive test cases (5)
1. "Using the NJ Transit connector, call server_status and tell me the date ranges for rail and bus, and whether each system is live or scheduled."
   Expected: rail Sep 15 2026–Mar 13 2027 scheduled, bus Oct 20 2026–Apr 17 2027 scheduled, PATH live.
2. "Using the NJ Transit connector, what is the next train from Newark Penn Station to New York Penn Station?"
   Expected: a same-trip NEC/NJCL result with departure, arrival, duration, data_source scheduled.
3. "Using the NJ Transit connector, what is the next PATH train from Hoboken to World Trade Center?"
   Expected: a live PATH arrival (route 860) with minutes away, data_source live_gtfs_rt.
4. "Using the NJ Transit connector with system nj_bus, what are the next buses from Port Authority Bus Terminal stop 3511?"
   Expected: scheduled buses (e.g. 128, 163, 119) with times and destinations; stop ID used because the name matches 4 bus stops.
5. "Using the NJ Transit connector, list all NJ Transit bus lines and tell me how many there are."
   Expected: 263 bus routes, short_name is the bus number.

## Negative test cases (3)
1. "Using the NJ Transit connector, book me a ticket from Newark Penn to New York Penn."
   Expected: refusal / no booking — the server has no booking tool; it only reads schedules.
2. "Using the NJ Transit connector, what is the next train from a station that does not exist, e.g. 'Nowhere Station'?"
   Expected: a "no station matches" error with candidates or a clear error, never an invented departure.
3. "Using the NJ Transit connector, tell me the live delay for a bus."
   Expected: scheduled-only answer with the explicit note that bus live delays via BUSDATA are not enabled — never an invented live delay.
