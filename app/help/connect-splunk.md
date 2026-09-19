---
icon: scroll-text
---
## What this is for

Searching your logs while the assistant troubleshoots: errors for a service
in the last hour, a request id across components, how often something
happened. It runs searches through the `splunk` CLI and always with a time
range and a result count, so a careless query cannot pull a whole index.

It reads search results; it does not create alerts, saved searches or
dashboards, and it never writes to an index.

## What to enter

- **URL** is the management API, not the web page: the port is usually
  `8089`, for example `https://splunk-api.example.com:8089`.
- **Token** is an authentication token created in Splunk under Settings,
  Tokens. If your Splunk does not issue tokens, a username and password work
  instead.
- **Default index** is searched when a query does not name one, for example
  `app_prod`. **Default earliest** is the time range applied when the
  request gives none, in Splunk's relative form such as `-1h` or `-24h`.
  **Max results** caps how many events a search may return (1 to 10000).
- **Name** is how the assistant addresses this instance with `--instance`.

## If it stops working

A certificate error on the management port is common: it often carries a
self-signed certificate. Ask your Splunk administrator for the address that
presents a trusted one. A search that returns nothing usually has too narrow a
time range or the wrong index; the assistant reports both with the result.
