---
icon: clipboard-check
---
## What this is for

Running mobile tests on BrowserStack real devices: the assistant starts
sessions, holds a device while you record test steps, runs scenarios in
parallel, and keeps each run's video and screenshots. See
[Mobile scenario testing](#/help/mobile-scenario-testing) for the whole flow.

Only needed if you work on mobile testing.

## Settings

- **Username** and **Access Key** come from your BrowserStack account
  settings. They are yours; runs count against your team's parallel sessions.
- **Test BrowserStack** signs in and shows how many parallel sessions are in
  use. A run with more scenarios than free sessions waits for them.
- **Advanced** holds what most teams leave alone: the default platform, the
  network (public, or a private network through BrowserStack Local), the idle
  timeout (BrowserStack allows at most 300 seconds), session video, and the hub
  and API addresses when your company routes BrowserStack through its own
  gateway.

## App packages

Builds you upload here go straight to BrowserStack; Portal keeps only the
reference. Upload a file, or give the URL of a Jenkins or Nexus artifact (it is
fetched with the credentials in your Jenkins or Nexus connector); any other
https URL is handed to BrowserStack to download. Give every build of an app the
same custom id so tests always pick the newest one. BrowserStack deletes
uploads after 30 days; an expiring package is marked in the list.

## Private networks

If the app talks to internal test servers, the device needs BrowserStack Local.
Choose *Private: the assistant runs BrowserStack Local* when your assistants
can reach those servers, or *a tunnel your team runs* when a long-running
tunnel already exists; ask your platform team which one applies.
