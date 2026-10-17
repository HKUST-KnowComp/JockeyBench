# Changelog

## 1.2.0 — One package, Program and Active Research settings

* Move the runtime to the repository root and integrate Active Research under
  `jockeybench.research`. Install once and select `jockeybench program` or `jockeybench research`;
  existing commands without a setting prefix continue to use Program.
* Add the public `jockeybench-research-v1` lifecycle with Adaptive and matched Frozen arms, shared
  data/ledger/scoring components, configurable compatible providers, fresh-run bindings and saved
  reports/audits. The supported public path no longer depends on an external `jrdb_lh` checkout.
* Add `jockeybench-research-warmup-v1`, using historical support through the installed engine.
  This deliberately replaces the public entrypoint's dependency on the original fixed source
  archive. The archive identity, earlier regression counts and historical fixed-run profiles
  remain provenance; they do not certify equivalent forecasts, requests or scores in this profile.
* Preserve the original source-drop metadata and record migrated paths plus original/current
  hashes separately. Add static provenance validation and root-level synthetic coverage, without
  collecting the embedded legacy scientific tests as the supported default suite.
* Keep the original Program contract and its pins separate from Research's public profile.
  Adding both settings to the same source identity requires fresh trajectories. No old checkpoint
  may be resumed or spliced under the new code/environment binding.
* Preserve the scope of the existing Program MIT license; the Research source migration does not
  create a new license grant. See [license scope](docs/LICENSE-SCOPE.md).

This release does not rerun the paper, validate private JRDB data or claim live-provider acceptance.
The public Research profile is a versioned deployment change, not merely a folder rename.

## 1.1.0 — Linux runtime support, protocol 0.6.1

* Add a Linux sandbox using bubblewrap namespaces, restricted mounts and a libseccomp syscall
  filter. Missing dependencies or unavailable isolation refuse execution; there is no unsandboxed
  fallback. The existing macOS sandbox remains available.
* Allow the importer and runtime on designated Linux experiment hosts, retaining the
  `.experiment-host` guard. Document Ubuntu dependencies, including the compiler and Python headers
  needed when `lhafile` builds from source.
* Record machine architecture in the execution environment. Code and environment bindings require
  fresh trajectories; existing macOS checkpoints must not be resumed or spliced on Linux.
* Add Ubuntu native sandbox coverage to CI using fabricated data. Platform support does not imply
  equal predictions, CPU fees or scores across operating systems or hardware.

The scoring rules, information projection, tariffs and protocol remain unchanged. This release
applies to `ProgramSetting/`; `ActiveResearch/` still depends on its external harness.

## 1.0.0 — protocol 0.6.1

First public release: the runtime of the paper's Program setting, reduced to the published protocol
(economic-v1, objective-v5, score-yen-v2 ×0.5, arms A and S, reference programs R0–R3).
The code is released under the MIT License; JRDB data are not covered (see NOTICE.md).

### Protocol 0.6.1

Agent-visible statements that contradicted the enforced rules were corrected. The rules, scoring
and every other part of the contract are unchanged from 0.6.0, the version used for the paper's main
cohort. `src/jockeybench/protocol/revisions.py` lists every pair of old and new wording. The tests
prove that reverting them reproduces the 0.6.0 system prompt, tool specifications and session task
texts byte for byte; tool responses and error messages were compared by a fingerprint harness
([docs/REPRODUCING.md](docs/REPRODUCING.md)).

* **Probabilities are required.** The program interface said, in one paragraph, that
  probabilities are *optional*, that they are *mandatory*, and how they must look *when supplied*.
  The `submissions` tool and a validation error also called them optional. The runtime has always
  rejected a prediction without probabilities. All four statements now say that `ranking` and
  `probabilities` are both required.
* **Hit bonus and equal probabilities.** The scoring section said the hit bonus is independent of
  the submitted probabilities. In fact a race where the current validated program returns valid
  probabilities that are all exactly equal is sealed as `silent_uniform` and earns no hit bonus. The
  scoring section and the uninformative-output rule now say so.
* **Response ceiling.** Direct tool responses were said to be limited by `response_bytes` (1 MiB),
  and that limit was said to shrink with the remaining context. The real ceiling is fixed at
  `min(response_bytes, context_bytes // 3)` (87,381 bytes). A data request that first fails its
  `max_spend` check keeps its call fee. A rejected `compute` still pays its CPU and nested SDK fees, and
  its file changes are rolled back; the `Undeliverable` error now says so too. Because the ceiling is never
  lowered, the session rules now say that a session ends when dropping old rounds cannot leave room for
  the next request or for a response at the ceiling.
* **`save_strategy` arguments.** `files` is required unless `mode=uniform`, which takes no files. At
  most 64 files are accepted per call, and the call's JSON arguments are limited to `response_bytes`.
* **Workspace files.** The text promised that training arrays on disk are rejected. In fact only flat
  `.py/.json/.txt/.md/.bin/.pkl` files are allowed, every `.py` file must parse (`strategy.py` must define
  `predict(sdk, context)`), and only non-finite JSON and JSON sample caches are refused. A rejected
  `compute` or save is rolled back as a whole. The matching error now lists the allowed types.
* **Rejected candidates.** Validation checks the program left in the workspace when a session ends. If
  it rejects that program, the program is discarded together with the files saved with it, `NOTES.md`
  included, and the last accepted workspace is restored. The session rules, the failure matrix and the
  repair task now say so; the repair task also says that `current_strategy_files` show that
  workspace's `.py/.json/.txt/.md` files.
* **`statistics` scope.** `statistics` defaults to `scope=support`, unlike the other page tools; its
  description now says so.

Lower-priority wording issues found by the same audit are not part of this release.

### Fixes

Defects inherited from the v0.6.0 runtime. Each one stopped or crashed a run, made an audit report a
false mismatch, or prevented a start, so none changes a trajectory that ran to completion without
being resumed after such a stop.

* `reexecute-audit` quoted every fee as zero, so `max_spend` never bound during re-execution and a
  program that relied on it was reported as a mismatch. It now quotes the live fees and books nothing.
* A strategy exception whose class name contained "storage" (the sandbox forwards only the class
  name, so the message never mattered) stopped the whole run as if managed storage had failed. Only
  the framework's two storage failures (quota, symlink) stop a run now. A v0.6.0 run resumed after such
  a stop recorded "Interrupted validation retained" or "Interrupted attempt retained" where 0.6.1
  records the strategy's own error.
* A `NOTES.md` that was not valid UTF-8 crashed every later session; it is now decoded leniently.
* The sandbox profile denied interpreters reached through a symlink chain (Homebrew venvs, venvs
  inside a symlinked folder) and every path with non-ASCII characters. The profile is unchanged for an
  interpreter reached without `..` hops or symlinked directories, such as a venv or the framework
  binary. For a launcher like python.org's `/usr/local/bin/python3` only the metadata allowances of
  its symlink hops change: they are now written as normalised paths, and the recorded
  `policy_digest` differs.
* Malformed configuration files (unknown keys, values of the wrong type) raised tracebacks instead of
  exit 2.

Defects of the importer and tooling written for this release: `data manifest` and `data build` used
different archive-name rules, and `data build` failed when its output directory was missing.
`scripts/check_repository.py` now checks what git records (the index, or every commit with
`--history`), not the working tree; CI runs the history check on a full clone.

### Removed

The `spread` schedule mode and the `sandbox-check` command, both unused by the paper protocol, and
dead code left over from the removed protocols. The entry points are the `jockeybench` script,
`python -m jockeybench` and `python -m jockeybench.cli`.
