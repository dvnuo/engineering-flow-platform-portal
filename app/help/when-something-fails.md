---
title: When something fails
summary: Reading a failure and knowing whose problem it is.
group: Working
icon: triangle-alert
order: 20
---
## Temporary provider failures

The model provider occasionally fails for a moment. These are retried
automatically; if one still reaches you, sending the message again usually
works.

## Credential failures

A run that fails immediately with a credentials error means a token expired or
was revoked. Open [Connectors](#/help/connect-llm) and reconnect that service.

## A mobile scenario failed

Each failed row of a scenario matrix is sorted into one of four causes:
**script drift** (the app changed and the test needs updating; the assistant
proposes the fix), **product defect** (the app is wrong; a defect is filed with
the evidence), **infrastructure** (the device, BrowserStack, or the network;
rerun it), or **test data** (the account, the build, or the environment). Open
the row's evidence for the video and the screenshot at the failure. A
*drift* marker on a passed row means a step only worked through a fallback
locator: fix it before it fails.

## An assistant that will not start

If it reports that connection settings are not ready, a connector needs
filling in. Anything mentioning images or capacity is a platform problem —
contact your administrator.

## A connector Test fails on the certificate

**Test** on a connector card probes the service from the Portal itself, while
the assistant reaches it from its own runtime. A message starting with *TLS
certificate verification failed* means the service, or the proxy in front of
it, presents a certificate from a company CA that the Portal does not trust
yet; the assistant usually trusts it already, so your chats keep working. Your
credentials are not the problem. Tell your administrator the host named in the
message: they add the CA to the Portal (`PORTAL_CA_BUNDLE`) and Test agrees
with the assistant again.

## Administrators: the Errors panel

**Administration > Errors** collects what failed across the platform: runs
that ended failed or stale, assistants whose runtime would not start,
connector tests that failed, Portal errors and warnings from the Portal's own
log. The same problem is grouped once, with how many times it happened, how
many members and assistants it touched and when it was last seen. Open a row
for the full message, its context (member, assistant, runtime, model, session,
task, trace id) and the traceback where there is one, and copy it into a
ticket. Filter by period, source or text, or export the period as CSV.
Messages are redacted before they are stored and kept for 90 days by default.
