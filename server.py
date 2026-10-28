"""
NJ Transit + PATH MCP server (v1) — for Gemini CLI, Claude, ChatGPT, Cursor, etc.

Why this exists: the consumer Gemini app has no live-transit tool, so it
guesses from training data. An MCP client that can call tools gets the
answer from a real feed instead.

What v1 actually does, honestly labelled in every response:
  * NJ Transit rail + light rail (17 routes, 230 stops in the current
    GTFS file): SCHEDULED departures from the official public GTFS file
    https://www.njtransit.com/rail_data.zip  (service Sep 15 2026-Mar 13 2027
    in the copy fetched 2026-10-28; the server re-downloads it daily).
    That file has no calendar.txt — service is defined only by
    calendar_dates.txt, so a trip runs on a date iff that date appears
    for its service_id with exception_type=1.
  * NJ Transit LIVE delays/cancellations: only when the operator sets
    NJT_USERNAME / NJT_PASSWORD (free registration at
    https://developer.njtransit.com/registration). The server then calls
    raildata.njtransit.com getToken once per day (10 token calls/day
    limit) and getTripUpdates (unlimited) and overlays delays on the
    scheduled departures. Without those credentials every NJ departure
    is explicitly labelled "scheduled", never "live".
  * PATH (Port Authority Trans-Hudson, the NJ<->NYC "subway" — NJ Transit
    itself has no subway; its closest equivalents are Hudson-Bergen /
    Newark / River Line light rail, which ARE in the NJ file above):
    TRULY LIVE arrivals, no key needed, from the community GTFS-Realtime
    feed https://path.transitdata.nyc/gtfsrt (updated every ~5s), with
    station IDs verified against the official PATH GTFS static feed.

Architecture for the wider goal (one server per domain, providers
pluggable): providers live in this file as small classes behind a
common shape — search_stations / next_departures / lines / alerts.
Adding MTA Subway, SEPTA, Amtrak etc. means adding one provider class
with its GTFS + GTFS-Realtime URLs and a station map, not rewriting
the MCP tools. Bus is deliberately NOT in v1: NJ Transit bus GTFS
(bus_data) and BUSDATA are a separate feed/API and were not verified
in this build; see README "What is not in v1".

Transports: stdio by default (Claude Desktop, Gemini CLI, Cursor,
VS Code). Streamable HTTP with `python server.py --transport http
--port 8000` for ChatGPT Developer Mode / remote clients, which
cannot use stdio.

Run locally:  .venv/bin/python server.py
Env (all optional): NJT_USERNAME, NJT_PASSWORD, NJT_BASE_URL
  (default https://raildata.njtransit.com), NJT_GTFS_URL
  (default https://www.njtransit.com/rail_data.zip),
  PATH_RT_URL (default https://path.transitdata.nyc/gtfsrt),
  MCP_TRANSPORT, MCP_PORT, TRANSIT_DATA_DIR.
No credentials are ever logged.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import threading
import time
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastmcp import FastMCP

ET = ZoneInfo("America/New_York")
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("TRANSIT_DATA_DIR", BASE_DIR / "data"))
NJT_GTFS_URL = os.environ.get(
    "NJT_GTFS_URL", "https://www.njtransit.com/rail_data.zip"
)
PATH_RT_URL = os.environ.get("PATH_RT_URL", "https://path.transitdata.nyc/gtfsrt")
NJT_BASE_URL = os.environ.get(
    "NJT_BASE_URL", "https://raildata.njtransit.com"
).rstrip("/")

mcp = FastMCP("nj-transit")

# ---------------------------------------------------------------- GTFS

def _parse_gtfs_time(t: str) -> int:
    """'25:30:00' -> minutes since midnight of the service date (can exceed 1440)."""
    h, m, s = (int(x) for x in t.strip().split(":"))
    return h * 60 + m


def _fmt_time(minutes: int) -> str:
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class GtfsFeed:
    """Minimal in-memory GTFS reader: stops, routes, trips, stop_times,
    calendar_dates. Loads once, refreshes if the cached zip is >24h old."""

    def __init__(self, data_dir: Path, url: str):
        self.data_dir = data_dir
        self.url = url
        self.zip_path = data_dir / "rail_data.zip"
        self._lock = threading.Lock()
        self.loaded_at: float | None = None
        self.stops: dict[str, dict] = {}
        self.routes: dict[str, dict] = {}
        self.trips: dict[str, dict] = {}
        self.stop_times_by_stop: dict[str, list[dict]] = defaultdict(list)
        self.max_seq: dict[str, int] = {}
        self.services_by_date: dict[str, set[str]] = defaultdict(set)
        self.date_range: tuple[str, str] | None = None

    def _download(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(
            self.url, headers={"User-Agent": "nj-transit-mcp/1.0"}
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = resp.read()
        self.zip_path.write_bytes(data)

    def _load_from_zip(self) -> None:
        with zipfile.ZipFile(self.zip_path) as z:
            def read(name: str):
                if name not in z.namelist():
                    return []
                with z.open(name) as fh:
                    text = io.TextIOWrapper(fh, encoding="utf-8-sig")
                    return list(csv.DictReader(text))

            self.stops = {r["stop_id"]: r for r in read("stops.txt")}
            self.routes = {r["route_id"]: r for r in read("routes.txt")}
            self.trips = {r["trip_id"]: r for r in read("trips.txt")}
            self.stop_times_by_stop = defaultdict(list)
            self.max_seq = {}
            for r in read("stop_times.txt"):
                self.stop_times_by_stop[r["stop_id"]].append(r)
                seq = int(r["stop_sequence"])
                if seq > self.max_seq.get(r["trip_id"], 0):
                    self.max_seq[r["trip_id"]] = seq
            self.services_by_date = defaultdict(set)
            dates = []
            for r in read("calendar_dates.txt"):
                if r["exception_type"] == "1":
                    self.services_by_date[r["date"]].add(r["service_id"])
                    dates.append(r["date"])
            if dates:
                self.date_range = (min(dates), max(dates))
        self.loaded_at = time.time()

    def ensure(self) -> None:
        with self._lock:
            if self.loaded_at is not None and (
                time.time() - self.loaded_at < 24 * 3600
            ):
                return
            stale = (
                not self.zip_path.exists()
                or (time.time() - self.zip_path.stat().st_mtime) > 24 * 3600
            )
            if stale:
                try:
                    self._download()
                except Exception:
                    # Never fail the whole server for a refresh: fall back
                    # to the cached zip if one exists, else re-raise.
                    if not self.zip_path.exists():
                        raise
            self._load_from_zip()

    def search(self, query: str, limit: int = 20) -> list[dict]:
        q = query.strip().lower()
        if not q:
            return []
        exact, partial = [], []
        for s in self.stops.values():
            name = s["stop_name"]
            ln = name.lower()
            item = {
                "stop_id": s["stop_id"],
                "name": name,
                "lat": s.get("stop_lat"),
                "lon": s.get("stop_lon"),
            }
            if ln == q:
                exact.append(item)
            elif q in ln:
                partial.append(item)
        # Station-level names first: prefer shorter / non-platform names.
        partial.sort(key=lambda x: (len(x["name"]), x["name"]))
        return (exact + partial)[:limit]

    def resolve(self, query: str) -> tuple[dict | None, list[dict], str | None]:
        """Returns (station, candidates, error). Exact name wins; a single
        substring match wins; anything else is ambiguous, not guessed."""
        q = query.strip()
        if q.isdigit() and q in self.stops:
            return self.stops[q], [], None
        matches = self.search(q, limit=20)
        if not matches:
            return None, [], f"No NJ Transit station matches '{q}'."
        exact = [m for m in matches if m["name"].lower() == q.lower()]
        if exact:
            return self.stops[exact[0]["stop_id"]], [], None
        if len(matches) == 1:
            return self.stops[matches[0]["stop_id"]], [], None
        return None, matches, (
            f"'{q}' matches {len(matches)} stations; "
            "ask the user to pick one, e.g. 'Newark Penn Station'."
        )


nj_feed = GtfsFeed(DATA_DIR, NJT_GTFS_URL)

# ------------------------------------------------------- NJ live (key-gated)

class NjLive:
    """Official NJ Transit GTFS-Realtime overlay. Token is fetched at most
    once per process run / per day (cached on disk); trip updates are
    fetched per call and cached 60s. All failures degrade to scheduled."""

    def __init__(self, base_url: str, data_dir: Path):
        self.base_url = base_url
        self.token_path = data_dir / ".njt_token.json"
        self._token: str | None = None
        self._updates: dict | None = None
        self._updates_at = 0.0
        self._lock = threading.Lock()
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(os.environ.get("NJT_USERNAME") and os.environ.get("NJT_PASSWORD"))

    def _post(self, path: str, fields: dict[str, str]) -> bytes:
        boundary = "----njtmcp"
        body = b""
        for k, v in fields.items():
            body += (
                f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="{k}"\r\n\r\n{v}\r\n'
            ).encode()
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    def token(self) -> str | None:
        if self._token:
            return self._token
        try:
            if self.token_path.exists():
                saved = json.loads(self.token_path.read_text())
                # Token is only ever re-fetched via getToken (10/day), so a
                # token saved today is reused for the rest of the day.
                if saved.get("date") == datetime.now(ET).strftime("%Y-%m-%d"):
                    self._token = saved["token"]
                    return self._token
        except Exception:
            pass
        try:
            raw = self._post(
                "/api/GTFSRT/getToken",
                {
                    "username": os.environ["NJT_USERNAME"],
                    "password": os.environ["NJT_PASSWORD"],
                },
            )
            data = json.loads(raw.decode("utf-8", "ignore"))
            if data.get("Authenticated") == "True" and data.get("UserToken"):
                self._token = data["UserToken"]
                try:
                    self.token_path.write_text(
                        json.dumps(
                            {
                                "date": datetime.now(ET).strftime("%Y-%m-%d"),
                                "token": self._token,
                            }
                        )
                    )
                    os.chmod(self.token_path, 0o600)
                except Exception:
                    pass
                return self._token
            self.last_error = "NJ Transit rejected the credentials."
        except Exception as e:  # noqa: BLE001 - degrade, don't crash the tool
            self.last_error = f"getToken failed: {e}"
        return None

    def trip_updates(self) -> dict:
        """trip_id -> {stop_id: {delay_min, time, status}}; {} if unavailable."""
        now = time.time()
        if self._updates is not None and now - self._updates_at < 60:
            return self._updates
        with self._lock:
            if self._updates is not None and now - self._updates_at < 60:
                return self._updates
            result: dict = {}
            tok = self.token() if self.configured else None
            if tok:
                try:
                    from google.transit import gtfs_realtime_pb2

                    raw = self._post("/api/GTFSRT/getTripUpdates", {"token": tok})
                    feed = gtfs_realtime_pb2.FeedMessage()
                    feed.ParseFromString(raw)
                    for entity in feed.entity:
                        if not entity.HasField("trip_update"):
                            continue
                        tu = entity.trip_update
                        tid = tu.trip.trip_id
                        per_stop: dict = {}
                        for stu in tu.stop_time_update:
                            if stu.HasField("arrival"):
                                t = stu.arrival.time
                                delay = stu.arrival.delay // 60 if stu.arrival.HasField("delay") else None
                            elif stu.HasField("departure"):
                                t = stu.departure.time
                                delay = stu.departure.delay // 60 if stu.departure.HasField("delay") else None
                            else:
                                continue
                            per_stop[stu.stop_id] = {
                                "time": t,
                                "delay_min": delay,
                                "status": str(stu.schedule_relationship),
                            }
                        if per_stop:
                            result[tid] = per_stop
                    self.last_error = None
                except Exception as e:  # noqa: BLE001
                    self.last_error = f"getTripUpdates failed: {e}"
            self._updates, self._updates_at = result, now
            return result

    def alerts(self) -> tuple[list[dict], str | None]:
        tok = self.token() if self.configured else None
        if not tok:
            return [], "NJ live alerts need NJT_USERNAME/NJT_PASSWORD (free developer registration)."
        try:
            from google.transit import gtfs_realtime_pb2

            raw = self._post("/api/GTFSRT/getAlerts", {"token": tok})
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(raw)
            out = []
            for entity in feed.entity:
                if not entity.HasField("alert"):
                    continue
                a = entity.alert
                def _txt(ts):
                    return ts.translation[0].text if ts.translation else ""
                out.append(
                    {
                        "header": _txt(a.header_text),
                        "description": _txt(a.description_text),
                    }
                )
            return out, None
        except Exception as e:  # noqa: BLE001
            return [], f"getAlerts failed: {e}"


nj_live = NjLive(NJT_BASE_URL, DATA_DIR)

# ---------------------------------------------------------------- PATH

PATH_STATIONS: dict[str, dict] = {
    "26722": {"name": "14th Street", "city": "New York"},
    "26723": {"name": "23rd Street", "city": "New York"},
    "26724": {"name": "33rd Street", "city": "New York"},
    "26725": {"name": "9th Street", "city": "New York"},
    "26726": {"name": "Christopher Street", "city": "New York"},
    "26727": {"name": "Exchange Place", "city": "Jersey City"},
    "26728": {"name": "Grove Street", "city": "Jersey City"},
    "26729": {"name": "Harrison", "city": "Harrison"},
    "26730": {"name": "Hoboken", "city": "Hoboken"},
    "26731": {"name": "Journal Square", "city": "Jersey City"},
    "26732": {"name": "Newport", "city": "Jersey City"},
    "26733": {"name": "Newark", "city": "Newark"},
    "26734": {"name": "World Trade Center", "city": "New York"},
}
PATH_ROUTES = {
    "859": "Hoboken – 33rd Street",
    "860": "Hoboken – World Trade Center",
    "861": "Journal Square – 33rd Street",
    "862": "Newark – World Trade Center",
    "1024": "Journal Square – 33rd Street (via Hoboken)",
}
_path_cache: dict = {"at": 0.0, "feed": None, "error": None}


def _path_feed():
    now = time.time()
    if _path_cache["feed"] is not None and now - _path_cache["at"] < 30:
        return _path_cache["feed"], _path_cache["error"]
    try:
        from google.transit import gtfs_realtime_pb2

        req = urllib.request.Request(
            PATH_RT_URL, headers={"User-Agent": "nj-transit-mcp/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(raw)
        _path_cache.update({"at": now, "feed": feed, "error": None})
        return feed, None
    except Exception as e:  # noqa: BLE001
        _path_cache["error"] = str(e)
        return _path_cache["feed"], str(e)


def _resolve_path(query: str):
    q = query.strip()
    if q in PATH_STATIONS:
        return PATH_STATIONS[q] | {"stop_id": q}, [], None
    matches = [
        {"stop_id": sid, **info}
        for sid, info in PATH_STATIONS.items()
        if q.lower() in info["name"].lower()
    ]
    if not matches:
        return None, [], f"No PATH station matches '{q}'."
    exact = [m for m in matches if m["name"].lower() == q.lower()]
    if exact:
        return exact[0], [], None
    if len(matches) == 1:
        return matches[0], [], None
    return None, matches, f"'{q}' matches {len(matches)} PATH stations; pick one."


# ---------------------------------------------------------------- helpers

def _nj_departures(stop_id: str, after: datetime, limit: int, line: str | None,
                   towards: str | None) -> tuple[list[dict], str, dict]:
    """Scheduled NJ departures at/after `after` (ET), with live overlay.
    Returns (departures, data_source, live_info). Handles service dates
    and trips that run past midnight (GTFS times >24:00)."""
    nj_feed.ensure()
    live = nj_live.trip_updates() if nj_live.configured else {}
    live_on = bool(live)
    out: list[dict] = []
    # Search today + yesterday's over-midnight trips + tomorrow early? The
    # practical window is: today's service, plus yesterday's service with
    # departure >= 24:00 (i.e. after midnight today).
    candidates: list[tuple[datetime, dict, dict]] = []
    for service_date, day_offset in (
        (after.date(), 0),
        (after.date() - timedelta(days=1), -1),
    ):
        key = service_date.strftime("%Y%m%d")
        active = nj_feed.services_by_date.get(key, set())
        if not active:
            continue
        for st in nj_feed.stop_times_by_stop.get(stop_id, []):
            trip = nj_feed.trips.get(st["trip_id"])
            if not trip or trip["service_id"] not in active:
                continue
            if int(st["stop_sequence"]) >= nj_feed.max_seq.get(st["trip_id"], 0):
                continue  # terminating arrival, not a departure
            dep_min = _parse_gtfs_time(st["departure_time"])
            if day_offset == -1 and dep_min < 24 * 60:
                continue
            dep_dt = datetime.combine(service_date, datetime.min.time(), tzinfo=ET) + timedelta(minutes=dep_min)
            if dep_dt < after:
                continue
            route = nj_feed.routes.get(trip["route_id"], {})
            if line and line.lower() not in (
                (route.get("route_short_name") or "").lower(),
                (route.get("route_long_name") or "").lower(),
            ):
                continue
            if towards and towards.lower() not in (trip.get("trip_headsign") or "").lower():
                continue
            candidates.append((dep_dt, st, trip))
    candidates.sort(key=lambda x: x[0])
    for dep_dt, st, trip in candidates[:limit]:
        route = nj_feed.routes.get(trip["route_id"], {})
        item = {
            "scheduled_departure": dep_dt.strftime("%Y-%m-%d %H:%M"),
            "departure_time": dep_dt.strftime("%H:%M"),
            "line": route.get("route_short_name") or trip["route_id"],
            "line_name": route.get("route_long_name"),
            "destination": trip.get("trip_headsign"),
            "trip_id": trip["trip_id"],
            "data_source": "scheduled",
        }
        overlay = live.get(trip["trip_id"], {}).get(stop_id)
        if overlay:
            item["data_source"] = "live_gtfs_rt"
            if overlay.get("time"):
                live_dt = datetime.fromtimestamp(overlay["time"], tz=ET)
                item["live_departure"] = live_dt.strftime("%Y-%m-%d %H:%M")
                item["live_time"] = live_dt.strftime("%H:%M")
            if overlay.get("delay_min") is not None:
                item["delay_min"] = overlay["delay_min"]
                item["status"] = (
                    "on time" if overlay["delay_min"] == 0
                    else f"{abs(overlay['delay_min'])} min late" if overlay["delay_min"] > 0
                    else f"{abs(overlay['delay_min'])} min early"
                )
            if overlay.get("status") in ("CANCELED", "SKIPPED"):
                item["status"] = overlay["status"].lower()
        out.append(item)
    source = "live_gtfs_rt" if live_on else "scheduled"
    return out, source, {"live_trips_in_feed": len(live), "error": nj_live.last_error}


# ---------------------------------------------------------------- tools

@mcp.tool
def search_stations(query: str, system: str = "all") -> dict:
    """Find stations by name. system: 'nj_transit', 'path', or 'all'.
    Returns station IDs to use with next_departures / trip_between."""
    nj_feed.ensure() if system in ("nj_transit", "all") else None
    results: list[dict] = []
    if system in ("nj_transit", "all"):
        for s in nj_feed.search(query):
            results.append({**s, "system": "nj_transit"})
    if system in ("path", "all"):
        q = query.strip().lower()
        for sid, info in PATH_STATIONS.items():
            if q in info["name"].lower():
                results.append(
                    {"stop_id": sid, "name": info["name"], "city": info["city"],
                     "system": "path"}
                )
    return {"query": query, "stations": results[:30]}


@mcp.tool
def next_departures(station: str, limit: int = 10, system: str = "nj_transit",
                    line: str | None = None, towards: str | None = None) -> dict:
    """Next departures from a station (name or stop ID).
    system 'nj_transit': rail + light rail, scheduled by default, live
    delays when NJT credentials are configured. system 'path': live PATH
    arrivals, no key needed. Times are America/New_York.
    NOTE: New Jersey Transit has no subway. For a subway-like ride use
    system='path', or NJ light rail lines HBLR / NLR / River Line."""
    limit = max(1, min(limit, 30))
    now = datetime.now(ET)
    if system == "path":
        stn, cands, err = _resolve_path(station)
        if err:
            return {"error": err, "candidates": cands}
        feed, ferr = _path_feed()
        if feed is None:
            return {"error": f"PATH live feed unavailable: {ferr}", "station": stn}
        arrivals = []
        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue
            tu = entity.trip_update
            for stu in tu.stop_time_update:
                if stu.stop_id != stn["stop_id"] or not stu.HasField("arrival"):
                    continue
                arr = datetime.fromtimestamp(stu.arrival.time, tz=ET)
                if arr < now - timedelta(minutes=1):
                    continue
                arrivals.append(
                    {
                        "arrival_time": arr.strftime("%H:%M"),
                        "minutes_away": max(0, round((arr - now).total_seconds() / 60)),
                        "line": PATH_ROUTES.get(tu.trip.route_id, tu.trip.route_id),
                        "route_id": tu.trip.route_id,
                        "data_source": "live_gtfs_rt",
                    }
                )
        arrivals.sort(key=lambda x: x["minutes_away"])
        return {
            "station": stn["name"],
            "system": "path",
            "as_of": now.strftime("%Y-%m-%d %H:%M %Z"),
            "data_source": "live_gtfs_rt",
            "departures": arrivals[:limit],
        }
    # nj_transit
    nj_feed.ensure()
    stn, cands, err = nj_feed.resolve(station)
    if err:
        return {"error": err, "candidates": cands}
    deps, source, live_info = _nj_departures(
        stn["stop_id"], now, limit, line, towards
    )
    return {
        "station": stn["stop_name"],
        "stop_id": stn["stop_id"],
        "system": "nj_transit",
        "as_of": now.strftime("%Y-%m-%d %H:%M %Z"),
        "data_source": source,
        "live_note": (
            "Live delays applied from NJ Transit GTFS-Realtime."
            if source == "live_gtfs_rt"
            else "Scheduled times from NJ Transit GTFS. "
                 "Set NJT_USERNAME/NJT_PASSWORD for live delays/cancellations."
        ),
        "live_info": live_info if nj_live.configured else None,
        "departures": deps,
    }


@mcp.tool
def trip_between(origin: str, destination: str, limit: int = 5) -> dict:
    """Next NJ Transit trains from origin to destination on the SAME trip
    (no transfers). Returns departure, arrival and duration. Times ET."""
    nj_feed.ensure()
    o, oc, oerr = nj_feed.resolve(origin)
    if oerr:
        return {"error": oerr, "candidates": oc}
    d, dc, derr = nj_feed.resolve(destination)
    if derr:
        return {"error": derr, "candidates": dc}
    now = datetime.now(ET)
    live = nj_live.trip_updates() if nj_live.configured else {}
    by_trip_o = {}
    for st in nj_feed.stop_times_by_stop.get(o["stop_id"], []):
        by_trip_o[st["trip_id"]] = st
    results = []
    for service_date, day_offset in (
        (now.date(), 0),
        (now.date() - timedelta(days=1), -1),
    ):
        key = service_date.strftime("%Y%m%d")
        active = nj_feed.services_by_date.get(key, set())
        for st in nj_feed.stop_times_by_stop.get(d["stop_id"], []):
            trip = nj_feed.trips.get(st["trip_id"])
            if not trip or trip["service_id"] not in active:
                continue
            ost = by_trip_o.get(st["trip_id"])
            if not ost or int(ost["stop_sequence"]) >= int(st["stop_sequence"]):
                continue
            dep_min = _parse_gtfs_time(ost["departure_time"])
            arr_min = _parse_gtfs_time(st["arrival_time"])
            if day_offset == -1 and dep_min < 24 * 60:
                continue
            base = datetime.combine(service_date, datetime.min.time(), tzinfo=ET)
            dep_dt, arr_dt = base + timedelta(minutes=dep_min), base + timedelta(minutes=arr_min)
            if dep_dt < now:
                continue
            route = nj_feed.routes.get(trip["route_id"], {})
            item = {
                "departure": dep_dt.strftime("%H:%M"),
                "arrival": arr_dt.strftime("%H:%M"),
                "duration_min": int((arr_dt - dep_dt).total_seconds() // 60),
                "line": route.get("route_short_name"),
                "line_name": route.get("route_long_name"),
                "destination": trip.get("trip_headsign"),
                "trip_id": trip["trip_id"],
                "data_source": "scheduled",
            }
            overlay = live.get(trip["trip_id"], {}).get(o["stop_id"])
            if overlay and overlay.get("delay_min") is not None:
                item["data_source"] = "live_gtfs_rt"
                item["delay_min"] = overlay["delay_min"]
            results.append((dep_dt, item))
    results.sort(key=lambda x: x[0])
    return {
        "origin": o["stop_name"],
        "destination": d["stop_name"],
        "as_of": now.strftime("%Y-%m-%d %H:%M %Z"),
        "trips": [r[1] for r in results[: max(1, min(limit, 10))]],
    }


@mcp.tool
def lines(system: str = "all") -> dict:
    """All lines/routes. NJ Transit: 17 rail + light-rail routes.
    PATH: 4 daytime routes."""
    out = []
    if system in ("nj_transit", "all"):
        nj_feed.ensure()
        for r in sorted(nj_feed.routes.values(), key=lambda x: x["route_short_name"]):
            out.append(
                {
                    "system": "nj_transit",
                    "route_id": r["route_id"],
                    "short_name": r["route_short_name"],
                    "name": r["route_long_name"],
                }
            )
    if system in ("path", "all"):
        for rid, name in sorted(PATH_ROUTES.items()):
            out.append({"system": "path", "route_id": rid, "name": name})
    return {"lines": out}


@mcp.tool
def service_alerts(system: str = "all") -> dict:
    """Service alerts. NJ Transit alerts require NJT credentials (see
    server_status for how to enable); PATH has no separate alerts feed
    in v1, so PATH alerts are reported as unavailable, not invented."""
    alerts: list[dict] = []
    notes: list[str] = []
    if system in ("nj_transit", "all"):
        nj_alerts, note = nj_live.alerts()
        alerts += [{"system": "nj_transit", **a} for a in nj_alerts]
        if note:
            notes.append(note)
    if system in ("path", "all"):
        notes.append("PATH alerts: not available in v1 (no alerts feed in the community GTFS-Realtime source).")
    return {"alerts": alerts, "notes": notes}


@mcp.tool
def server_status() -> dict:
    """What data this server is actually serving right now: GTFS date
    range and counts, whether live NJ data is enabled, PATH feed age.
    Use this to answer 'is this live or scheduled?' honestly."""
    try:
        nj_feed.ensure()
        gtfs_info = {
            "date_range": nj_feed.date_range,
            "stops": len(nj_feed.stops),
            "routes": len(nj_feed.routes),
            "trips": len(nj_feed.trips),
            "loaded": bool(nj_feed.loaded_at),
        }
    except Exception as e:  # noqa: BLE001
        gtfs_info = {"error": str(e)}
    feed, ferr = _path_feed()
    return {
        "server": "nj-transit",
        "version": "1.1",
        "timezone": "America/New_York",
        "now": datetime.now(ET).strftime("%Y-%m-%d %H:%M %Z"),
        "nj_transit_gtfs": gtfs_info,
        "nj_live_enabled": nj_live.configured,
        "nj_live_note": (
            "Configured: delays/cancellations overlay GTFS-Realtime."
            if nj_live.configured
            else "Not configured: NJ departures are SCHEDULED only. "
                 "Register free at https://developer.njtransit.com/registration, "
                 "then set NJT_USERNAME and NJT_PASSWORD."
        ),
        "nj_live_last_error": nj_live.last_error,
        "path_live": {
            "available": feed is not None,
            "entities": len(feed.entity) if feed else 0,
            "feed_time": (
                datetime.fromtimestamp(feed.header.timestamp, tz=ET).strftime("%Y-%m-%d %H:%M:%S %Z")
                if feed else None
            ),
            "error": ferr,
        },
    }


# ------------------------------------------------------------ HTTP auth
# Public MCP endpoints must not be open to anyone: when
# MCP_BEARER_TOKEN is set, /mcp requires `Authorization: Bearer <token>`.
# When it is unset (local stdio / local testing), HTTP is unauthenticated
# — do NOT expose that publicly. /health is always public and unauthenticated.

class BearerAuthMiddleware:
    """Pure ASGI middleware, so it works under uvicorn and under Vercel."""

    def __init__(self, app, token: str | None = None):
        self.app = app
        self.token = token or os.environ.get("MCP_BEARER_TOKEN")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.token:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in ("/", "/health"):
            body = b'{"status":"ok","server":"nj-transit"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        if path.startswith("/mcp"):
            headers = {
                k.decode("latin-1").lower(): v.decode("latin-1")
                for k, v in scope.get("headers", [])
            }
            if headers.get("authorization") == f"Bearer {self.token}":
                await self.app(scope, receive, send)
                return
            body = b'{"error":"unauthorized"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_http_app():
    """Stateless Streamable HTTP app: safe for serverless (Vercel) and
    for long-lived hosts (Railway). No MCP session is kept server-side."""
    from starlette.middleware import Middleware

    return mcp.http_app(
        path="/mcp",
        stateless_http=True,
        middleware=[Middleware(BearerAuthMiddleware)],
    )


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    # Railway / Render / Fly set PORT; MCP_PORT overrides it locally.
    port = int(os.environ.get("MCP_PORT") or os.environ.get("PORT") or 8000)
    i = 0
    while i < len(args):
        if args[i] == "--transport" and i + 1 < len(args):
            transport, i = args[i + 1], i + 2
        elif args[i] == "--port" and i + 1 < len(args):
            port, i = int(args[i + 1]), i + 2
        else:
            i += 1
    if transport == "http":
        try:
            import uvicorn

            uvicorn.run(build_http_app(), host="0.0.0.0", port=port)
        except ImportError:
            mcp.run(transport="http", host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
