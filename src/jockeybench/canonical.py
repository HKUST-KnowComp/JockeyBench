"""Canonical JSON encoding and content hashes used for every receipt."""

import hashlib
import json
from pathlib import Path

PACKAGE = Path(__file__).parent


def packed(value):
    """Canonical UTF-8 JSON: sorted keys, no whitespace, finite numbers only."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(packed(value)).hexdigest()


def file_hash(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def code_hash():
    """Identity of the runtime source bound into every trajectory.

    The data importer is excluded: it never runs during a trajectory.
    """
    files = sorted(p for p in PACKAGE.rglob('*.py') if 'importer' not in p.relative_to(PACKAGE).parts)
    return digest({str(p.relative_to(PACKAGE)): file_hash(p) for p in files})
