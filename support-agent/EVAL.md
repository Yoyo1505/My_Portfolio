# Evaluation

Model: `llama3.1:8b` through Ollama, CPU only, temperature 0, fixed seed. 25 golden cases (`eval/golden.jsonl`) scored on route, required content, forbidden content and grounding.

## Run 1 (complete) - `eval/results/llama3.1-8b-run1.json`

| Passed | Route accuracy | Required content | Forbidden content absent | Grounded | Median latency |
|--------|----------------|------------------|--------------------------|----------|----------------|
| 19/25 | 100% | 76% | 100% | 100% | 69 s |

The 6 failures:

| Case | What happened | Cause |
|------|---------------|-------|
| g01, g05 | "I'm unable to find the order": the model called `get_order` with an invented id | Agent bug: the tool only accepted exact ids |
| g09 | Refund described as processed, without the amount | Tool output: `list_orders` did not expose refund details |
| g23 | Gave the due date but not the amount or that it failed | Model brevity; no prompt rule required amount, date and status |
| g03 | Correct explanation of retries but no amount | Model brevity |
| g07 | Said a payment cannot be moved; policy allows one reschedule | Model misread the policy chunk |

No answer contained an amount or date that was absent from the tool results, and no answer leaked another customer's data.

## Run 2 (cancelled after 14 of 25 cases) - `eval/results/llama3.1-8b-run2-partial-cancelled.log`

Changes: `get_order` accepts a merchant or item reference (still limited to the customer's own orders), `list_orders` exposes refund, dispute and reschedule state, and the prompt requires amount, date and status.

Of the 14 cases that finished, g01, g07 and g09 flipped from fail to pass; g03 and g05 still failed; the rest were unchanged. The run was stopped before the remaining 11 cases, so there is no complete run-2 score. Do not read the partial log as a result for the full set.

## Reading the numbers

Latency is CPU inference (about 3 tokens per second on this machine) and is not representative of a hosted model. The golden set is small and was written alongside the agent, so 76% is a development signal, not a generalization claim.
