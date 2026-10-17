# Reproducing the Program setting

This document covers the Program setting. The unified Active Research public profile has a separate
[guide](RESEARCH.md); the original source-drop validation results do not establish reproduction of
its historical experiments with the migrated profile.

## Configuration

`configs/paper.json` is the main-cohort protocol, and it equals the defaults of `jockeybench.config.Config`:

| Setting | Value |
| --- | --- |
| Window | ten race days from 2024-01-01 (`smoke_days: 10`, `schedule_mode: full`: every eligible race of each day) |
| Protocol / projection | `economic-v1` / `objective-v5` |
| Tariff | `score-yen-v2`, multiplier 0.5, hit bonus 100, smoothing 0.02 |
| Allowances | 40 model calls in warmup and 20 per reflection (5 reserved for repair); 1,000 tool calls per phase; 400,000 / 100,000 evidence cells |
| Machine ceilings | 1,800 s per job, 120 CPU-s per prediction job, 10 GiB RSS, 5 GiB storage per runs root, 4 numerical threads |

Reference programs: `--reference R0|R1|R2|R3` (default 87 support races for the fit). The paper's
R2 reference reached a smoothed log gain of 0.343 on its 324-race window.

## Relation to the runtime of the paper

The paper's trajectories were produced by an internal v0.6.0 runtime that also contained earlier
protocols. This repository is that runtime reduced to the published protocol:

* **Removed:** the earlier `hit-only-v1` protocol with its reset/feedback/adaptive arms, offline
  baseline agent and JSONL server; the `accuracy-v1` track; arm B; projection `objective-v4`; tariffs
  `score-yen-v1` and `no-cost-v1`; the `spread` schedule; the undocumented `sandbox-check` command;
  dead helpers. The importer no longer builds the research-only "expanded" tables (odds snapshots,
  expert indices, entity links, free-text views) that the runtime never reads.
* **Corrected (protocol 0.6.1):** agent-visible statements that contradicted the rules the runtime
  enforces ([CHANGELOG.md](../CHANGELOG.md), `protocol/revisions.py`). Examples:
  * probabilities were called optional although they are required;
  * the hit bonus was called independent of the probabilities;
  * the response ceiling was stated wrongly;
  * a rejected program's files were said to persist.

  The paper's trajectories were run with the 0.6.0 wording. The rules themselves were the same:
  for example, a prediction without probabilities was rejected either way.
* **Fixed:** defects of the v0.6.0 runtime. Each one stopped or crashed the run, made an audit report
  a false mismatch, or prevented a start, so none changes a trajectory that ran to completion without
  being resumed after such a stop ([CHANGELOG.md](../CHANGELOG.md) lists the details):
  * `reexecute-audit` quoted every fee as zero, so a program's `max_spend` never bound during
    re-execution. It now quotes the live fees and still books nothing.
  * A strategy exception whose class name contained "storage" stopped the whole run as if managed
    storage had failed. Only the framework's two storage failures stop a run now.
  * A `NOTES.md` that was not valid UTF-8 made every later session crash.
  * The sandbox profile refused interpreters reached through a symlink chain, such as Homebrew venvs,
    and any path with non-ASCII characters. The profile is unchanged for interpreters reached without
    `..` hops or symlinked directories; for others only the metadata allowances of the hops change.
  * Malformed configuration files raised tracebacks instead of exit 2.
* **Unchanged:** everything else an agent or the evaluator observes. Three limits inherited from the
  hit-only protocol (`feedback_memory_items`, `feedback_memory_bytes`, `format_repairs`) have no
  effect, but are still shown to agents so that the contract changes only where it was corrected.
  They are no longer part of the bound configuration.

### Verification

1. **Contract pins** (`tests/test_specification.py`). The 0.6.1 system prompt, tool specifications and
   session task texts are pinned, and so is every raised protocol-error message that is a literal or
   a string constant, together with the runtime-built allowance message and generic fallback. Reverting the documented corrections
   makes the system prompt, the tool specifications and the session task texts equal those of the
   v0.6.0 runtime byte for byte.
2. **Behavioural fingerprint.** A harness ran both runtimes on the same fabricated archive and
   recorded everything observable. It drives the internal v0.6.0 runtime and is therefore not
   distributed. It recorded:
   * reference trajectories R0–R3 in arm A, and R2 in arm S from the shared warmup;
   * a scripted model trajectory in both arms. It covers a failed validation and its repair, an
     explicit uniform choice, nine kinds of rejected request and context trimming.
   * every provider request body (system prompt, tool list, every tool response), every sealed
     prediction, score, ledger entry, report, and both audits.

   CPU time and the sandbox wall clock were pinned. Reference trajectories are identical. For the
   model trajectory, the corrected sentences change the length of metered responses and therefore
   the byte counters, and the corrected repair task changes the session key from which operation and
   ledger ids are derived. After reverting the corrections and neutralising the byte counters and
   those derived ids, the two fingerprints are identical. The only other difference is the three
   inherited limits leaving the bound configuration.
3. **Licensed-data acceptance.** The R2 reference on the paper window was run against the existing
   private database and compared race by race with the acceptance run of the original release. See
   below.

## What does not reproduce exactly

* **Model behaviour.** Provider outputs are not deterministic even at temperature 0, and served models
  change. Compare distributions over independent repetitions, not single trajectories.
* **Your data.** A new purchase may differ in coverage or revisions from the paper's archive, so the
  schedule and race count can differ. The pseudonymisation key is private: identifiers differ, and
  programs that hash them (R3 does, for pedigree tokens) compute slightly different features.
* **Your environment.** Every trajectory records, and shows agents, the Python version, the OS and its
  release, the thread count, the hash seed, and whether numpy, scipy, sklearn, pandas and statsmodels
  can be imported. It does not record library versions, so resume and the audits do not detect a
  changed numpy or scipy. Use the pinned lock file: other library versions can change floating-point
  results.
* **Wall-clock-dependent paths.** Job timeouts and the `seconds` counters depend on the machine.
  Prices use measured CPU time, not wall time.

## Acceptance on licensed data

Run on 2026-10-16 on the experiment host (macOS 27, Python 3.13.2, the pinned lock) with this
release's final tree (runtime code hash `ee26a2ce…`). It used the same interpreter environment and the
same private database as the acceptance run of the original release. No model calls were made.

| Check | Result |
| --- | --- |
| Unit and native tests | 181 + 19 passed |
| R2 on the paper window (10 days, 324 races) | completed; 324 `predictor` deliveries; clean trajectory |
| Compared with the original runtime's R2 run, race by race | identical schedule, sealed predictions, scores, feedback, 2,136 non-CPU ledger entries, 1,079 audit rows, repeat-read index, strategy versions, report fields |
| Only difference | measured CPU fees: 4,932.81 vs 4,932.72 score-yen in total |
| `replay-audit` | 1,079 responses and 324 scores verified |
| `reexecute-audit` | 324 / 324 predictions reproduced (tolerance 1e-9) |
| R2 aggregates | log revenue 11,122.96; hit revenue 5,284.62; mean smoothed log gain 0.3433; top-1 23.8%; top-3 precision 42.0% |
| Importer, January 2024 (288 races, 4,088 runner rows) | `races` and `entries` identical to both the original importer's output and the paper's database; race ids identical to the original importer's |
