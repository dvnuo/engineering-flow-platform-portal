---
icon: clipboard-check
---
## What this is for

Recording mobile test steps on a BrowserStack real device from your computer.
Portal and the assistants never connect to BrowserStack themselves: recordings
go through the local bridge on your computer, and test runs through your
team's Jenkins pipeline. See
[Mobile scenario testing](#/help/mobile-scenario-testing) for the whole flow.

Only needed if you record mobile tests.

## Settings

- **Username** and **Access Key** come from your BrowserStack account
  settings. The Recording panel hands them to the local bridge on your
  computer for each recording; the bridge keeps them in memory only.
- **Test BrowserStack** signs in from your computer through the local bridge
  and shows how many parallel sessions are in use. Start the bridge first.
- **Advanced** holds what most teams leave alone for the devices you record
  on: the default platform, the network (public, or a private network through
  BrowserStack Local), the idle timeout (BrowserStack allows at most 300
  seconds), session video, and the hub and API addresses when your company
  routes BrowserStack through its own gateway. Test runs use the pipeline's
  own settings.

## The local bridge

The program of **Connectors > Local bridge**, `efp-bridge`, records mobile
tests: its package carries `mobile-auto`. It starts the device, holds it while you
record, and passes Appium Inspector's commands to it, keeping a log of what
you did. Install it once from that page; the BrowserStack page shows whether
it is running. Your computer needs to reach BrowserStack. Behind a proxy the
bridge uses your computer's own proxy settings (a static proxy, a PAC script,
or auto-detection, the way the browser does), or set one on the BrowserStack
connector page or under *Network from this computer* in the Recording panel.
A proxy that asks for a login takes it in the *Proxy user name* and *Proxy
password* fields below the address (a domain user as `DOMAIN\user`). The
password field hides what you type, so sharing your screen does not show it.
The login stays in this browser on this computer and goes only to the local
bridge, which hands it to mobile-auto and BrowserStack Local there. A login
saved inside the address before these fields existed moves into them.
If **Test BrowserStack** is answered by your company's web filter block page,
check the same address in the browser: a BrowserStack sign-in prompt means the
bridge only needs the browser's proxy; the same block page means the network
must allow `api-cloud.browserstack.com` and `hub-cloud.browserstack.com`.

## Builds

The Recording panel lists the builds in your BrowserStack account and uploads
new ones through the bridge. Give every build of an app the same custom id,
for example `fxapp-android-uat`, so recordings and the pipeline always pick
the newest one. BrowserStack deletes uploads after 30 days.

## Test accounts' passwords

A recording never stores what you type into a password field: it refers to
each one by a name such as `MOBILE_SECRET_PASSWORD`, and the assistant tells
you which names it needs. The values live in Jenkins, as credentials of the
mobile scenario pipeline, not in Portal; ask whoever runs your Jenkins to add
them.

## Private networks

**Network** follows your deployment's default (the first option says which)
unless you choose otherwise; *Public* is right unless the app talks to
internal test servers that BrowserStack's devices cannot reach. Then the device needs BrowserStack
Local: choose *Private: start BrowserStack Local with each session* when the
computer you record on can reach those servers (the BrowserStackLocal program
must be installed on it; the bridge hands it your proxy), or *a tunnel your
team runs* when a long-running tunnel already exists; ask your platform team
which one applies. A private network makes every recording start a tunnel
first, so a recording that fails with "BrowserStack Local readiness" on an
app that does not need one means Network should go back to Public.
