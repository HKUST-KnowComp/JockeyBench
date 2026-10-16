# Review draft boundaries

- This folder contains the user's Active Research extension only. `jrdb_lh` and
  the historical framework archive are external dependencies; do not vendor,
  modify, or copy their source here.
- Preserve copied scientific Python files byte-for-byte unless the user later
  explicitly authorizes a scientific change. Verify against
  `metadata/source-manifest.json`.
- The main package is under `src/`; historical files are archived under
  `history/` and excluded from installation and the default upload candidate.
  Preserve the partition metadata against `metadata/initial-source-manifest.json`.
  When local `history/` is present, verify all historical bytes; it is deliberately
  absent from the publication candidate and not required for its checks.
- Run only the static/package checks documented in docs/TESTING.md in this source drop.
  Scientific tests require separately supplied framework fixtures and in some
  cases the original Mini host. Do not claim full regression without those.
- Never call providers, launch/resume experiments, create an experiment-host
  marker, or copy datasets/checkpoints/credentials as part of packaging.
- The user explicitly authorized the code/documentation upload to
  HKUST-KnowComp/JockeyBench on 2026-10-17, followed by the reviewed README.md.
  The publication now contains 149 files, including README.md.
  Further publication changes still require applicable user authorization;
  no model/provider or scientific run is authorized by this source drop.
