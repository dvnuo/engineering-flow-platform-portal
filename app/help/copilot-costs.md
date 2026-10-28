---
title: Copilot costs
summary: Why one message can cost a hundred credits, and how to spend less.
group: Working
icon: coins
order: 25
---
## How Copilot bills now

Since June 2026 GitHub Copilot bills per **token**, in GitHub AI Credits (1 credit = $0.01).
The Business plan includes $19 per member per month, pooled at the organization level; past that, the same per-token prices apply.
The old "one premium request per message" model is gone.

The cost of one model call = input tokens × price + cached input tokens × discounted price + output tokens (reasoning included) × price.
GPT-6.1 Sol's default-tier prices: input $2 per million tokens, cached input $0.10, output $10.

## Why one message can cost a hundred credits

- Answering one message often takes a dozen or more steps: read a file, run a command, look at the result. **Every step resends the whole context** (system prompt, tool definitions, history, tool output).
- 15 steps with a 100K-token context each is 1.5 million input tokens: about 100 credits when the cache hits, 400 or more when it does not.
- Reasoning ("thinking") counts as output, priced at five times the input rate. High reasoning effort adds a lot of reasoning tokens.

Hover over the model badge at the top right of an answer (it reads like "Auto → GPT-6 Luna") to see what that answer cost: steps, input / output tokens, cache hit rate, estimated credits and the billing tier, under the model lines.
The estimate uses GitHub's published price list; it is not the bill itself.

## The 272K line

When one request's input exceeds **272K tokens**, the GPT-6 line moves to the long context tier and **every price doubles**.
The Context panel marks that line on its meter; past it, the meter turns to the warning colour and the panel says "Long context".

In the context size presets, 1M is labelled "Long context 2×". Pick it knowing what it means.

## How to spend less

1. **Start a new session for a new topic.** The longer the context, the more every message costs, and tool output from an old session stays in it.
2. **Paste fewer large files.** Pasted content is resent on every step; letting the assistant read the parts it needs is cheaper.
3. **Keep reasoning effort at Medium.** It is enough for everyday work; raise it to High per session in Run settings when a task needs it.
4. **Keep the model on Auto.** Copilot picks a cheaper model for simple requests and takes 10% off.
5. **Watch for the "Paused" notice.** When one message exceeds the credits limit or the step limit, the assistant stops and explains; click "Continue" only if you want it to carry on.

## Checking your balance

- "Copilot balance this month" in the Context panel shows used / remaining credits and the reset date (from GitHub's quota endpoint, which is not guaranteed to be available).
- Usage (30 days) in the assistant details shows the last 30 days' requests, tokens and estimated credits.
- The authoritative numbers are on GitHub's Billing page: [Models and pricing for GitHub Copilot](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing).
