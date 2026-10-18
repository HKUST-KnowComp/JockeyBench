# JockeyBench

English | [简体中文](README.zh-CN.md)

JockeyBench evaluates LLM agents that develop forecasting programs under a research budget.
**Program and Active Research are two settings of one experiment framework**, installed from this
repository root with one `jockeybench` command.

| Setting | What the agent develops | Evaluation arms |
| --- | --- | --- |
| **Program** | A Python program that produces race rankings and win probabilities | A: daily revision; S: frozen initial program |
| **Active Research** | A reusable information-acquisition policy; a predictor uses the resulting evidence | Adaptive: daily policy updates; Frozen: the same initial policy without updates |

Both use the shared time-gated data service, accounting, prediction validation and scoring engine.
The public Research integration has its own `jockeybench-research-v1` profile. It is a new deployment
profile, not a byte-identical recreation of the historical DeepSeek/Frozen experiment.
See [architecture](docs/ARCHITECTURE.md) and [Research](docs/RESEARCH.md).

**No data are distributed.** Users obtain JRDB data under their own subscription and build a private
database with the included importer. The 2026 season remains sealed. See [data](docs/DATA.md).

## Current Program results

The [2026-10-18 result snapshot](results/README.md) includes **26 models, with rounds 1–5 for each
model in both A and S**: 130 A and 130 S trajectories on the same 324 races over 10 race days.
Additional complete rounds are reported separately as robustness comparisons; two models with
fewer than five complete rounds are listed as exclusions. These are Program-setting results.
The [machine-readable aggregates](results/program-results.json) accompany the tables.

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
.venv/bin/jockeybench program --help
.venv/bin/jockeybench research --help
```

There is one Python package, `jockeybench`, including `jockeybench.research`. A separately installed
`active_research` or `jrdb_lh` package and an external warmup source archive are not required by the
public Research entrypoint. The older source-drop records remain as [provenance](provenance/active-research/README.md).

## Test with fabricated data

```bash
.venv/bin/pytest
JRDB_NATIVE=1 .venv/bin/pytest -m native
.venv/bin/python -B scripts/verify_research_provenance.py
```

Default tests use fabricated inputs and do not call providers. Embedded historical Research tests
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

# Program: offline reference, with no model call.
.venv/bin/jockeybench program --config configs/smoke.json doctor
.venv/bin/jockeybench program --config configs/smoke.json run --run-id program-r2 --reference R2

# Research: provider-backed Adaptive and its matched Frozen control.
.venv/bin/jockeybench research --config configs/research.json run \
  --run-id research-adaptive --provider-config provider.local.json --arm adaptive
.venv/bin/jockeybench research --config configs/research.json run \
  --run-id research-frozen --provider-config provider.local.json --arm frozen \
  --initial-from research-adaptive
```

Research execution calls the configured model and may incur provider charges. Its provider file and
run directories are private inputs, not included examples of completed experiments. See
[Research configuration and boundaries](docs/RESEARCH.md). Existing `jockeybench --config ...`
commands continue to select Program. See [models](docs/MODELS.md) and [Program reproduction](docs/REPRODUCING.md).

## Project layout

```text
src/jockeybench/             Shared engine, Program setting and CLI
src/jockeybench/research/    Active Research setting and compatibility components
configs/                    Public configuration templates
tests/                      Supported synthetic and packaging tests
provenance/active-research/ Original source-drop records and migration hashes
results/                    Program-setting results of the main cohort (aggregates only)
```

The Program code retains its MIT license. The original Research source drop did not include a
separate license grant; moving it does not broaden the existing MIT grant. See [license scope](docs/LICENSE-SCOPE.md).
