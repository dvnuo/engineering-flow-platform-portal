---
title: Building apps
summary: From a GitHub repository to an app at its own address, and what looking after it involves.
group: Working
icon: hammer
order: 23
---
## What you publish

An app is one directory of a GitHub repository with a manifest,
`efp-app.json`, in it. The Portal reads the directory with the platform's
read-only service account and hands it to the app host, which installs its
requirements, starts it and serves it under an address of its own. An app is
a Python web app (`fastapi`, `flask`, `streamlit`, `gradio`, or any `command`
that serves HTTP) or a `static` site.

This page is the Portal's side: registering, deploying and looking after an
app. The Python SDK, each framework's details and a longer troubleshooting
list are in the developer guide, `docs/DEVELOPING_APPS.md` of the
`engineering-flow-platform-app` repository. The same repository holds the
SDK reference (`docs/SDK_REFERENCE.md`), a guide for turning an existing
project into an app (`docs/CONVERTING_A_PROJECT.md`), example apps to copy
(`examples/`) and `AGENTS.md`, the same rules written as a brief for an AI
agent doing the work.

## Start from a starter

```
pip install efp-app-sdk
efp-app init fastapi my-app
cd my-app
efp-app dev
```

`efp-app init` writes a working app with its manifest; `efp-app dev` runs it
on your machine the way the host does. Push the directory to a repository
the platform can read, and it is ready to register.

## The manifest

```json
{
  "name": "Mind map",
  "description": "Draw and share mind maps.",
  "icon": "icon.png",
  "type": "fastapi",
  "entry": "main:app",
  "requirements": "requirements.txt",
  "health_path": "/"
}
```

| Field | Meaning |
|---|---|
| `name` | required; what the app is called until you rename it when registering |
| `description` | one or two sentences for the app's card |
| `icon` | a PNG, JPEG, GIF or WebP file in the directory |
| `type` | `fastapi`, `flask`, `streamlit`, `gradio`, `command` or `static` |
| `entry` | `main:app` for FastAPI and Flask, the script for Streamlit and Gradio, the directory for a static site |
| `requirements` | the requirements file, `requirements.txt` unless you say otherwise |
| `health_path` | the path the host asks to see that the app answers, `/` by default |
| `owners` | optional; the Portal usernames that may publish this directory |

Leave `owners` out and any member may register the directory as an app of
their own. List usernames there, as in `"owners": ["alice", "bob"]`, and only
those members may register and deploy it. The Portal reads every repository
with one service account, which may reach repositories that some members
cannot open on GitHub, so list `owners` when not everyone on the Portal
should run this code.

## Registering: four fields

**Apps > New app** asks for four things: the repository, a branch or tag,
the entry directory (the folder that holds `efp-app.json`; empty for the top
of the repository) and a name. As soon as the first three are filled in, the
Portal reads the repository and shows what it found: the app's kind, its name
and the address it will get. When something is wrong it says what and how to
fix it:

- **The Portal cannot read this repository.** It names the service account;
  add that account to the repository with the Read role.
- **efp-app.json does not name you.** The manifest lists who may publish it
  under `owners` and you are not there. Add your username, commit and push.
- **efp-app.json needs fixing.** Each broken rule is listed.

**Create and deploy** registers the app and starts its first deployment. The
address is made from the name, never changes afterwards and is never given to
another app.

## Deploying and rolling back

A deployment takes the newest commit of the branch or tag, reads its manifest
and sends the code to the host, which unpacks it, installs the dependencies,
starts the app, checks that it answers and only then switches to it. The
app's page shows each step as it happens, with the log; when the dependencies
cannot be installed, the log carries pip's own message. A deployment that
fails leaves the release that was running untouched.

- **Overview** says how far the running release is behind its branch and
  offers **Deploy latest**.
- **Deployments** can deploy another branch or tag, which the app follows
  from then on, and keeps the history.
- **Roll back** returns to the release that ran before the current one.

Run `efp-app check` before you deploy. An app is served under `/<name>/`, so
a URL that starts with `/` in a page, a script or a style sheet points
outside the app; the check finds those, which do not show up when the app
runs at the top of your own machine. To watch them happen, run the app the
way it is served here: `efp-app dev --prefix <name>` serves it under
`/<name>/` and prints every request that leaves the app.

## The token and local development

Every app has one token. The deployed app receives it by itself. To run the
app on your own machine with the real model provider and connectors, copy
the token from **Settings > App token** into `.env`:

```
EFP_APP_TOKEN=efpa_...
EFP_PORTAL_URL=https://<this Portal's address>
```

Then start it with `efp-app dev`. Calls made locally are real: they use your
model provider, count against the app's daily limit and reach the real
systems behind your connectors.

Keep `.env` out of the repository. The token works from anywhere the Portal
can be reached, and the page shows when it was last used and whether that was
from inside the cluster or from outside. If it leaks, **Reset token**: the old
one stops working at once and the app restarts with the new one.

## Who may open it

| Audience | Who gets in |
|---|---|
| Only the owner | you; the default |
| The owner's group | every member of your group |
| Every member | everyone who can sign in to the Portal |
| Anyone | no sign-in at all |

**Anyone** is offered only where an administrator switched it on, and takes
effect once an administrator approved the app; until then every member can
open it.

## What the app may use of yours

An app's code can call your model provider and your connectors through the
SDK. Both are off until you turn them on under **Settings**, and each asks
you first:

- **Model provider.** *Visitors' use of this app consumes your Copilot and
  may slow down your own chats.* Every call runs on your own model provider,
  whoever the visitor is, up to a daily limit of credits and tokens. Once the
  limit is reached, calls are refused until the next day (UTC).
- **Connectors** (Jira, Confluence, GitHub, Jenkins, Nexus, Splunk).
  *Visitors will read these systems with your connector's account.* A
  connector is read-only unless you also allow writing.
- **A wider audience** while either is on reminds you of the same, because
  more people will then spend and read on your behalf.

## Looking after it

- **Environment variables**: settings and secrets your code reads from its
  environment. Names may not start with `EFP_`, nor be one the system reads
  to load programs (`LD_...`, `GCONV_PATH`, `GLIBC_TUNABLES`); values are
  hidden until you reveal them; saving restarts the app.
- **Logs**: what the app prints, followed live. Pause to read, download to
  keep.
- **Usage**: requests, visitors, errors and response time; model calls and
  credits against the daily limit; connector calls; who visits most; memory
  and data size.
- **Data**: how much the app saved on the host. It survives restarts and
  deployments. **Clear data** removes it for good.
- **Activity**: who deployed, changed, reset or took down what, and what the
  host reported.

## Templates

Offer an app as a template under **Settings > Template**, then publish a tag
of the repository as a version. Other members start their own app from a
version: they get that version's code, never your token, variables or data.
Their app stays on the version they picked; when you publish a newer one they
are offered the upgrade and decide when to take it.

To change the code of an app you did not write, fork its repository on
GitHub with your own account (the link is under **Settings > Code**) and
register your fork as a new app.

## Limits

- One Python version, chosen by the platform. Dependencies are installed as
  wheels from the platform's package index; there are no system packages.
- One process per app, with a memory ceiling. An app that goes over it is
  stopped and the reason is shown.
- An app is stopped after a while without visitors and started again by the
  next visit. Do the work in request handlers and keep state in storage: no
  background jobs, no schedulers, nothing important in memory.
- The code directory is read-only. What must survive goes through the SDK's
  storage, which has a quota.
- All apps share one address, so a browser's local storage is shared between
  them: keep nothing confidential there. Camera, microphone, location and
  screen capture are switched off.

## When something fails

- **Install the dependencies** fails: a package has no wheel for the host's
  Python, or is not in the platform's index. Pin a version that has one.
- **Check that it answers** fails: the app must listen on `127.0.0.1` and
  the port in `PORT`, and answer its `health_path` in time.
- **Works locally, no styles or 404s when deployed**: a URL starts with `/`.
  Run `efp-app check`.
- **Crashed** or **Stopped by the host**: read **Logs**; for memory, look at
  **Usage**.

Visitors' side of all this is in [Using apps](#/help/using-apps).
