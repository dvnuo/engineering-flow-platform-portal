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
4. You record short segments on a real BrowserStack device: log in, skip the
   introduction, choose a currency. Each is recorded once per platform.
5. The assistant turns the approved scenarios and your segments into one test
   per Examples row, runs one on a single device, and asks you to approve it.
6. The run fans out in parallel. Each scenario ends with a result, its
   screenshots, and a video of the session; failures are sorted into script
   drift, product defects, infrastructure, or test data.

## Before you start

- **A QA Assistant**: create your assistant with the *QA Assistant* type. It
  carries the skills and instructions this flow uses; other types do not.
- **Connectors > BrowserStack**: your username and access key, turned on.
  *Test BrowserStack* shows how many parallel sessions your plan has; a run
  waits for free ones.
- **App packages** (same page): upload the builds you test, or give a Jenkins
  or Nexus artifact URL. Give every build of an app the same custom id, for
  example `fxapp-android-uat`; tests that name the custom id always get the
  newest build. BrowserStack keeps uploads for 30 days.
- **Jira**: the delegation needs your Jira connector. Ask your BA to fill in
  the expected results in each Examples row; a row without them cannot be
  checked, and the assistant flags it instead of inventing a value.

## Recording a segment

Open a chat with the assistant and choose **Recording** in the tool bar.

1. Pick the app package, optionally a device, and list the segments you will
   record, then **Start recording session**. The assistant starts the device
   and holds it; it appears in the panel within about a minute.
2. **Open Inspector** opens Appium Inspector already attached to that device
   (when your Portal hosts it). Otherwise attach the desktop Inspector with the
   session id shown in the panel.
3. Select elements in the Inspector and use its Tap and Send Keys buttons. Tap
   elements rather than points on the screenshot: an element keeps working when
   the layout moves, a point does not.
4. Type real values, including passwords. Password fields are never stored; the
   assistant names each one (for example `MOBILE_SECRET_PASSWORD`) and you add
   its value under **Connectors > BrowserStack > Test secrets**. The other
   values become parameters for your test data.
5. Press **Segment done**. The assistant compiles the segment and tells you how
   robust it is. Carry on with the next segment on the same device.
6. **Finish recording** when you are done. The device is released.

The device stays held while you record. If you need longer, use **Hold 15 more
minutes**; BrowserStack ends a session that sees no command for five minutes.

## What a recording grade means

| Grade | Meaning |
| --- | --- |
| good | Every step targets an element by an id or its text |
| fair | Some steps need a look: a coordinate tap, an element picked by position |
| poor | Most steps depend on screen positions; ask the app developers to add accessibility ids or resource ids, then record again |

## Reading the results

Each scenario run has an evidence card: its result, the device, the
BrowserStack session, a video, and screenshots at the checks. The task page of
a run shows the scenario matrix while it runs, and the triage of every failure
afterwards, with links to the defect or the fix.
