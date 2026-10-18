# Evaluating a model in the Program setting

For Active Research, use the separate [Research provider configuration](RESEARCH.md). Its public
profile validates a narrower set of provider fields and uses a different lifecycle.

Any OpenAI-compatible chat-completions endpoint with tool calling can be evaluated. Model calls happen
only when `--provider-config` is given. A provider receives the permitted projection of your licensed
data through tool responses, so use only services your data agreement allows.

## Provider file

```json
{
  "base_url": "https://api.example.com/v1",
  "model": "example-model",
  "api_key_env": "JOCKEYBENCH_API_KEY",
  "reasoning_effort": "high",
  "temperature": 0,
  "output_token_field": "max_tokens",
  "timeout_seconds": 900,
  "retry_attempts": 10,
  "retry_base_seconds": 2,
  "retry_max_seconds": 300,
  "input_usd_per_million": 0.5,
  "output_usd_per_million": 1.5,
  "reservation_output_tokens": 65536,
  "max_estimated_cost_usd": 10
}
```

| Field | Meaning |
| --- | --- |
| `api_key_env` | Name of the environment variable holding the key (default `OPENAI_API_KEY`). The key is read from that variable only; the run stops before any request if it is unset. |
| `temperature`, `seed`, `reasoning_effort` | Sent verbatim when present; omit a field to leave it to the provider default |
| `output_token_field` | `max_tokens` (default) or `max_completion_tokens`. Its value is `limits.model_output_tokens` from the run configuration (`null` in the paper: no framework cap). |
| `timeout_seconds` | Per-request timeout (default 300) |
| `retry_*` | Bounded exponential backoff for network errors, 408/409/429 and 5xx. Failed attempts are recorded but consume no model call. Defaults when omitted: `retry_attempts` 1 (no retry), `retry_base_seconds` 2, `retry_max_seconds` 300. |
| `*_usd_per_million`, `reservation_output_tokens`, `max_estimated_cost_usd` | Cost monitoring. Before each attempt, a worst-case cost is reserved. The run stops (exit 2, `funding_stop`) once observed plus reserved cost reaches the threshold, or when a cost cannot be bounded. On endpoints that retry often, size the threshold for the reservations, not for the expected spend. |

Provider-reported costs (`usage.estimated_cost`) take precedence over configured rates. Missing token
usage stays unknown and is never priced at zero. Reports list observed USD separately from the
score-yen ledger (`net_B_yen` subtracts it at `tariff.usd_to_yen`).

## Running

```bash
export JOCKEYBENCH_API_KEY=...
.venv/bin/jockeybench --config configs/paper.json run --run-id m-warmup --warmup-only --provider-config provider.json
.venv/bin/jockeybench --config configs/paper.json --arm S run --run-id m-S --initial-from m-warmup --provider-config provider.json
.venv/bin/jockeybench --config configs/paper.json --arm A run --run-id m-A --initial-from m-warmup --provider-config provider.json
.venv/bin/jockeybench --config configs/paper.json --arm S replay-audit --run-id m-S
.venv/bin/jockeybench --config configs/paper.json --arm A reexecute-audit --run-id m-A
```

Audit each run with the same `--config` and `--arm` it ran with. One runs root holds one active
trajectory, and `--initial-from` reads from the same root, so a model's warmup and arms run one after
another; run other models or repetitions in their own roots.

For comparison with the [current Program result snapshot](../results/README.md), use five independent
repetitions per model, each with a shared warmup for its matched A/S pair. The published main table
selects round indices 1–5 for both arms; extra complete rounds are reported separately as robustness
comparisons. These commands run one pair at a time and do not automatically repeat it five times.

A run that exits with code 3 has an interrupted provider request. Resume it by re-running the same
command; the checkpoint continues exactly where it stopped. Never resume a run with a changed
provider file or configuration: the binding refuses it, by design. Keep independent repetitions in
separate run ids.

## Scaffolds

The paper also evaluated models through Codex and Claude Code clients with the same tools. Those
client adapters are tied to local subscription installations and are not part of this release. This
package provides the API-compatible loop (`agents/compatible.py`). Each model session is one
conversation. Old complete tool rounds are dropped first when the context limit is reached, and a
notice is added to the system message.
