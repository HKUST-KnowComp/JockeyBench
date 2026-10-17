# Active Research provenance

This directory contains the evidence and documentation from the original ActiveResearch source drop.
It is **not a separately installable package**. The current implementation lives at
[`src/jockeybench/research`](../../src/jockeybench/research), under the single root `pyproject.toml`.
Use the root [README](../../README.md) and [Research guide](../../docs/RESEARCH.md) for current commands.

## Original records

The original `metadata/source-manifest.json`, `initial-source-manifest.json`, `resource-manifest.json`,
`dependency-map.json`, `package-layout.json`, file lists and `release-validation.json` are retained.
Their paths and test results describe the old source drop, not the current directory layout.
The old packaging declaration is retained as `metadata/source-drop-pyproject.toml`, and the original
README is [SOURCE-DROP-README.md](docs/SOURCE-DROP-README.md).

The original inventory accounts for 155 Python files: 128 published files and 27 excluded historical
files whose source bytes are not distributed. The initial hashes are historical evidence. Namespace,
shared-engine and deployment changes in the unified package mean current files need not have those
same hashes. Historical regression counts do not validate these changes.

## Current migration record

`metadata/source-drop-evidence.json` protects the preserved metadata. The separate
`metadata/migration-manifest.json` records each published file's old and new paths, original SHA-256,
current SHA-256 and change status. It separately lists new Research files and non-Python resources.
`metadata/current-dependency-map.json` is a fresh static import graph for the unified package.
It is not substituted for the historical dependency map.

From the repository root:

```bash
python -B scripts/verify_research_provenance.py
```

The verifier checks preserved metadata, current source hashes, syntax, file coverage, internal import
resolution and the absence of imports from the old `active_research` and `jrdb_lh` namespaces. It does
not import scientific code, load data, invoke providers, run policies or prove behavioral equivalence.
After reviewing an intentional source change, maintainers refresh the current migration record with
`python -B scripts/verify_research_provenance.py --record-migration`, then rerun the verifier and the
applicable synthetic tests. Recording never rewrites original source-drop evidence.

The remaining documents in `docs/` are historical records, including their old commands, paths,
external-dependency requirements and original macOS validation scope. They are not deployment
instructions for the public `jockeybench-research-v1` profile.
