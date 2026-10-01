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
   currency), and you confirm the split.
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
| Recording scenarios | Your computer: the Recording panel drives the local bridge, which drives the BrowserStack device |
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

Open a chat with the assistant and choose **Recording** in the tool bar.

1. If the panel says the local bridge is not running, press **Start bridge**.
2. Pick a build (or upload one), the platform, and optionally a device, then
   **Start recording device**. Leave *Segment names* empty unless you want to
   save each part yourself under names you choose. The bridge
   starts the device and holds it; the panel shows what it is doing and says
   when the device is ready, usually within a minute, longer when all your
   parallel sessions are busy. A reload of the page picks the start up again;
   **Cancel** releases the device once it is up.
3. **Open Inspector** opens Appium Inspector already attached to that device
   (when your Portal hosts it). Otherwise attach an Inspector of your own to
   the bridge with the host, port, and path shown in the panel: the desktop
   app, or the Inspector plugin of an Appium server on your computer. Only
   what goes through the Inspector is recorded; the live device view on
   BrowserStack's own site is not seen by the bridge.
4. Select elements in the Inspector and use its Tap and Send Keys buttons. Tap
   elements rather than points on the screenshot: an element keeps working when
   the layout moves, a point does not.
5. Type real values, including passwords. Password fields are never stored;
   the assistant names each one (for example `MOBILE_SECRET_PASSWORD`) for the
   pipeline's credentials. The other values become parameters for your test
   data.
6. Go through the whole scenario, then press **Save recording**. The
   recording goes into the assistant's workspace. The assistant lays it out by
   the screens it went through and proposes where to split it into segments,
   on a review card in the chat: approve the split or say what to change. It
   then compiles each segment and tells you how robust it is.
7. To record another scenario on the same device, carry on and save again.
   **Finish recording** when you are done. The device is released.

The device is held for 30 minutes at a time; **Hold 30 more minutes** extends
it. Keep the bridge running while you record: BrowserStack ends a session that
sees no command for five minutes.

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
