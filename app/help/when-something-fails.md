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
was revoked. Open [Connections](#/help/connect-llm) and reconnect that service.

## An assistant that will not start

If it reports that connection settings are not ready, its Connections need
filling in. Anything mentioning images or capacity is a platform problem —
contact your administrator.
