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
4. You record short segments on a real BrowserStack device from your computer:
   log in, skip the introduction, choose a currency. Each is recorded once per
   platform.
5. The assistant turns the approved scenarios and your segments into one test
   per Examples row, commits them to your team's test repository, and has the
   Jenkins pipeline run one row of each scenario on a single device. You
   approve them.
6. The full run goes through the same pipeline, in parallel. Each scenario
   ends with a result, its screenshots, and a video of the session; failures
   are sorted into script drift, product defects, infrastructure, or test data.

## Where things run

| What | Where |
| --- | --- |
| Recording segments | Your computer: the Recording panel drives the local bridge, which drives the BrowserStack device |
| Compiling segments, writing tests, triage | The assistant, on files only |
| Dry runs, full runs, reruns | Your team's Jenkins pipeline |

Portal and the assistants never connect to BrowserStack.

## Before you start

- **A QA Assistant**: create your assistant with the *QA Assistant* type. It
  carries the skills and instructions this flow uses; other types do not.
- **Connectors > BrowserStack**: your username and access key, turned on.
- **Connectors > Local browser**: install the local bridge on your computer
  once; recordings run through it. *Test BrowserStack* on the BrowserStack
  page checks that your computer reaches BrowserStack.
- **Connectors > Jenkins** and **GitHub**: the assistant starts the mobile
  scenario pipeline with your Jenkins connector and commits the tests to your
  team's test repository with GitHub. Your Jenkins administrator sets the
  pipeline up once, with the test accounts' passwords as Jenkins credentials.
- **Jira**: the delegation needs your Jira connector. Ask your BA to fill in
  the expected results in each Examples row; a row without them cannot be
  checked, and the assistant flags it instead of inventing a value.

## Recording a segment

Open a chat with the assistant and choose **Recording** in the tool bar.

1. If the panel says the local bridge is not running, press **Start bridge**.
2. Pick a build (or upload one), the platform, optionally a device, and list
   the segments you will record, then **Start recording device**. The bridge
   starts the device and holds it; it is ready in about a minute, longer when
   all your parallel sessions are busy.
3. **Open Inspector** opens Appium Inspector already attached to that device
   (when your Portal hosts it). Otherwise attach the desktop Inspector to the
   bridge with the host, port, and path shown in the panel.
4. Select elements in the Inspector and use its Tap and Send Keys buttons. Tap
   elements rather than points on the screenshot: an element keeps working when
   the layout moves, a point does not.
5. Type real values, including passwords. Password fields are never stored;
   the assistant names each one (for example `MOBILE_SECRET_PASSWORD`) for the
   pipeline's credentials. The other values become parameters for your test
   data.
6. Press **Segment done**. The segment goes into the assistant's workspace;
   the assistant compiles it and tells you how robust it is. Carry on with the
   next segment on the same device.
7. **Finish recording** when you are done. The device is released.

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
