# JockeyBench development

This repository is one installable project with two settings: Program and Active
Research. Runtime code is in `src/jockeybench`; the public Research entry point is
`jockeybench research`. Its `jockeybench-research-v1` deployment profile is distinct
from the historical fixed-run profile retained in the imported research modules.

The 2026-10-17 user request authorizes the runtime integration, including adapting
imports and interfaces and running synthetic fixture tests. The old packaging-only
instructions in `provenance/active-research/AGENTS.md` describe the original source
drop. Preserve those provenance records; do not present migrated hashes as original
scientific source hashes.

Follow the parent workspace's experiment rules: on this MacBook, edit code/docs,
audit, and run synthetic in-memory tests only. Real data, doctor/replay against a
real source, and model experiments run only on the designated Mac mini. Never copy
datasets, checkpoints, credentials or host markers into code copies or publication.
Paid experiments still require explicit authorization. A package migration is not
authorization to rerun an experiment or resume a stopped trajectory.

Run the root test suite and native sandbox fixture tests, lint/format checks, and
the repository/provenance verifiers before publishing. Keep Program protocol pins
and Research acquisition, feedback and scoring contracts distinct. Any change to
source or environment needs new run identities and a new source binding.
