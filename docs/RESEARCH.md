<a name="active-research"></a>

# LLM-as-policy

The public `jockeybench llm-as-policy` entrypoint is installed alongside the code-as-policy workflow.
It uses the shared JockeyBench engine and does not require an external framework checkout or warmup archive.
It runs on macOS, Linux, and [Windows through WSL2 with Ubuntu 22.04](WINDOWS.md).

## Lifecycle and profile

`jockeybench-research-v1` is the public LLM-as-policy deployment profile:

1. Adaptive constructs an initial acquisition policy using historical support before the configured
   origin, subject to the public warmup's publication-time boundary.
2. The acquisition policy selects evidence for each race; a predictor produces the sealed forecast.
3. Outcomes are released at the day boundary. The framework builds a daily feedback Frame.
4. Adaptive may update its policy for later days. Frozen loads the exact initial policy from a
   completed matching Adaptive run and neither warms up again nor runs the updater.

The updater after the first race day must pass its lifecycle check before the second day starts.
From the second day onward, a failed check retains the previous validated policy and execution
continues. Each check is saved in `updater-checkpoint-<day>.json`; the final report lists the failed
dates in `updater_failed_days`, including a failure after the last race day. A candidate saved during
an unsuccessful updater session remains in the audit records, while the previous policy stays active.

The public warmup is identified separately as `jockeybench-research-warmup-v1`. It uses the installed
engine's source projection and services. Its output transfers an acquisition policy into the evaluated
trajectory; it is not a replacement claiming the fixed historical archive's hash or behavior.

The LLM-as-policy workflow uses the common configuration with core `arm: "A"` and `tariff.hit_bonus_yen: 0`.
Select the LLM-as-policy arm with `--arm adaptive` or `--arm frozen`. The A/S flag controls the
code-as-policy arm. The score components and weighted research charges remain distinct from provider USD and from real betting profit. See `research/common_scoring.py` and the run report.

## Provider settings

Create a private `provider.local.json` file and replace the example endpoint, model and prices with
values for your chosen provider. Keys are read only from the named environment variable.

```json
{
  "research_profile": "jockeybench-research-v1",
  "base_url": "https://api.example.com/v1",
  "model": "example-model",
  "api_key_env": "JOCKEYBENCH_API_KEY",
  "temperature": 0,
  "output_token_field": "max_tokens",
  "timeout_seconds": 900,
  "retry_attempts": 1,
  "input_usd_per_million": 0.5,
  "output_usd_per_million": 1.5,
  "reservation_output_tokens": 65536,
  "max_estimated_cost_usd": 1
}
```

The example prices and ceiling are illustrative, not a price quote or a recommended experiment budget.
The public profile requires positive finite prices, timeout and cost ceiling, positive integer retry
and reservation limits, and `output_token_field: "max_tokens"`. Optional `seed`, `reasoning_effort`,
`retry_base_seconds` and `retry_max_seconds` follow the provider transport. Unknown fields, embedded
credentials and unsupported output-token conventions are rejected. A provider must support the
native chat-completions tools used by the builder, predictor and updater.

`max_estimated_cost_usd` applies separately to the warmup, each race's prediction session and each
daily updater session. **It is not a total-trajectory spending cap.** A trajectory can therefore spend
more than this value across its sessions. Reports keep provider accounting separate from the
score-yen ledger; choose and authorize the overall experiment budget before launching.

Only call providers permitted by your data agreement. Model support depends on that provider's
behavior; structural compatibility is not evidence of end-to-end validation of every model.

## Commands

On the designated experiment host, using its local private data and credentials:

```bash
export JRDB_SOURCE="$PWD/data/private/expanded.sqlite" JRDB_RUNS="$PWD/runs"
export JOCKEYBENCH_API_KEY=...

.venv/bin/jockeybench llm-as-policy --config configs/research.json doctor
.venv/bin/jockeybench llm-as-policy --config configs/research.json run \
  --run-id research-adaptive --provider-config provider.local.json --arm adaptive
.venv/bin/jockeybench llm-as-policy --config configs/research.json report --run-id research-adaptive
.venv/bin/jockeybench llm-as-policy --config configs/research.json replay-audit --run-id research-adaptive

.venv/bin/jockeybench llm-as-policy --config configs/research.json run \
  --run-id research-frozen --provider-config provider.local.json --arm frozen \
  --initial-from research-adaptive
```

Frozen's source must be a completed Adaptive run under the same managed root, with matching config,
provider, source, code, platform environment and schedule. Initial-policy and warmup-accounting
identities are checked. Every new run requires a new name. Stopped or interrupted LLM-as-policy runs are
not restarted, resumed or spliced; changes require a fresh trajectory.

The manifest records the public profile, config, source identity, code, provider, environment and
schedule. The paper compares trajectories using `score.common_net`; the forecast prompt uses the
delivery-gated objective reported as `score.aligned_net`. The [README](../README.md#llm-as-policy-paper-scores-and-materials)
defines both scores. For completed reports from earlier public LLM-as-policy versions, the two scores agree
when the saved delivery counts show that every forecast was direct or repaired. Otherwise, `report`
computes `common_net` in memory from the saved scoring checkpoints. If the available records cannot
establish the common score, it returns `null` for that field and retains the recorded aligned net.
`replay-audit` separately verifies recorded evidence and scoring on the experiment host.
For an exact replay audit or a matched Frozen control, use the source revision recorded in the
original run's manifest. On an older revision, the LLM-as-policy command is `jockeybench research`.

## Historical evidence and verification scope

The original ActiveResearch publication contained 128 Python files and metadata for 27 excluded
historical files. Those original hashes and its earlier macOS validation results are preserved under
[provenance](../provenance/active-research/README.md). The current migration records original and
current hashes separately. Namespace and runtime changes are intentional changes, not byte identity.

The historical fixed 324-race deployment, DeepSeek-specific identities, original archive hash and
specialized model adapters are not silently treated as interchangeable with the public profile.
Root synthetic tests verify the supported migrated behavior; the static provenance checker verifies
file identity and imports. Neither constitutes a rerun of the paper or verification with private data
and live providers. Legacy preparation modules remain for traceability and are not the public launch
interface.
