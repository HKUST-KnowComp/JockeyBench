# Contributing

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install --no-build-isolation --no-deps -e .
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest                       # add JRDB_NATIVE=1 on macOS to run sandbox jobs
python3 scripts/check_repository.py    # refuses data, keys and local paths in staged files
python3 scripts/check_repository.py --history   # every commit; run before a push
```

Rules that keep results comparable:

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
  then refuse to resume. Finish or abandon running trajectories before changing code.
* **Each default lives in one place.** Run-configuration defaults live in `config.py`; agent and
  provider defaults live in their own module. The CLI imports them rather than restating them.
