# Terms of Use — NJ Transit MCP Server (unofficial)

This is an unofficial, independent service. It is not affiliated with,
endorsed by, or operated by NJ Transit, the Port Authority of New York
and New Jersey, or PATH.

## What it does
It answers schedule questions from public data: NJ Transit's public
GTFS files (rail/light rail and bus, scheduled times) and the community
PATH GTFS-Realtime feed (live arrivals). NJ Transit live delays and
cancellations are only shown when that feature is enabled; otherwise
departures are explicitly labelled "scheduled", never "live".

## Free use
The public hosted endpoint is free to use, rate-limited per IP
(currently 60 requests per minute per server instance; the server may
return 429 with a retry time if you exceed it). There is no account,
no payment, and no guarantee of uptime or accuracy — always check the
official NJ Transit / PATH sources before travelling, especially for
same-day trips, delays, and cancellations.

## Acceptable use
Do not abuse the endpoint (automated hammering, bypassing rate limits,
or reselling the service as if it were official). All six tools are
read-only; the server cannot book, buy, cancel, or change anything.

## No warranty
Schedules, live arrivals, and alerts are provided as-is, from
third-party feeds that can be delayed, incomplete, or wrong. The
operator is not liable for missed trains, buses, or connections.

## Changes
These terms, the endpoint URL, and the rate limits may change. The
current version of this file in the public repository is the current
version of the terms.
