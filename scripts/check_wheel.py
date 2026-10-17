"""Check the built distribution and its isolated CLI imports, without data or providers."""

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    'jockeybench/research/daily_reflection_budget.json',
    'jockeybench/research/cli.py',
    'jockeybench/research/public.py',
    'jockeybench/research/portable_warmup.py',
    'jockeybench/sandbox/client/worker.py',
    'jockeybench/sandbox/client/sdk.py',
    'jockeybench/sandbox/client/linux_guard.py',
}
PROBE = '''
import importlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import jockeybench
assert pathlib.Path(jockeybench.__file__).resolve().is_relative_to(root)
importlib.import_module('jockeybench.research.public')
from jockeybench.cli import main
setting = sys.argv[2]
try:
    main([setting, '--help'])
except SystemExit as exc:
    assert exc.code == 0, exc.code
print(json.dumps({'setting': setting, 'installed_version': jockeybench.__version__, 'from_wheel': True}))
'''


def check(wheel):
    expected = {path.relative_to(ROOT / 'src').as_posix(): path.read_bytes() for path in (ROOT / 'src/jockeybench').rglob('*.py')}
    resource = 'jockeybench/research/daily_reflection_budget.json'
    expected[resource] = (ROOT / 'src' / resource).read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or name.startswith(('active_research/', 'jrdb_lh/')):
                raise ValueError('Unexpected wheel path: ' + name)
        if not REQUIRED <= names:
            raise ValueError('Required runtime files missing: ' + ', '.join(sorted(REQUIRED - names)))
        for name, content in expected.items():
            if name not in names or archive.read(name) != content:
                raise ValueError('Missing or changed packaged source/resource: ' + name)
        with tempfile.TemporaryDirectory(prefix='jockeybench-wheel-') as folder:
            archive.extractall(folder)
            for setting in ('program', 'research'):
                result = subprocess.run(
                    [sys.executable, '-I', '-c', PROBE, folder, setting],
                    cwd=folder,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                print(result.stdout.strip().splitlines()[-1])
    return {'passed': True, 'wheel': wheel.name, 'verified_source_and_resource_files': len(expected), 'provider_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    args = parser.parse_args()
    try:
        result = check(args.wheel.resolve())
    except (OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}))
        if isinstance(exc, subprocess.CalledProcessError):
            print(exc.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
