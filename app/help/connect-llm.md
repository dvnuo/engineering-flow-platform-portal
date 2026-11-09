---
icon: sparkles
---
## What this is for

Every answer an assistant gives comes from a model provider. Without this, an
assistant starts but cannot reply.

Your administrator chooses which provider is available. You authorize your own
access to it.

## Steps per message

One message can take many model calls, one for each tool the assistant uses.
When a message reaches the step limit, the assistant stops, summarizes what it
has done so far, and the chat shows "Paused at the step limit" with a Continue
button.

Your administrator sets the default limit. To use a different one for your own
assistants, fill in Max steps per message here; leave it blank to follow the
default again. A higher limit lets one message run longer, and cost more.

## If it stops working

Authorization can expire. The symptom is a run that fails immediately with a
credentials error rather than a slow or wrong answer.

Re-run the authorization here; you do not need to recreate the assistant.
