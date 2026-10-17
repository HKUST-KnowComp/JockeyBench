# Contributing

Use Python 3.13 and the pinned dependencies on both macOS and Linux. On Ubuntu 22.04/24.04, install
`bubblewrap`, `libseccomp2`, `libarchive-tools`, `lsof` and `build-essential` first:

```bash
sudo apt-get update
sudo apt-get install bubblewrap libseccomp2 libarchive-tools lsof build-essential
```

`lhafile` may compile its C extension, especially on Linux ARM. Ensure the interpreter includes
matching Python 3.13 development headers; for a system interpreter these may require its
`python3.13-dev` package from the same package source. Managed interpreters may include the headers.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install --no-build-isolation --no-deps -e .
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
JRDB_NATIVE=1 .venv/bin/pytest -m native  # real macOS or Linux sandbox, fabricated data only
python3 scripts/check_repository.py    # refuses data, keys and local paths in staged files
python3 scripts/check_repository.py --history   # every commit; run before a push
python3 -B scripts/verify_research_provenance.py # original evidence and current migration hashes
```

Linux native tests need permission to create user namespaces. A container or Ubuntu 24.04 AppArmor
policy can prevent this; use a suitable host or runner instead of disabling host security controls
globally. CI keeps Ubuntu 24.04 unit coverage and uses Ubuntu 22.04 for Linux native jobs. Sandbox
startup must fail closed when isolation is unavailable. Development tests require neither private
data nor an `.experiment-host` marker; only a designated experiment host may carry that marker.

Rules that keep results comparable:

* **One package, separate setting contracts.** Install from the root. Default pytest collects `tests/`
  only; historical tests embedded in Research source are not a supported regression suite by themselves.
  Research's public profile is versioned separately from the Program contract described below.
* **Preserve provenance.** Never rewrite the original Research metadata to make current code appear
  unchanged. After reviewing a Research source change, refresh only its current migration record with
  `python3 -B scripts/verify_research_provenance.py --record-migration`, then rerun verification and
  the relevant synthetic tests. The imported Research tree is excluded from blanket formatting to avoid
  gratuitous changes; lint and format new adapters explicitly.

* **Never commit data.** No archives, specification documents, databases, keys, checkpoints, run
  directories or model transcripts. Tests build fabricated archives in memory or in temporary
  directories. `scripts/check_repository.py` runs in CI.
* **The agent-facing contract is frozen.** Any change to a prompt, tool specification, tool response
  or agent-visible error message changes the protocol. `tests/test_specification.py` pins the system
  prompt, the tool specifications, the session task texts and the literal and constant messages of
  raised protocol errors; review messages built at run time and tool-response changes by hand. A protocol change needs a new `PROTOCOL_VERSION`, an
  entry in `protocol/revisions.py` and `CHANGELOG.md`, new pins, and fresh trajectories. Never update
  the pins without recording the change.
* **A checkpoint binds the code.** Editing runtime code changes the code hash, and existing trajectories
  then refuse to resume. Finish or abandon running trajectories before changing code. Platform and
  sandbox changes also require fresh trajectories; never resume or splice one across operating
  systems. CPU fees and predictions are not guaranteed to be identical across platforms.
* **Each default lives in one place.** Run-configuration defaults live in `config.py`; agent and
  provider defaults live in their own module. The CLI imports them rather than restating them.
