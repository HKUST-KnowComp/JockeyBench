"""Key the pseudonymous race identifiers with a private namespace.

A built database carries public, unkeyed race tokens. ``anonymize`` copies it,
stores the private 256-bit key in the copy's metadata, re-keys every race token
with HMAC-SHA256 and marks the copy ready. The runtime derives every other token (horse, jockey,
trainer, pedigree) with the same key on the fly, so names and registration
numbers never reach an agent. Pseudonymization reduces direct lookup; it does
not guarantee unlinkability.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path

VERSION = 'jrdb-pseudonymization-v4'  # the token scheme read by jockeybench.data.source
TOKEN = re.compile(r'([a-z][a-z_]*?)_([0-9a-f]{14})')


def identifier(key, kind, value, length=24):
    payload = json.dumps([VERSION, kind, value], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()[:length]


def rekey(key, token):
    """``kind_<14 hex>`` -> ``kind_<24 hex>`` keyed by the private namespace."""
    match = TOKEN.fullmatch(token)
    if not match:
        raise ValueError('not an unkeyed pseudonymous token')
    return match[1] + '_' + identifier(key, 'entity', token)


def load_or_create_key(path):
    """A 32-byte key file with mode 600; created exclusively when absent."""
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write(secrets.token_bytes(32))
    if path.stat().st_mode & 0o077:
        raise ValueError('namespace key must have mode 600')
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError('invalid private namespace key')
    return key


def implementation_hash():
    root = Path(__file__).parent
    return hashlib.sha256(
        json.dumps({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob('*.py'))}, sort_keys=True).encode()
    ).hexdigest()


def anonymize(source, output, key_path):
    """Write a keyed copy of ``source`` to the fresh path ``output``."""
    source, output = Path(source), Path(output)
    if output.exists():
        raise ValueError('Output database exists; choose a fresh path')
    key = load_or_create_key(key_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    con = sqlite3.connect(output)
    try:
        src.backup(con)
        src.close()
        output.chmod(0o600)
        receipt = {
            'version': VERSION,
            'status': 'ready',
            'identity': 'HMAC-SHA256 with private 256-bit namespace key; 96-bit entity IDs',
            'namespace_commitment': hashlib.sha256(key).hexdigest(),
            'implementation_hash': implementation_hash(),
            'text_views': 'none; free text is kept privately and never projected to agents',
            'claim': 'pseudonymization and reduced direct lookup; not guaranteed unlinkability',
        }
        with con:
            for rid, token in con.execute('SELECT race_id,public_id FROM expanded_race_ids').fetchall():
                con.execute('UPDATE expanded_race_ids SET public_id=? WHERE race_id=?', (rekey(key, token), rid))
            con.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('anonymization_private_key', key.hex()))
            con.execute(
                'INSERT OR REPLACE INTO metadata VALUES(?,?)',
                ('anonymization_receipt', json.dumps(receipt, ensure_ascii=False, separators=(',', ':'))),
            )
    finally:
        con.close()
    Path(str(output) + '.anonymization.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    return {'status': 'prepared', 'output': str(output)}
