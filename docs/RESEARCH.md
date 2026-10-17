# Active Research

The public `jockeybench research` entrypoint is installed together with Program. It uses the shared
JockeyBench engine and does not require an external framework checkout or warmup archive.

## Lifecycle and profile

`jockeybench-research-v1` is the public Research deployment profile:

1. Adaptive constructs an initial acquisition policy using historical support before the configured
   origin, subject to the public warmup's publication-time boundary.
2. The acquisition policy selects evidence for each race; a predictor produces the sealed forecast.
3. Outcomes are released at the day boundary. The framework builds a daily feedback Frame.
4. Adaptive may update its policy for later days. Frozen loads the exact initial policy from a
   completed matching Adaptive run and neither warms up again nor runs the updater.

The public warmup is identified separately as `jockeybench-research-warmup-v1`. It uses the installed
engine's source projection and services. Its output transfers an acquisition policy into the evaluated
trajectory; it is not a replacement claiming the fixed historical archive's hash or behavior.

Research uses the common configuration with core `arm: "A"` and `tariff.hit_bonus_yen: 0`.
Choose the Research arm through `--arm adaptive` or `--arm frozen`; the Program A/S flag is not the
Research control assignment. The score components and weighted research charges remain distinct
from provider USD and from real betting profit. See `research/common_scoring.py` and the run report.

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

.venv/bin/jockeybench research --config configs/research.json doctor
.venv/bin/jockeybench research --config configs/research.json run \
  --run-id research-adaptive --provider-config provider.local.json --arm adaptive
.venv/bin/jockeybench research --config configs/research.json report --run-id research-adaptive
.venv/bin/jockeybench research --config configs/research.json replay-audit --run-id research-adaptive

.venv/bin/jockeybench research --config configs/research.json run \
  --run-id research-frozen --provider-config provider.local.json --arm frozen \
  --initial-from research-adaptive
```

Frozen's source must be a completed Adaptive run under the same managed root, with matching config,
provider, source, code, platform environment and schedule. Initial-policy and warmup-accounting
identities are checked. Every new run requires a new name. Stopped or interrupted Research runs are
not restarted, resumed or spliced; changes require a fresh trajectory.

The manifest records the public profile, config, source identity, code, provider, environment and
schedule. Inspect delivery state before interpreting scores. `report` reads saved aggregate results;
`replay-audit` reads the source and replays recorded evidence and scoring on the experiment host.

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
