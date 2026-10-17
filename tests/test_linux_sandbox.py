"""Linux launch policy checks and real isolation using fabricated fixtures only."""

import os
import platform
from dataclasses import replace
from pathlib import Path

import pytest

from conftest import native
from jockeybench.errors import JobFailed
from jockeybench.sandbox import linux
from jockeybench.sandbox.executor import linux_process_usage


def test_missing_bubblewrap_never_falls_back_to_plain_python(monkeypatch):
    monkeypatch.setattr(linux.shutil, 'which', lambda name: None)
    with pytest.raises(ValueError, match='requires bubblewrap'):
        linux.command({})


def test_policy_binds_runtime_and_workspace_without_exposing_prefix_siblings(tmp_path, monkeypatch):
    base, venv, workspace, client = [tmp_path / name for name in ('base', 'venv', 'workspace', 'client')]
    for root in (base, venv):
        (root / 'lib' / 'python3.13').mkdir(parents=True)
        (root / 'bin').mkdir()
    interpreter = base / 'bin' / 'python3.13'
    interpreter.touch()
    (venv / 'bin' / 'python').symlink_to(interpreter)
    (venv / 'pyvenv.cfg').write_text('home = ' + str(base / 'bin'))
    workspace.mkdir()
    client.mkdir()
    (base / 'secret.txt').write_text('synthetic private sibling')
    spec = linux.policy(venv / 'bin' / 'python', base, workspace, client, readonly=True)
    sources = [Path(src) for src, _ in spec['runtime_mounts']]
    assert base not in sources and venv not in sources and tmp_path not in sources
    assert (base / 'secret.txt') not in sources
    assert interpreter.resolve() in sources
    assert (venv / 'pyvenv.cfg').resolve() in sources
    assert spec['runtime_symlinks'] == [[str(interpreter.resolve()), str(venv / 'bin' / 'python')]]
    monkeypatch.setattr(linux.shutil, 'which', lambda name: '/usr/bin/bwrap')
    command = linux.command(spec)
    assert command[0] == '/usr/bin/bwrap'
    assert all(flag in command for flag in ('--unshare-user', '--unshare-pid', '--unshare-net', '--die-with-parent', '--as-pid-1'))
    position = command.index(str(workspace))
    assert command[position - 1] == '--ro-bind'
    assert command[-4:] == [str(venv / 'bin' / 'python'), '-B', '-s', str(client / 'worker.py')]
    assert '--remount-ro' in command


def test_linux_live_usage_includes_worker_not_just_launcher(monkeypatch):
    def stat(utime, stime, cutime, cstime, rss):
        fields = ['0'] * 22
        for i, value in zip((11, 12, 13, 14, 21), (utime, stime, cutime, cstime, rss), strict=True):
            fields[i] = str(value)
        return '123 (a name with ) brackets) ' + ' '.join(fields)

    contents = {
        '/proc/100/stat': stat(1, 2, 3, 4, 5),
        '/proc/100/task/100/children': '200 300',
        '/proc/200/stat': stat(90, 20, 0, 0, 100),
        '/proc/200/task/200/children': '',
    }

    def read(path):
        if str(path) not in contents:
            raise FileNotFoundError
        return contents[str(path)]

    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(os, 'sysconf', lambda key: 100 if key == 'SC_CLK_TCK' else 4096)
    assert linux_process_usage(100) == (1.2, 105 * 4096)


@native
@pytest.mark.native
def test_numerical_threads_and_libraries_work_inside_the_boundary(executor, private_file):
    executor.preflight([private_file])
    result = executor.execute('''from concurrent.futures import ThreadPoolExecutor
import numpy as np
import scipy.linalg
from sklearn.linear_model import LogisticRegression
with ThreadPoolExecutor(2) as pool:
    values = list(pool.map(lambda n: n * n, [2, 3]))
model = LogisticRegression().fit(np.array([[0], [1], [2], [3]]), [0, 0, 1, 1])
result = {'threads': values, 'inverse': scipy.linalg.inv([[2.]])[0, 0], 'prediction': int(model.predict([[3]])[0])}
''')['result']
    assert result == {'threads': [4, 9], 'inverse': 0.5, 'prediction': 1}


@native
@pytest.mark.native
@pytest.mark.skipif(platform.system() != 'Linux', reason='Linux disallows interpreter replacement as well as child processes')
def test_exec_replacement_and_parent_environment_are_unavailable(executor, private_file, monkeypatch):
    monkeypatch.setenv('JOCKEYBENCH_TEST_SECRET', 'synthetic secret')
    executor.preflight([private_file])
    result = executor.execute('''import os, sys
result = {'environment_hidden': 'JOCKEYBENCH_TEST_SECRET' not in os.environ, 'exec_denied': False}
try:
    os.execve(sys.executable, [sys.executable, '-c', 'pass'], {})
except PermissionError:
    result['exec_denied'] = True
''')['result']
    assert all(result.values())


@native
@pytest.mark.native
@pytest.mark.skipif(platform.system() != 'Linux', reason='Linux syscall and namespace guarantees')
def test_linux_kernel_boundary_and_hidden_host_paths(executor, private_file):
    executor.preflight([private_file])
    result = executor.execute('''import ctypes, errno, os, socket
libc = ctypes.CDLL(None, use_errno=True)
result = {'pid_namespace': os.getpid() == 1, 'host_accounts_hidden': not os.path.exists('/etc/passwd')}
for name in ('unshare', 'setns'):
    value = getattr(libc, name)(0 if name == 'unshare' else -1, 0)
    result[name] = value == -1 and ctypes.get_errno() == errno.EPERM
try:
    socket.socketpair()
except PermissionError:
    result['socketpair_denied'] = True
else:
    result['socketpair_denied'] = False
''')['result']
    assert all(result.values()), result


@native
@pytest.mark.native
def test_busy_worker_cpu_is_included_in_settlement(executor, private_file):
    executor.preflight([private_file])
    executor.execute('''import time
start = time.process_time()
while time.process_time() - start < 0.2:
    pass
result = True
''')
    assert executor.service.state['compute_jobs'][-1]['cpu_seconds'] >= 0.2


@native
@pytest.mark.native
def test_terminated_worker_cpu_is_not_lost_with_the_launcher(executor, private_file):
    executor.preflight([private_file])
    executor.config = replace(executor.config, limits=replace(executor.config.limits, job_seconds=0.5))
    with pytest.raises(JobFailed, match='timed out'):
        executor.execute('while True: pass')
    job = executor.service.state['compute_jobs'][-1]
    assert job['status'] == 'settled' and job['cpu_seconds'] >= 0.2
