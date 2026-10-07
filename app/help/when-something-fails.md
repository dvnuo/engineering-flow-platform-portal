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
