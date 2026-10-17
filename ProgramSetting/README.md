# JockeyBench

English | [简体中文](README.zh-CN.md)

**Evaluating LLM agents that write, test and revise forecasting programs under a research budget — on ten years of Japanese horse racing.**

An agent receives a sandboxed Python environment, a metered set of data tools over a time-gated
racing archive, and one job: write `predict(sdk, context)`. Every evening at 21:00 JST the framework —
not the model — runs the agent's current program on each of tomorrow's races and seals a ranking and
win probabilities. Results are released the next evening; in the adaptive arm the agent may then
revise its program. Every data read, data-tool call and CPU-second of the agent's code is billed in
*score-yen* (control tools are free), and the trajectory is scored by a smoothed log score minus those
fees.

This directory, `ProgramSetting/` of the JockeyBench repository, is the runtime of the paper's
**Program** setting (main cohort): protocol `economic-v1`, projection `objective-v5`, tariff
`score-yen-v2` at ×0.5, arms **A** (daily revision) and **S** (frozen initial program), and the
reference programs **R0–R3**. It is a self-contained package; run every command below from this
directory. Its CI workflow is `.github/workflows/program-setting.yml` at the repository root.

> **No data are distributed.** The archive is JRDB's paid member data. Every user obtains it under
> their own subscription and builds a private database with the importer included here. See
> [docs/DATA.md](docs/DATA.md).

## How a trajectory works

```text
 warmup (model session)            day d                                                   day d+1
 ─────────────────────── ┬──────────────────────────────────────────────────────────────┬──────────
  explore support races   │ 21:00 (d-1) cutoff     race day d       20:30 release   reflect  │ 21:00 (d)
  write strategy.py       │ framework runs predict ─► seal ─► races ─► results + score ─► (arm A) │ cutoff ...
  validate (3 races ×2)   │ (no model call)                           booked in ledger  revise  │
```

* **Information boundary.** Only whitelisted objective facts are projected: cards, workouts, past
  results, pedigree statistics, jockey/trainer form. Odds, popularity, payouts, JRDB indices and
  ratings, predictions and free text are never exposed. Identifiers are keyed pseudonyms.
* **Clock.** A card is visible from its cutoff, finish labels from day close (20:30 JST) and review
  fields from their publication time. The 2026 season is a sealed holdout the runtime never reads.
* **Program interface.** `predict(sdk, context)` returns a full ranking plus probabilities. The program
  sees race data only through `sdk.call(tool, **arguments)`, the same metered tools the agent uses.
* **Validation, not selection.** A new program must pass interface and resource checks on up to three
  support races, run twice. It is never selected by accuracy. On failure the previous valid
  program is kept, then a deterministic uniform forecast.
* **Score.** Per race, `S·ln(n·p̄) + I(predictor)·B·(hit − 1/n)` with S = B = 100 and smoothing
  λ = 0.02. A uniform forecast scores exactly 0. Data, call and CPU fees are subtracted. See
  [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for every rule.
* **Integrity.** A checkpoint is bound to the source, code, configuration, agent and sandbox
  environment. Every step is crash-idempotent. `replay-audit` re-serves every tool response and
  re-scores every race; `reexecute-audit` re-runs every sealed program.

## Install

Requires **Python 3.13**. The runtime supports **macOS** with `sandbox-exec` and **Linux** with
`bubblewrap` and `libseccomp`. Linux targets Ubuntu 22.04/24.04 on x86_64 or aarch64. Install its
system dependencies before creating the Python environment:

```bash
sudo apt-get update
sudo apt-get install bubblewrap libseccomp2 libarchive-tools lsof build-essential
```

`lhafile` may need to compile a C extension on Linux, especially on ARM, so a C compiler is required.
The Python installation must also provide matching Python 3.13 development headers. If your system
Python does not include them, install its matching development package (usually `python3.13-dev`
from the same package source as the interpreter); a managed Python distribution may include them.

The Linux host must permit bubblewrap to create user namespaces. Container policies and some
[Ubuntu 24.04 AppArmor configurations](https://documentation.ubuntu.com/security/security-features/privilege-restriction/apparmor/)
restrict this even when the packages are installed. Use a host
or runner with a policy that permits bubblewrap; the runtime refuses execution when isolation is
unavailable and never falls back to an unsandboxed process. Do not disable host security controls
globally to make a run start.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/pip install --no-build-isolation --no-deps -e .
```

The lock pins every runtime, importer and test dependency. The interpreter's importable libraries are
recorded in each trajectory, so keep the experiment environment free of extra scientific packages.
Contributors also install `requirements-dev.txt`, which adds `ruff`.

## Test without any data

```bash
.venv/bin/pytest
```

Every test uses fabricated data, held in memory or written to temporary directories. On either
supported platform, also exercise the native sandbox with fabricated data:

```bash
JRDB_NATIVE=1 .venv/bin/pytest -m native
```

These tests do not require an experiment-host marker, JRDB data or provider credentials. CI runs the
unit suite on Ubuntu 22.04, Ubuntu 24.04 and macOS, and native jobs on Ubuntu 22.04 and macOS.

## Run

Run real-data and model commands only on your designated experiment host, from a checkout root
already provisioned with the `.experiment-host` marker. Do not copy or create that marker on a
development machine. Prepare `data/private/expanded.sqlite` there
([docs/DATA.md](docs/DATA.md)), then run a reference program; reference programs make no model calls.

```bash
export JRDB_SOURCE="$PWD/data/private/expanded.sqlite" JRDB_RUNS="$PWD/runs"
.venv/bin/jockeybench --config configs/smoke.json doctor
.venv/bin/jockeybench --config configs/smoke.json run --run-id smoke-r2 --reference R2
.venv/bin/jockeybench --config configs/smoke.json replay-audit --run-id smoke-r2
.venv/bin/jockeybench --config configs/smoke.json reexecute-audit --run-id smoke-r2
```

`configs/paper.json` is the paper's main configuration: ten race days of 2024 H1, all eligible races.
To evaluate a model, write a provider file ([docs/MODELS.md](docs/MODELS.md)) and run one shared
warmup followed by both arms:

```bash
.venv/bin/jockeybench --config configs/paper.json run --run-id m-warmup --warmup-only --provider-config provider.json
.venv/bin/jockeybench --config configs/paper.json --arm S run --run-id m-S --initial-from m-warmup --provider-config provider.json
.venv/bin/jockeybench --config configs/paper.json --arm A run --run-id m-A --initial-from m-warmup --provider-config provider.json
```

Audit each run with the same `--config` and `--arm` it ran with, for example
`--arm S replay-audit --run-id m-S`. `report --run-id m-A` prints a stored report, and
`compare runs/m-A/report.json.gz runs/m-S/report.json.gz` pairs two reports race by race (left minus
right, here A − S).

A runs root (`JRDB_RUNS`) holds one active trajectory at a time, and its 5 GiB storage ceiling covers
every run in it. `--initial-from` reads from the same root, so a model's warmup and both arms share a
root and run one after another; give each model its own root.

Every command prints JSON. Exit code 2 is a protocol refusal, and 3 an interrupted provider request.
For exit 3, resume by re-running the same command with the original code and environment. Moving to
Linux requires fresh trajectories; do not resume or splice a macOS trajectory on Linux.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/jockeybench/data/` | Read-only, time-gated archive access, field whitelist and projection |
| `src/jockeybench/protocol/` | Tool broker (metering, prices, idempotence), tool specifications, the agent task specification |
| `src/jockeybench/sandbox/` | macOS `sandbox-exec` and Linux bubblewrap/seccomp execution, CPU accounting, strategy versions; `client/` is the only benchmark code a sandboxed program can see |
| `src/jockeybench/lifecycle.py`, `runner.py` | Warmup, sealing, settlement, reflection, validation and fallbacks; the checkpointed run |
| `src/jockeybench/scoring.py`, `ledger.py`, `reporting.py` | Scoring rule, integer score-yen ledger, reports and paired comparisons |
| `src/jockeybench/agents/` | OpenAI-compatible tool-calling agent, transport with cost monitoring, reference programs R0–R3 |
| `src/jockeybench/audit/` | Replay and re-execution audits |
| `src/jockeybench/importer/` | JRDB archives → private database → keyed pseudonyms |
| `configs/` | `paper.json` (main protocol) and `smoke.json` (two days) |
| `tests/` | Unit and native tests on fabricated data |
| `docs/` | Architecture, data preparation, models, reproduction |

## Reproducibility

The rules, scoring, accounting and audits are those of the v0.6.0 runtime that produced the paper's
main cohort. The agent-facing contract (system prompt, tool specifications, every tool response and
error message) is the v0.6.0 contract with the statements that contradicted the enforced rules
corrected, released as protocol **0.6.1**. Examples are whether probabilities are required, how the hit
bonus treats equal probabilities, and the real response ceiling. [CHANGELOG.md](CHANGELOG.md) and
`protocol/revisions.py` list every change.

The tests prove that reverting the corrections reproduces the v0.6.0 system prompt, tool
specifications and session task texts byte for byte. Tool responses and error messages were compared
by a fingerprinting harness that ran full synthetic trajectories, including a scripted model session,
against the original runtime. It drives the internal v0.6.0 runtime and is not distributed. It found
no difference other than those the corrections cause and three no-op limits that are no longer part of
the bound configuration. [docs/REPRODUCING.md](docs/REPRODUCING.md) lists what was verified and what
was removed. It also covers what cannot be reproduced exactly: provider nondeterminism, your private
pseudonymisation key and your interpreter environment.

Linux support preserves the protocol rules but does not establish identical predictions, CPU fees
or scores across operating systems or hardware. Runtime 1.1.0 records the machine architecture with
the platform and sandbox environment for each new trajectory. This platform support applies to
`ProgramSetting/`; `ActiveResearch/` still
depends on its external `jrdb_lh` harness and is not a standalone Linux package.

## Citation

The accompanying paper, *JockeyBench: Evaluating LLM Agents on Horse-Racing Decision-Making*, is
under review. Citation details will be added after the review period.

## License

The code is released under the [MIT License](LICENSE). It covers this repository only and grants no
right to JRDB data or specification documents, which remain subject to your agreement with JRDB; see
[NOTICE.md](NOTICE.md).
