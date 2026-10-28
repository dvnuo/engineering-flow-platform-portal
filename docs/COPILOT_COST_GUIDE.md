# Copilot cost guide (for members)

The same guide inside the Portal is Help → Working → "Copilot costs" (`app/help/copilot-costs.md`).

## How billing works

- Since 2026-06-01 GitHub Copilot bills per token in GitHub AI Credits, 1 credit = $0.01; the Business plan includes $19 per member per month, pooled at the organization level, and overage is billed at the same per-token prices. Premium requests (per call) remain only for existing annual Pro subscribers.
- GPT-6.1 Sol default tier: input $2.00 per million tokens, cached input $0.10, cache write $2.50, output $10 (reasoning counts as output). A request whose input exceeds 272K tokens moves to the long context tier, where everything doubles.
- Official price list: <https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing>

## Why one message can cost a hundred credits

The agent loop behind one message runs a dozen or more steps, and every step resends the whole context (system prompt, tool definitions, history, tool output). 15 steps at about 100K tokens each is 1.5 million input tokens: about 100 credits with cache hits, 400 or more without, and double again on the long context tier. High reasoning effort adds reasoning tokens, billed as output.

## What the Portal shows

| Where | What |
| --- | --- |
| The model badge at the top right of every answer, on hover | Model, requested model and tier, then steps, input / output tokens, cache hit rate, estimated credits and the billing tier |
| Context panel | The 272K billing line on the meter, the current tier, the runtime's real budget, usage calibrated from measured tokens, this session's cumulative credits, this month's Copilot balance |
| Assistant details, Usage (30 days) | Requests, input / output / cached / reasoning tokens, estimated credits |
| "Paused" notice | When one message exceeds the credits limit or the step limit the assistant stops and explains; click "Continue" to carry on |

## How to spend less

1. Start a new session for a new topic: the longer the context, the more every message costs.
2. Paste fewer large files: pasted content is resent on every step.
3. Reasoning effort defaults to Medium: raise it to High per session when needed.
4. Keep the model on Auto: simple requests go to a cheaper model, with a 10% discount.
5. The 1M context preset is labelled "Long context 2×"; pick it knowing the price.
