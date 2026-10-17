"""``jockeybench data``: prepare locally purchased archives. No network or credential handling."""

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path


def window(start, end):
    """Import windows are confined to 2015-2025; 2026 data are a sealed holdout."""
    if not date(2015, 1, 1) <= date.fromisoformat(start) <= date.fromisoformat(end) < date(2026, 1, 1):
        raise ValueError('Import window must be within 2015–2025; 2026 data are disabled')


def manifest(raw, start, end, fetched_at):
    """Hash the archives of the allowed years; the operator supplies the actual acquisition time."""
    window(start, end)
    if datetime.fromisoformat(fetched_at).tzinfo is None:
        raise ValueError('fetched-at needs an explicit timezone')
    from .archives import TYPES, archive_year

    raw = Path(raw)
    output = raw / 'manifest.jsonl'
    if output.exists():
        raise ValueError('Manifest already exists; retain acquisition provenance or use a fresh input directory')
    rows = []
    for folder in sorted({v[0] for v in TYPES.values()}):
        for path in sorted((raw / folder).glob('*')):
            year = archive_year(path)
            if path.suffix.lower() not in {'.zip', '.lzh'} or year is None or not int(start[:4]) <= year <= int(end[:4]):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError('Archive must be a regular file')
            with path.open('rb') as stream:
                checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            rows.append(
                {'relpath': path.relative_to(raw).as_posix(), 'sha256': checksum, 'size': path.stat().st_size, 'fetched_at': fetched_at}
            )
    if not rows:
        raise ValueError('No supported archives in requested years')
    with output.open('x') as stream:
        for row in rows:
            stream.write(json.dumps(row) + '\n')
    output.chmod(0o600)
    return {'archives': len(rows), 'manifest': str(output)}


def add_arguments(parser):
    sub = parser.add_subparsers(dest='data_command', required=True)
    sub.add_parser('layouts', help='list the required archive directories and specifications; reads nothing')
    p = sub.add_parser('manifest', help='hash acquired archives into raw/manifest.jsonl')
    p.add_argument('--raw', required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True)
    p.add_argument('--fetched-at', required=True, help='actual acquisition timestamp, including timezone')
    p = sub.add_parser('build', help='decode archives into a fresh private database')
    for name in ('raw', 'docs', 'database', 'start', 'end'):
        p.add_argument('--' + name, required=True)
    p = sub.add_parser(
        'anonymize', help='derive the keyed database the runtime reads (keeps raw identifiers and embeds the key; keep it private)'
    )
    p.add_argument('--source', required=True, help='database written by build')
    p.add_argument('--output', required=True, help='fresh path for the keyed copy')
    p.add_argument('--key', required=True, help='private 32-byte namespace key (created with mode 600 if absent)')


def execute(args):
    if args.data_command == 'layouts':
        from .archives import TYPES

        return {c: {'archive_directory': d, 'specification': spec} for c, (d, spec) in TYPES.items()}
    from ..cli import require_experiment_host

    require_experiment_host()
    os.umask(0o077)
    if args.data_command == 'manifest':
        return manifest(args.raw, args.start, args.end, args.fetched_at)
    if args.data_command == 'build':
        window(args.start, args.end)
        if Path(args.database).exists():
            raise ValueError('Output database exists; choose a fresh path')
        if not (Path(args.raw) / 'manifest.jsonl').is_file():
            raise ValueError('An acquisition manifest is required')
        from .build import build_database

        result = build_database(args.raw, args.docs, args.database, args.start, args.end)
        if not result['counts'].get('races'):
            raise ValueError('No races imported; inspect archive coverage and build receipt')
        return result
    paths = [Path(getattr(args, k)).resolve() for k in ('source', 'output', 'key')]
    if len(set(paths)) != 3:
        raise ValueError('source, output and key must be distinct paths')
    from .pseudonymize import anonymize

    return anonymize(args.source, args.output, args.key)
