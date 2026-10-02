---
title: Mobile scenario testing
summary: From a Jira requirement to one video and one set of screenshots per scenario.
group: Working
icon: smartphone
order: 15
---
## How it fits together

1. A business analyst finishes a requirement in Jira and moves it to the status
   your team agreed on, for example **Ready for Test**.
2. A delegation that starts on that status change has the assistant draft the
   scenarios: grouped Scenario Outlines with Examples tables, positive and
   negative, each Examples row with its expected results.
3. You approve the scenarios on the task page (a review card: tick the ones to
   keep, say what to change).
4. You record a scenario in one go on a real BrowserStack device from your
   computer, once per platform. The assistant proposes how to split the
   recording into reusable segments (log in, skip the introduction, choose a
   currency), and you confirm the split. You then replay the segments on the
   same device and watch them work before any script is generated.
5. The assistant turns the approved scenarios and your segments into a plain
   Python test project (the Gherkin you approved, its step definitions, and
   the segments as functions, each recorded line marked `# recorded:`),
   commits it to your team's test repository, and has the Jenkins pipeline
   run one row of each scenario on a single device. You approve them.
6. The full run goes through the same pipeline, in parallel, and publishes a
   Cucumber report on the build page. Each scenario ends with a result, its
   screenshots, and a video of the session; failures are sorted into script
   drift, product defects, infrastructure, or test data. The tests run
   without EFP: anyone with the repository and a BrowserStack account can run
   them.

## Where things run

| What | Where |
| --- | --- |
| Recording scenarios | Your computer: the Mobile testing panel drives the local bridge, which drives the BrowserStack device |
| Compiling segments, writing and exporting the tests, triage | The assistant, on files only |
| Dry runs, full runs, reruns | Your team's Jenkins pipeline, running the Python project with behave and the Appium client |

Portal and the assistants never connect to BrowserStack.

## Before you start

- **A QA Assistant**: create your assistant with the *QA Assistant* type. It
  carries the skills and instructions this flow uses; other types do not.
- **Connectors > BrowserStack**: your username and access key, turned on.
- **Connectors > Local bridge**: install the local bridge (`efp-bridge`) on
  your computer once; recordings run through it. *Test BrowserStack* on the BrowserStack
  page checks that your computer reaches BrowserStack.
- **Connectors > Jenkins** and **GitHub**: the assistant starts the mobile
  scenario pipeline with your Jenkins connector and commits the tests to your
  team's test repository with GitHub. Your Jenkins administrator sets the
  pipeline up once, with the test accounts' passwords as Jenkins credentials.
- **Jira**: the delegation needs your Jira connector. Ask your BA to fill in
  the expected results in each Examples row; a row without them cannot be
  checked, and the assistant flags it instead of inventing a value.

## Recording a scenario

Open a chat with the assistant and choose **Mobile testing** in the tool bar.
The panel has three parts: the device at the top, a progress strip showing
which step comes next, and the step itself. Settings (the local bridge, the
proxy, uploading a build) sit behind the gear icon.

1. If the panel says the local bridge is not running, press **Start bridge**.
2. Pick a build, the platform, and optionally a device, then **Start and
   open Inspector**. The bridge starts the device and holds it; the panel
   shows what it is doing, usually for about a minute, longer when all your
   parallel sessions are busy. The Inspector opens in its own tab and attaches
   to the device as soon as it is ready; nothing to type in. (When your Portal
   does not host the Inspector, the button reads **Start recording device**
   and the device menu shows the connection details for the desktop Appium
   Inspector.) A reload of the page picks the start up again; **Cancel**
   releases the device once it is up.
3. Record in the Inspector: select elements and use its Tap and Send Keys
   buttons. Tap elements rather than points on the screenshot: an element
   keeps working when the layout moves, a point does not. Only what goes
   through the Inspector is recorded; the live device view on BrowserStack's
   own site is not seen by the bridge. The device menu (the three dots on the
   device card) also opens the Inspector inside Portal, next to the panel.
4. Type real values, including passwords. Password fields are never stored;
   the assistant names each one (for example `MOBILE_SECRET_PASSWORD`) for the
   pipeline's credentials. The other values become parameters for your test
   data.
5. Go through the whole scenario, then press **Save recording**. The
   recording goes into the assistant's workspace. The assistant lays it out by
   the screens it went through and proposes where to split it into segments,
   on a review card in the chat: approve the split or say what to change. It
   then compiles each segment and tells you how robust it is. The progress
   strip moves on to Replay once the segments exist. (If you listed segment
   names before starting, save after each one instead; each is compiled as it
   is.)
6. Replay the compiled segments under **Replay** before any script is
   generated from them. Tick the segments (those of the split you just
   approved are ticked), choose where to start (restart the app, clear its
   data first on Android, or the screen the device shows), type the values of
   the passwords they use, and press **Replay**. The replay borrows the device
   from the Inspector and hands it back when it ends; meanwhile the Inspector
   can only watch. The result goes into the assistant's workspace and the
   chat, where a card shows each segment's result with a screenshot after it,
   or the step that failed and the screen it stopped on. The assistant reviews
   it: it fixes a failed step and asks you to replay again. Scripts are
   generated once every segment passes. The passwords stay in the page and the
   local bridge; the assistant never sees them.
7. To record another scenario on the same device, carry on and save again.
   **Finish recording** in the device menu releases the device.

The device is held for 30 minutes at a time; **Hold 30 more minutes** extends
it. Keep the bridge running while you record: BrowserStack ends a session that
sees no command for five minutes.

## The Library

The **Library** tab under the device card lists what this assistant's
workspace keeps for mobile testing. It needs no device.

- **Segments**, per platform: the steps, the grade and how many steps still
  need a look (what the compiler wrote into the file), when it was compiled,
  and whether a replay passed it. **Replay** ticks the segment under Session
  (and starts there if a device is held); **View** shows the segment's steps
  as the assistant wrote them; **Delete** removes it from the workspace and tells the assistant,
  which marks it to be recorded again in every scenario that uses it.
- **Recordings**: when each was saved, its size, and whether its split was
  proposed and compiled. **Split** asks the assistant to propose the split
  again; **Delete** removes the recording and its split file (segments already
  compiled from it stay).
- **Replays**: each result with the segments it ran. **Details** shows the same
  card as the chat, with the screenshots; **Delete** removes the report and its
  files without a word to the assistant.

Deleting asks first. The assistant hears `Segment <name> deleted: <path>` or
`Recording <name> deleted: <path>` and keeps the scenario plan in step.

## What a recording grade means

| Grade | Meaning |
| --- | --- |
| good | Every step targets an element by an id or its text |
| fair | Some steps need a look: a coordinate tap, an element picked by position |
| poor | Most steps depend on screen positions; ask the app developers to add accessibility ids or resource ids, then record again |

## Reading the results

Each scenario run has an evidence card: its result, the device, the
BrowserStack session, a video, and screenshots at the checks. The task page of
a run shows the scenario matrix while the pipeline runs, and the triage of
every failure afterwards, with links to the defect or the fix.
