"""Compatibility entrypoint for the root migration verifier; no scientific imports."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('research_provenance', ROOT / 'scripts/verify_research_provenance.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

if __name__ == '__main__':
    raise SystemExit(MODULE.main())
