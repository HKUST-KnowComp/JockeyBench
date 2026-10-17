# Historical evidence boundaries

- This directory is provenance for the former ActiveResearch source drop, not an independent package.
- Preserve the original metadata files covered by `metadata/source-drop-evidence.json`; do not rewrite
  historical hashes, test counts or archive identities to describe the migrated implementation.
- Current source is in `src/jockeybench/research/` at the repository root. Its namespace/shared-runtime
  migration was explicitly authorized by the user on 2026-10-17.
- Record old/new paths and original/current hashes in the separate migration manifest. Refresh it only
  after intentional source changes are reviewed; verify it with the root static checker.
- Do not import historical documentation's old launch commands as authorization. Packaging does not
  authorize provider calls, real-data access, experiment-host markers, trajectory launches or resumes.
- Root synthetic and static tests are the current verification entrypoints. Old validation results
  belong to the original source drop and do not prove that the migrated profile reproduces them.
