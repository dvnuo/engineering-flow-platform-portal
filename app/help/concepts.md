---
title: Assistants, tasks, and automations
summary: The three things worth understanding once.
group: Getting started
icon: compass
order: 20
---
## Assistant

A workspace you chat with, running with your credentials.

## Task

Work handed to an assistant to run on its own, tracked to completion instead of
held in a chat. Use it when the work is long enough that you would rather do
something else meanwhile. A task is private (you and administrators) unless
you make it public from its page, which lets every member open it.

## Delegation

A rule that starts work automatically — on a GitHub review request, a Jira
assignment, or a timer. Like a task it is private until you make it public,
and the tasks it starts follow its setting.

## Scenario run

One Examples row of one scenario, run on one BrowserStack device. It ends with
a result, screenshots at its checks, and a video of the session: the evidence
for that scenario.

## Segment

A short recorded stretch of an app (logging in, choosing a currency) that
scenarios reuse. Recorded once per platform; the assistant keeps it working
when the app's layout moves.

## Connectors

Everything an assistant can reach: the model provider, Jira, Confluence,
GitHub, AWS and the rest, one connector each. You set each up once and every
assistant of yours uses it. The credentials are your own; your administrator
fills in only where each service lives.

Each connector starts on your group's Default connectors and changes with
them. **Customize** gives you your own values for that connector, which your
group's changes then leave alone; **Use group default** switches back. The
model provider is always your own, and the proxy is the platform's for
everyone.
