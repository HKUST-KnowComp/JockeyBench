# JockeyBench

English | [简体中文](README.zh-CN.md)

JockeyBench evaluates LLM agents that develop forecasting programs under a research budget.
The paper compares two workflows in the same framework. Both are installed from this repository root.

| Workflow | Command | What the agent develops | Evaluation arms |
| --- | --- | --- | --- |
| **Code-as-policy** | `jockeybench code-as-policy` | A Python program that produces race rankings and win probabilities | A: daily revision; S: frozen initial program |
| **LLM-as-policy** | `jockeybench llm-as-policy` | A reusable evidence plan; an LLM forecasts each race from the evidence collected | Adaptive: daily policy updates; Frozen: the same initial policy without updates |

Existing scripts can use `jockeybench program` for code-as-policy and `jockeybench research` for LLM-as-policy.

Both use the shared time-gated data service, accounting, prediction validation and scoring engine.
The public LLM-as-policy integration has its own `jockeybench-research-v1` profile. It is a new deployment
profile, not a byte-identical recreation of the historical DeepSeek/Frozen experiment.
See [architecture](docs/ARCHITECTURE.md) and the [LLM-as-policy guide](docs/RESEARCH.md).

**No data are distributed.** Users obtain JRDB data under their own subscription and build a private
database with the included importer. The 2026 season remains sealed. See [data](docs/DATA.md).

<a id="current-program-results"></a>

## Current code-as-policy results

The [2026-10-18 result snapshot](results/README.md) includes **26 models, with rounds 1–5 for each
model in both A and S**: 130 A and 130 S trajectories on the same 324 races over 10 race days.
Additional complete rounds are reported separately as robustness comparisons; two models with
fewer than five complete rounds are listed as exclusions. These are code-as-policy results.
The [machine-readable aggregates](results/program-results.json) accompany the tables.

## LLM-as-policy paper scores and materials

The paper uses **`score.common_net`** for its net score in the cross-workflow comparison.
Both report fields are trajectory totals. For the paper's per-race net score, divide `score.common_net`
by 324 for each complete run before averaging across runs. The common score applies the centered Top-1 term, `100 × (hit − 1/n)`, to every scorable forecast,
including uniform submissions and execution fallbacks. Here `n` is the number of active runners.
The LLM-as-policy report also gives **`score.aligned_net`**, which applies the same term only to
direct or repaired predictor deliveries. Both totals use the same log reward and deduct the
same weighted research costs, including the attributed warmup cost once. Provider charges are
reported separately in USD. The existing field `score.net_after_warmup` equals `score.aligned_net`.

- [Paper cohort](artifacts/workflow-cohort.csv). Filter the `workflow` column by `LLM-as-policy` or
  `code-as-policy`. The balanced comparison covers nine models and three runs per model per workflow,
  with 324 sealed races per run. Matched Frozen controls form a separate sample. The
  [legacy-label CSV](artifacts/workflow-cohort-legacy-labels.csv) differs only in the `workflow` column, which uses
  `Adaptive Acquisition` and `Program`. The `updater_lifecycle` column reflects the checks completed on
  2026-11-18; eleven trajectories previously recorded as UNKNOWN were confirmed as PASS from their
  saved post-release updater checkpoints.
- [Submitted interface supplement](artifacts/minimal-interface-supplement.zip). Open
  `jracebench-llm-policy/` for prompts, tool schemas, configuration and source references for
  the historical DeepSeek-V4-Flash R1 deployment and its matched Frozen control.

## Install once

Use **Python 3.13** on macOS, Linux, or **Windows through WSL2 with Ubuntu 22.04**.
Windows users should follow the [Windows installation guide](docs/WINDOWS.md), which includes a
PowerShell launcher and a fresh-environment installer. Native Windows Python and WSL1 are unsupported.
macOS uses `sandbox-exec`; Linux and WSL2 use bubblewrap and libseccomp.
On Ubuntu 22.04/24.04, first install:

```bash
sudo apt-get update
sudo apt-get install bubblewrap libseccomp2 libarchive-tools lsof build-essential
```

Python must include matching development headers for the `lhafile` extension. Linux must permit
bubblewrap user namespaces; containers and some AppArmor policies restrict them. Isolation failure
stops execution. Use an appropriately configured host rather than disabling host security globally.

From the repository root:

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/pip install --no-build-isolation --no-deps -e .
.venv/bin/jockeybench code-as-policy --help
.venv/bin/jockeybench llm-as-policy --help
```

There is one Python package, `jockeybench`, including `jockeybench.research`. A separately installed
`active_research` or `jrdb_lh` package and an external warmup source archive are not required by the
LLM-as-policy entrypoint. The older source-drop records remain as [provenance](provenance/active-research/README.md).

## Test with fabricated data

```bash
.venv/bin/pytest
JRDB_NATIVE=1 .venv/bin/pytest -m native
.venv/bin/python -B scripts/verify_research_provenance.py
```

Default tests use fabricated inputs and do not call providers. Embedded historical LLM-as-policy tests
are not collected by default; they are not evidence that the migrated public profile reproduced
historical trajectories. CI targets Ubuntu, macOS, and Windows Server 2025 with an actual WSL2 Ubuntu
22.04 instance; Windows 10/11 user machines are deployment targets, not a tested hardware matrix.
Contributors also install `requirements-dev.txt`.

## Run either setting

Run real-data and model commands only on a designated experiment host already provisioned with the
`.experiment-host` marker. Configure its private database, storage and provider credentials locally.
Do not create or copy the marker onto a development machine.

```bash
export JRDB_SOURCE="$PWD/data/private/expanded.sqlite" JRDB_RUNS="$PWD/runs"

# code-as-policy reference, with no model call.
.venv/bin/jockeybench code-as-policy --config configs/smoke.json doctor
.venv/bin/jockeybench code-as-policy --config configs/smoke.json run --run-id program-r2 --reference R2

# LLM-as-policy with daily updates and its matched Frozen control.
.venv/bin/jockeybench llm-as-policy --config configs/research.json run \
  --run-id research-adaptive --provider-config provider.local.json --arm adaptive
.venv/bin/jockeybench llm-as-policy --config configs/research.json run \
  --run-id research-frozen --provider-config provider.local.json --arm frozen \
  --initial-from research-adaptive
```

LLM-as-policy execution calls the configured model and may incur provider charges. Its provider file and
run directories are private inputs, not included examples of completed experiments. See
[LLM-as-policy configuration](docs/RESEARCH.md). Existing `jockeybench --config ...`
commands continue to select code-as-policy. See [models](docs/MODELS.md) and the [code-as-policy reproduction guide](docs/REPRODUCING.md).

## Project layout

```text
src/jockeybench/             Shared engine, code-as-policy workflow and CLI
src/jockeybench/research/    LLM-as-policy workflow and compatibility components
configs/                    Public configuration templates
tests/                      Supported synthetic and packaging tests
provenance/active-research/ Original source-drop records and migration hashes
results/                    Code-as-policy results of the main cohort (aggregates only)
```

The JockeyBench authors license both workflows under the [MIT License](LICENSE). This includes their reference prompts
and code in the paper's interface supplement. See [license scope](docs/LICENSE-SCOPE.md).
