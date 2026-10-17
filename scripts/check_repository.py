"""Refuse files that could carry licensed data, credentials or local paths.

Checks what git records under this project's directory (the repository root or a subdirectory of it),
not the working tree:

    python3 scripts/check_repository.py            # the index: what the next commit contains
    python3 scripts/check_repository.py --history  # every file of every commit (full clone); run before a push

CI runs the history check on a full clone.
"""

import hashlib
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_DIRS = {'data', 'private', 'raw', 'runs', 'logs', 'checkpoints', 'artifacts'}  # at any depth, any case
PACKAGE_DATA = ('src', 'jockeybench', 'data')  # the archive-access subpackage is code, not data
# The same code namespace before the unified repository-root migration.
FORMER_PACKAGE_DATA = ('ProgramSetting', *PACKAGE_DATA)
# This exact already-published historical source contains only a former machine
# path. The exception never applies to current files, changed bytes or secrets.
HISTORICAL_PERSONAL_PATHS = {
    'ActiveResearch/src/active_research/llm/agent.py': '8d9fe3c88d8a7194c506a66fdb56bf0a85cb047cac1ba19c1adfd03186297a5c',
}
PERSONAL_PATH = re.compile(rb'/(?:Users|home)/[A-Za-z0-9._-]+')
ALLOWED_SUFFIXES = {'.py', '.md', '.toml', '.txt', '.json', '.yml', '.cff', '.in'}
ALLOWED_NAMES = {'.gitignore', '.python-version', 'LICENSE'}
ARCHIVE_NAME = re.compile(r'[A-Za-z]{2,4}(?:_\d{4}|\d{6})(?:\.txt)?\.(?:lzh|zip|txt)', re.I)  # JRDB file naming
MAX_BYTES = 256 * 1024
SECRETS = [
    re.compile(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----'),
    re.compile(rb'(?:sk-(?:proj-|ant-)?|sk_live_|gh[pousr]_|github_pat_|xox[baprs]-|hf_|AIza)[A-Za-z0-9_-]{24,}'),
    re.compile(rb'AKIA[A-Z0-9]{16}'),
    re.compile(rb'https?://[^\s/:]+:[^\s/@]+@'),
    re.compile(
        rb'[A-Za-z0-9._%+-]+@(?!example\.)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:com|org|net|edu|gov|io|ai|dev|app|co|cn|jp|uk|de)\b'
    ),
    re.compile(rb'\.ts\.net\b'),
]
FIXED_WIDTH_RECORD = re.compile(rb'^[0-9A-Z ]{200,}\r?$', re.M)  # an archive record pasted as text


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True).stdout


def entries(records, object_field):
    """``(path, mode, object)`` from the ``-z`` output of ``ls-files -s`` or ``ls-tree -r``."""
    for record in filter(None, records.split(b'\0')):
        meta, path = record.split(b'\t', 1)
        fields = meta.decode().split()
        yield path.decode('utf-8', 'surrogateescape'), fields[0], fields[object_field]


def recorded_files(top, prefix, history):
    """``(path, label, mode, object)`` under ``prefix`` for the index, or for every commit a push can send.

    Paths are relative to the project directory.
    """
    strip = len(prefix) + 1 if prefix else 0
    if not history:
        for path, mode, blob in entries(git(top, 'ls-files', '-s', '-z', '--', prefix or '.'), 1):
            yield path[strip:], path, mode, blob
        return
    seen = set()
    for commit in git(top, 'rev-list', '--exclude=refs/stash', '--all').decode().split():
        for path, mode, blob in entries(git(top, 'ls-tree', '-r', '-z', commit, '--', prefix or '.'), 2):
            if (path, blob) not in seen:
                seen.add((path, blob))
                yield path[strip:], f'{path}@{commit[:8]}', mode, blob


def check(root, path, mode, blob, history=False):
    """The reason this recorded file may not be committed, or ``None``."""
    rel = PurePosixPath(path)
    directories = [d.lower() for d in rel.parts[:-1]]
    if rel.parts[: len(PACKAGE_DATA)] == PACKAGE_DATA:
        directories = directories[len(PACKAGE_DATA) :]
    elif history and rel.parts[: len(FORMER_PACKAGE_DATA)] == FORMER_PACKAGE_DATA:
        directories = directories[len(FORMER_PACKAGE_DATA) :]
    if mode == '120000':
        return 'symlink'
    if mode == '160000':
        return 'submodule'
    if not path.isprintable() or any('\udc80' <= ch <= '\udcff' for ch in path):
        return 'path is not printable UTF-8'
    if BLOCKED_DIRS & set(directories):
        return 'path inside a data or run directory'
    if ARCHIVE_NAME.fullmatch(rel.name):
        return 'named like a JRDB archive'
    if rel.suffix not in ALLOWED_SUFFIXES and rel.name not in ALLOWED_NAMES:
        return 'file type not allowed in the repository'
    if int(git(root, 'cat-file', '-s', blob)) > MAX_BYTES:
        return 'file larger than 256 KiB'
    content = git(root, 'cat-file', 'blob', blob)
    try:
        content.decode('utf-8')
    except UnicodeDecodeError:
        return 'not UTF-8 text'
    if any(pattern.search(content) for pattern in SECRETS):
        return 'possible credential, address or personal path'
    if PERSONAL_PATH.search(content):
        known_original = history and HISTORICAL_PERSONAL_PATHS.get(path) == hashlib.sha256(content).hexdigest()
        if not known_original:
            return 'possible credential, address or personal path'
    if FIXED_WIDTH_RECORD.search(content):
        return 'looks like a fixed-width archive record'
    return None


def problems(root=ROOT, history=False):
    top = Path(git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve()
    if top == Path.home().resolve():
        yield '(repository)', 'the enclosing git repository is the home directory, not this project'
        return
    if history and git(top, 'rev-parse', '--is-shallow-repository').strip() == b'true':
        yield '(repository)', 'shallow clone; fetch the full history first'
        return
    prefix = Path(root).resolve().relative_to(top).as_posix()
    prefix = '' if prefix == '.' else prefix
    files = list(recorded_files(top, prefix, history))
    if not files:
        yield '(repository)', 'nothing recorded; stage or commit the files to check'
    for path, label, mode, blob in files:
        reason = check(top, path, mode, blob, history=history)
        if reason:
            yield label, reason


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if set(argv) - {'--history'}:
        print(__doc__)
        return 2
    try:
        found = list(problems(history='--history' in argv))
    except (FileNotFoundError, subprocess.CalledProcessError):
        print('FAIL: not a git checkout; this check reads what git records')
        return 2
    for name, reason in found:
        print(f'{name}: {reason}')
    print('FAIL' if found else 'PASS', f'({len(found)} problems)')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
