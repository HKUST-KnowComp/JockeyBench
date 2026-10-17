"""Linux worker launch policy: an empty bubblewrap filesystem plus runtime mounts.

Only the interpreter, installed Python libraries, system shared libraries and
client are readable. Host home directories, source trees and databases are not
mounted. The worker installs a seccomp filter before executing agent code.
"""

import os
import shutil
from pathlib import Path

# CPython is pinned by pyproject.toml. Explicit directories avoid binding an
# entire prefix (which might also contain the checkout, data or credentials).
PYTHON_LIBRARY = 'python3.13'
SYSTEM_LIBRARIES = (
    '/lib/x86_64-linux-gnu',
    '/usr/lib/x86_64-linux-gnu',
    '/lib/aarch64-linux-gnu',
    '/usr/lib/aarch64-linux-gnu',
    '/lib64',
    '/usr/lib64',
)


def policy(python, base, workspace, client, readonly=False):
    """Return a JSON-serializable launch policy without requiring bwrap to exist.

    Supports ordinary CPython/venv installations on glibc x86_64 and aarch64.
    Paths are bound individually; a missing required runtime fails at launch.
    ``readonly`` makes framework-run strategy files immutable at the mount level.
    """
    python = Path(os.path.abspath(python))
    base, workspace, client = (Path(p).resolve() for p in (base, workspace, client))
    prefix = python.parent.parent
    mounts = {}

    def runtime(path, required=False):
        path = Path(path)
        if required or path.exists():
            # Bind the canonical source at both its real and requested names.
            # This also handles venv interpreter symlinks without exposing bin/.
            source = path.resolve()
            mounts[str(source)] = str(source)
            mounts[str(path)] = str(source)

    runtime(python, required=True)
    for root in dict.fromkeys((base, prefix)):
        for lib in ('lib', 'lib64'):
            runtime(root / lib / PYTHON_LIBRARY)
            runtime(root / lib / (PYTHON_LIBRARY.replace('.', '') + '.zip'))
            for shared in sorted((root / lib).glob('lib*.so*')):
                runtime(shared)
        runtime(root / 'pyvenv.cfg')
    for path in SYSTEM_LIBRARIES:
        runtime(path)
    for path in (
        '/lib/ld-linux-aarch64.so.1',
        '/etc/ld.so.cache',
        '/usr/lib/locale',
        '/usr/share/zoneinfo',
    ):
        runtime(path)
    runtime(client, required=True)
    # Keep the venv launcher a symlink. Binding a second copy of the binary at
    # that name changes ELF $ORIGIN and breaks relocatable CPython's libpython
    # lookup (e.g. uv-managed interpreters).
    symlinks = []
    if python != python.resolve():
        mounts.pop(str(python), None)
        symlinks.append([str(python.resolve()), str(python)])
    return {
        'backend': 'bubblewrap-seccomp-v1',
        'python': str(python),
        'workspace': str(workspace),
        'worker': str(client / 'worker.py'),
        'readonly': bool(readonly),
        'runtime_mounts': [[source, destination] for destination, source in sorted(mounts.items())],
        'runtime_symlinks': symlinks,
    }


def command(spec):
    """Translate the recorded policy into a fail-closed bubblewrap invocation.

    The outer bubblewrap monitor reaps the worker, so Linux wait4 accounting
    includes the worker. Live CPU/RSS checks must inspect its child as well.
    SDK descriptors explicitly inherited by Popen remain open in the worker.
    """
    binary = shutil.which('bwrap')
    if binary is None:
        raise ValueError('Linux isolation requires bubblewrap (bwrap) and libseccomp2; install them before running JockeyBench')
    argv = [
        binary,
        '--unshare-user',
        '--unshare-pid',
        '--unshare-net',
        '--unshare-ipc',
        '--unshare-uts',
        '--as-pid-1',
        '--die-with-parent',
        '--cap-drop',
        'ALL',
    ]
    for source, destination in spec['runtime_mounts']:
        argv.extend(('--ro-bind', source, destination))
    for source, destination in spec['runtime_symlinks']:
        argv.extend(('--symlink', source, destination))
    argv.extend(('--ro-bind' if spec['readonly'] else '--bind', spec['workspace'], spec['workspace']))
    # A fresh procfs exposes only this PID namespace. In particular, there is no
    # host /proc/<broker>/root or /proc/<broker>/fd route around the mount policy.
    argv.extend(('--proc', '/proc', '--remount-ro', '/proc'))
    for device in ('null', 'random', 'urandom'):
        argv.extend(('--dev-bind', '/dev/' + device, '/dev/' + device))
    # No writable /tmp or /dev/shm escape hatch: scratch files count toward the
    # workspace quota. Individual device mounts remain usable for null/random.
    argv.extend(('--remount-ro', '/', '--chdir', spec['workspace']))
    argv.extend(('--', spec['python'], '-B', '-s', spec['worker']))
    return argv
