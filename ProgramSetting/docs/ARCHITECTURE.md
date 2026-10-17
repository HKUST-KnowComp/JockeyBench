# Architecture

This document describes what the runtime guarantees and where each rule lives. The agent-facing
wording of every rule is produced by `protocol/specification.py`. Agents read it as the system prompt
and through `describe`.

## Modules

```text
cli.py ──► runner.Run ──► lifecycle (warmup · predict · settle · reflect)
              │                 │
              │                 ├─► agents/ (CompatibleAgent | ProgramReference)
              │                 │        └─► protocol/service.Service.call  ◄── sandbox/client (SDK pipe)
              │                 │                     │
              │                 └─► sandbox/executor  ├─► data/source.Source (read-only SQLite)
              │                                       └─► ledger (score-yen)
              ├─► storage (checkpoint.json.gz, versions/, workspace/)
              └─► scoring · reporting · audit/
```

| Module | Responsibility |
| --- | --- |
| `data/source.py` | Opens the prepared database read-only and immutable, refuses active writers, and serves cards, history pages, results and form at a decision clock |
| `data/fields.py`, `data/projection.py`, `data/catalog.py` | The field whitelist, typed projection with explicit missing reasons, and the agent-facing field catalogue |
| `protocol/service.py` | The only boundary between an agent or program and the data: validation, metering, pricing, allowances, cursors, idempotence and the audit log |
| `protocol/tools.py` | Tool arguments and JSON-schema specifications, in the order sent to the model |
| `protocol/specification.py` | The task specification (system prompt), `describe`, and the per-session task payload |
| `sandbox/executor.py` | Platform-specific sandbox jobs, the SDK pipe broker, `wait4` CPU accounting, and content-addressed strategy versions |
| `lifecycle.py` | The trajectory state machine: sessions, validation, repair, prediction fallbacks and daily settlement |
| `runner.py` | The checkpoint binding, resume and shared-warmup import, and the isolation preflight |
| `scoring.py`, `ledger.py` | Prediction validation, the scoring rule, and the integer micro-yen ledger with reconciliation |
| `audit/` | Replay and re-execution of a completed checkpoint |
| `importer/` | Archive decoding and keyed pseudonymisation; never runs during a trajectory |

## The clock

Times are JST and are computed in `clock.py`.

* `cutoff(D)` is 21:00 on the evening before race day D. Every prediction for day D runs at this
  clock, with cards and labels published by then.
* `release(D)` is 20:30 on day D. Finish and abnormal-status labels of day D become visible, and
  revenue is booked.
* Review fields (sectional times, margins, running details) are visible from their publication time:
  the following Thursday at 18:00 in the importer's reconstruction.

A request for a race whose card is not yet published is refused. `form`, the form attached to history
pages by `include_form`, and `connections` are frozen at the target race's own cutoff, even when
queried later. History pages (`history`, `horse_history`, paged `results`) and the `statistics` and
`coverage` views over them carry the labels visible at the query clock. No query can return a race
dated 2026-01-01 or later.

## Tools and metering

`Service.call` validates the tool name and arguments, charges one tool call and checkpoints, and only
then dispatches. *Data tools* (`card`, `form`, `history`, `horse_history`, `training`, `statistics`,
`coverage`, `results`, `connections`) are metered in **evidence cells**: one cell per scalar fact slot
delivered, nulls included. A *repeat* (the same tool and canonical arguments with an identical response
digest within one trajectory) carries no evidence fee. Fees:

| Fee | Base price (score-yen) | Applies to |
| --- | --- | --- |
| evidence | 0.01 per charged cell | data tools |
| call | 0.5 per data-tool call | data tools, including rejected requests; not allowance or safety-limit rejections, undeliverable responses, or a `max_spend` that is not a finite nonnegative number |
| CPU | 0.2 per CPU-second | sandbox processes (user + system time, measured with `wait4`) |

All prices are multiplied by `tariff.multiplier` (0.5 in the paper). Control tools carry no call or
data fee; `compute` pays its CPU and the fees of its nested SDK calls. A direct response larger than
the fixed ceiling `min(response_bytes, context_bytes // 3)` (87,381 bytes at the defaults, envelope
included) is refused with no data or call fee; SDK calls inside programs may return up to
`response_bytes`. A request whose fees exceed its `max_spend` is refused before any data fee is booked
and keeps its call fee. Data allowances (calls, cells, bytes per phase) reject
*recoverably* with `BudgetLimit`. Model-call, turn or context exhaustion ends the session.

Every response carries `_cost` (this call's receipt), `_ledger` (cumulative) and `_budget` (the
remaining allowances).

### Idempotence

Calls made on behalf of a model carry an `operation_id`. After a crash:

* a completed data read is re-dispatched and must reproduce its recorded digest;
* a completed control call returns its stored result;
* a read interrupted after its call was charged completes without a second charge;
* an interrupted mutation (`compute`, `save_strategy`) is rolled back to its strategy snapshot and
  reported to the model. It is never executed twice.

## Programs and the sandbox

Each `compute` runs in a fresh interpreter inside the platform's sandbox. macOS uses a deny-default
`sandbox-exec` profile. Linux uses bubblewrap namespaces and restricted filesystem mounts, with a
libseccomp filter that denies network and subprocess creation. Both backends expose the interpreter
and its libraries, `sandbox/client/`, and the strategy workspace. Prediction and validation jobs
get the workspace read-only. The SDK reaches the broker through two inherited pipes.
`Run.preflight` checks, before any agent code runs, that the archive, its key, a canary and the
checkpoint are unreadable, and that network and subprocesses are denied.

Linux requires bubblewrap, libseccomp and a host policy permitting user namespaces; the target
distributions are Ubuntu 22.04/24.04 on x86_64 and aarch64. A missing dependency, denied namespace
operation or failed isolation check refuses execution. There is no unsandboxed fallback. Containers
and Ubuntu 24.04 AppArmor policies may require an appropriately configured host. Native tests
exercise isolation using fabricated data and require no experiment-host marker.

`save_strategy` takes flat `.py/.json/.txt/.md` files. The workspace, and so every saved version, may
also hold flat `.bin/.pkl` files written by `compute`. Every `.py` file must parse, `strategy.py`
must define `predict(sdk, context)`, and JSON that is not finite or looks like cached historical
samples is refused. Each saved state is a
content-addressed version under `versions/`. Files written during a model session persist only if
the session calls `save_strategy`.

## The trajectory

1. **Warmup.** The agent explores initial-support races (before the window) in one session and saves
   a program.
2. **Validation.** A changed program runs twice on up to three support races. Interface or resource
   failures reject it and keep the previous version. Uninformative output (all probabilities equal)
   or nondeterministic output keeps the program valid but grants one repair session per phase.
3. **Prediction.** At each cutoff the framework executes the current program on every race of the
   day. If it fails, the previous valid version runs once; if that fails too, a seeded uniform
   forecast is used. Each sealed prediction records its *delivery*: `predictor`, `silent_uniform`,
   `explicit_uniform`, `no_strategy` or `execution_fallback`.
4. **Settlement.** At release the day's races are scored and booked in one checkpoint.
5. **Reflection** (arm A only). The agent sees the day's per-race revenues, hit counts and the ledger,
   and may revise the program, which is then validated as above. Arm S skips reflection and cannot
   save.

Arms A and S start from the same warmup with `--initial-from`. The S run imports the warmup's program,
receipts and costs.

## Scoring

For a race with `n` starters (scratched runners removed and probabilities renormalised):

```text
p̃_i      = (1 − λ)·p_i + λ/n                      λ = 0.02
revenue  = S·ln(n · mean_{i ∈ winners} p̃_i)       S = 100; uniform scores exactly 0
         + I(delivery = predictor) · B · (hit − 1/n)   B = 100; hit = top-ranked starter won
net      = Σ revenue − Σ fees
```

Dead heats average over all official winners. Cancelled races and races with missing results book
zero. The private market comparison and the nominal no-takeout return in reports are evaluator
diagnostics. They are never shown to agents.

## Checkpoints and audits

A run directory holds `checkpoint.json.gz`, the strategy `versions/` and the `workspace/`. Checkpoints
are written atomically with an embedded digest. Each one binds the source manifest, the public
configuration, the runtime code hash (the package minus the importer), the agent identity and the
sandbox environment, including machine architecture. A resume with any of them changed is refused.

Moving between macOS and Linux requires a new trajectory, including a new warmup. Checkpoints must
not be resumed or spliced across operating systems. The protocol rules remain the same, but native
library behavior and CPU time can differ; Linux support does not establish equal predictions,
CPU fees or final scores across platforms.

* `replay-audit` re-serves every successful response from the unchanged source and checks its
  digest, evidence count and fee. It also reconciles the ledger with the receipts and recomputes
  every sealed score.
* `reexecute-audit` restores each sealed strategy version and re-runs it at its sealed clock, with
  fees quoted but not booked, so `max_spend` binds as it did. It requires the same ranking and
  probabilities within 1e-9. It restores the validated version, not later notes-only edits of the
  workspace, so a program that reads `NOTES.md` after such an edit is reported as a mismatch.
