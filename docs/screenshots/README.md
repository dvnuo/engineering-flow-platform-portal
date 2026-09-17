# Screenshot Provenance

These PNGs accompany the [Beginner's Guide](../BEGINNER_GUIDE.md). They are actual browser captures of Portal, not generated mockups.

- Source revision: `021baaafdc3f2406540b467596c6ba961ad821d5` (`master` when fetched on September 17, 2026).
- Capture date: September 17, 2026.
- Browser: local Microsoft Edge, controlled with Playwright in headless mode.
- Viewport: 1440 × 1000 CSS pixels; device scale factor 1; light theme; English locale.
- Application: the unmodified Portal application served on `http://127.0.0.1:8766`.
- Data: an isolated SQLite database, migrated from empty through `20260913_0035`; a fictional `guide-admin` account; the standard seeded assistant types; one demonstration assistant.
- Runtime: `K8S_ENABLED=false`. Delegation, reconciliation, and idle-stop workers were disabled. No real model, external service credential, Kubernetes workload, browser bridge, or successful task execution was used.
- Editing: screenshots were saved directly by the browser. No application UI or service responses were replaced with invented results.

The local no-op Kubernetes path can report an assistant as **Ready/Running** without a reachable runtime. Those labels in the screenshots illustrate the layout, not a verified operational deployment. Empty lists, absent model authorization, unavailable runtime details, and a bridge that is not detected are intentional and explained in the guide. No task or delegation was submitted for execution during capture.

## Image map

| File | Screen and capture state |
| --- | --- |
| `01-sign-in.png` | `/login`, GitHub Copilot sign-in offered |
| `02-workspace.png` | First workspace before an assistant exists |
| `03-create-assistant.png` | Simple creation dialog with a demonstration name |
| `04-advanced-setup.png` | Advanced creation, Engine step |
| `05-chat-workspace.png` | Selected demonstration assistant with an unsent example prompt, no successful runtime reply |
| `06-assistant-details.png` | Details and lifecycle controls |
| `07-connections.png` | Default profile, setup progress and profile information |
| `08-model-connection.png` | The same profile scrolled to the model section |
| `09-tasks.png` | Empty Tasks overview |
| `10-create-task.png` | Create Task dialog, Agent step; not submitted |
| `11-delegations.png` | Empty Delegations overview |
| `12-create-delegation.png` | Create Delegation dialog, Basics step; not submitted |
| `13-browser-connector.png` | Local browser setup and bridge-not-detected status |
| `14-user-management.png` | Demonstration administrator and allowlist |
| `15-assistant-types.png` | Seeded Business, Dev, and Ops types |
| `16-default-connections.png` | Administrator's default-connection template |
| `17-help.png` | Getting-started help topic |
| `18-browser-enable.png` | Local browser test and enable controls |
| `19-timer-schedule.png` | Timer prompt and schedule preview; rule not submitted |

## Recreate the captures

1. Follow the [local setup instructions](../OPERATIONS_GUIDE.md) using a separate database and fictional account. Never point a screenshot walkthrough at a production database.
2. Run migrations, disable the three background workers, keep Kubernetes disabled, and start Portal bound to loopback. Enable Copilot login to match image 1, then use `/admlogin` to enter with the local bootstrap account. Do not authorize a real Copilot account for this walkthrough.
3. Set the browser viewport and theme as above. Capture the empty workspace before creating the example assistant. Dismiss the welcome tour and use the ordinary UI to open each screen in the table.
4. Use a seeded Dev Assistant type for the demonstration assistant. Do not send a chat, task, or external service test expecting it to work without a runtime.
5. For image 19, select Timer, use `0 9 * * 1-5` and `Asia/Shanghai`, leave the rule disabled, and inspect the schedule preview without submitting it.
6. Wait for each panel's real content to load, move the pointer away from controls so tooltips disappear, and save the viewport as PNG. Visually inspect each image for legibility and accidental personal information.
7. Update the source revision, date, guide captions, and image map when refreshing images after UI changes.

Successful chats, populated runtime files/sessions, model authorization, task results, and an active bridge need a separately configured runtime and test accounts. Capture those in a controlled environment if future documentation adds such examples; do not relabel this local walkthrough as a live-service test.
