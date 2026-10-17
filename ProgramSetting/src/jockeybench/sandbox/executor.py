"""Deny-default execution of agent code, the SDK pipe broker, CPU accounting and strategy versions.

Each ``compute`` call starts a fresh interpreter under ``sandbox-exec``: no
network, no subprocesses, and file access limited to the strategy workspace
(read-only for framework-run predictions). The only channel out is a pair of
pipes to the tool broker. CPU time is measured with ``wait4`` and billed.
Saved strategy files are content-addressed snapshots under ``versions/``.
"""

import ast
import json
import os
import resource
import selectors
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ..canonical import digest, file_hash, packed
from ..errors import BudgetExceeded, JobFailed, Rejected
from ..ledger import book
from ..storage import tree_bytes
from .environment import execution_env

CLIENT = Path(__file__).parent / 'client'
ALLOWED_SUFFIXES = {'.py', '.json', '.txt', '.md', '.bin', '.pkl'}
SAMPLE_CACHE_KEYS = ('examples', 'runners', 'card', 'label_missing', 'records')
PREFLIGHT = '''import os, socket, subprocess
result = {'blocked': [], 'metadata_blocked': [], 'network_blocked': False, 'spawn_blocked': False}
for p in PROBES:
    try:
        with open(p, 'rb') as f: f.read(1)
    except PermissionError:
        result['blocked'].append(True)
    except Exception:
        result['blocked'].append(False)
    else:
        result['blocked'].append(False)
    try:
        os.stat(p)
    except PermissionError:
        result['metadata_blocked'].append(True)
    except Exception:
        result['metadata_blocked'].append(False)
    else:
        result['metadata_blocked'].append(False)
try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        sock.connect(('127.0.0.1', 9))
except PermissionError:
    result['network_blocked'] = True
try:
    subprocess.run(['/usr/bin/true'], check=True)
except PermissionError:
    result['spawn_blocked'] = True
'''


def validate_json_artifact(content):
    """Saved JSON may hold fitted parameters, never cached historical samples."""
    try:
        value = json.loads(content)
        packed(value)
    except (ValueError, TypeError, RecursionError) as exc:
        raise Rejected('Strategy JSON must be finite, bounded JSON') from exc
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if any(k in item and isinstance(item[k], (dict, list)) for k in SAMPLE_CACHE_KEYS):
                raise Rejected(
                    'Historical sample caches are not allowed. Fit from SDK pages in RAM within one compute call; persist only code, concise notes and fitted parameters.'
                )
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def validate_files(files):
    if not isinstance(files, dict) or len(files) > 64:
        raise Rejected('At most 64 strategy files allowed')
    for name, content in files.items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name.startswith('.')
            or Path(name).suffix not in {'.py', '.json', '.txt', '.md'}
        ):
            raise Rejected('Strategy tool accepts flat .py/.json/.txt/.md files')
        if not isinstance(content, str) or len(content.encode()) > 1024**2:
            raise Rejected('Invalid strategy file size')
        if name.endswith('.json'):
            validate_json_artifact(content)
        if name.endswith('.py'):
            try:
                module = ast.parse(content)
            except SyntaxError as exc:
                raise Rejected('Strategy syntax check failed') from exc
            if name == 'strategy.py':
                functions = {n.name: n for n in module.body if isinstance(n, ast.FunctionDef)}
                if 'predict' not in functions or len(functions['predict'].args.args) != 2:
                    raise Rejected('strategy.py must define predict(sdk, context)')


def profile(python, base, workspace, readonly=False):
    """The ``sandbox-exec`` policy: deny by default, allow the interpreter, its libraries and the workspace."""

    def literal(p):
        return json.dumps(str(Path(p).resolve()), ensure_ascii=False)  # sandbox-exec does not decode \uXXXX escapes

    read_roots = [base, Path(python).parent.parent / 'lib', CLIENT, '/System/Library', '/usr/lib', '/private/var/db/dyld']
    app_python = Path(base) / 'Resources/Python.app/Contents/MacOS/Python'
    clauses = [
        '(version 1)',
        '(deny default)',
        '(allow sysctl-read)',
        '(allow process-exec (literal ' + literal(python) + ') (literal ' + literal(app_python) + '))',
        '(allow signal (target self))',
        '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))',
        '(allow file-read* (literal "/") (literal ' + literal(Path(python).parent.parent / 'pyvenv.cfg') + '))',
        '(allow file-write* (literal "/dev/null"))',
    ]
    clauses += ['(allow file-read* (subpath ' + literal(p) + '))' for p in read_roots]
    clauses += ['(allow file-read* ' + ('' if readonly else 'file-write* ') + '(subpath ' + literal(workspace) + '))']
    # Interpreter launch/imports need to resolve permitted paths and ancestors.
    # Never grant metadata access to arbitrary files (e.g. private score logs).
    metadata = set()
    for raw in [python, *read_roots, workspace, Path(python).parent.parent / 'pyvenv.cfg']:
        for p in (Path(raw).absolute(), Path(raw).resolve()):
            metadata.add(p)
            metadata.update(p.parents)
    link = Path(python).absolute()
    for _ in range(16):
        # exec walks each hop at its canonical location (e.g. Homebrew's opt/ -> Cellar/ chain).
        for hop in (link, link.parent.resolve() / link.name):
            metadata.add(hop)
            metadata.update(hop.parents)
        for node in (link, *link.parents):  # directory symlinks below a non-canonical ancestor (/tmp -> /private/tmp)
            if node.is_symlink():
                canonical = Path(os.path.realpath(node.parent)) / node.name
                metadata.add(canonical)
                metadata.update(canonical.parents)
        if not link.is_symlink():
            break
        destination = Path(os.readlink(link))
        link = destination if destination.is_absolute() else Path(os.path.normpath(link.parent.resolve() / destination))
    clauses += ['(allow file-read-metadata (literal ' + json.dumps(str(p), ensure_ascii=False) + '))' for p in sorted(metadata)]
    return '\n'.join(clauses)


def cpu_time(text):
    """Parse ``ps -o time`` output ([[hh:]mm:]ss.ss) into seconds."""
    total = 0.0
    for part in text.strip().split(':'):
        total = total * 60 + float(part)
    return total


class Executor:
    def __init__(self, config, storage, service):
        self.config, self.storage, self.service = config, storage, service
        self.work = storage.path / 'workspace'
        self.work.mkdir(exist_ok=True)
        self.versions = storage.path / 'versions'
        self.versions.mkdir(exist_ok=True)
        self.ready = False
        probe = subprocess.run(
            [config.python, '-I', '-B', '-c', 'import sys;print(sys.base_prefix)'], capture_output=True, text=True, check=True
        )
        self.base = Path(probe.stdout.strip())
        self.policy = profile(config.python, self.base, self.work)

    # -- strategy versions -------------------------------------------------------------
    def files_digest(self):
        tree_bytes(self.work)  # Reject symlinks before any traversal.
        paths = sorted(self.work.rglob('*'))
        if any(p.is_dir() for p in paths):
            raise Rejected('Strategy workspace must stay flat')
        files = {}
        cap = self.config.limits.artifact_bytes
        for p in paths:
            if p.suffix not in ALLOWED_SUFFIXES or not p.is_file() or (cap is not None and p.stat().st_size > cap):
                raise Rejected('Strategy workspace files must be .py/.json/.txt/.md/.bin/.pkl files within configured sizes')
            if p.suffix == '.py':
                validate_files({p.name: p.read_text()})
            elif p.suffix == '.json':
                validate_json_artifact(p.read_text())
            files[p.name] = file_hash(p)
        return digest(files)

    def snapshot(self):
        ident = self.files_digest()
        target = self.versions / ident
        if not target.exists():
            self.storage.check(tree_bytes(self.work))
            shutil.copytree(self.work, target)
        self.service.state['strategy_digest'] = ident
        self.service.persist()
        return ident

    def program_digest(self, ident):
        """Digest of a version's program files, ignoring NOTES.md."""
        return digest({p.name: file_hash(p) for p in sorted((self.versions / ident).iterdir()) if p.name != 'NOTES.md'})

    def session_files(self):
        """Strategy files (24 KiB in total, truncation listed) and the full NOTES.md shown at the start of every session."""
        ident = self.service.state.get('accepted_workspace') or self.service.state.get('validated_strategy')
        folder = self.versions / ident if ident else self.work
        tree_bytes(folder)
        files, truncated, remaining = {}, [], 24 * 1024
        paths = [p for p in folder.iterdir() if p.is_file() and p.suffix in {'.py', '.txt', '.md', '.json'}]
        for p in sorted(paths, key=lambda p: (p.stat().st_size, p.name)):
            raw = p.read_bytes()
            text = raw[:remaining].decode('utf-8', errors='ignore')
            files[p.name] = text
            remaining -= len(text.encode())
            if len(raw) > len(text.encode()):
                truncated.append(p.name)
        # Notes are shown in full; compute can write any bytes, so decode leniently like the files above.
        notes = (folder / 'NOTES.md').read_text(encoding='utf-8', errors='replace') if 'NOTES.md' in files else None
        return {'current_strategy_files': files, 'truncated': truncated, 'notes': notes}

    def restore(self, ident):
        source = self.versions / ident
        if not source.is_dir() or source.is_symlink():
            raise Rejected('Missing strategy checkpoint')
        tree_bytes(source)
        for p in self.work.iterdir():
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
        self.storage.check(tree_bytes(source))
        for p in source.iterdir():
            shutil.copyfile(p, self.work / p.name)
        if self.files_digest() != ident:
            raise Rejected('Strategy version digest mismatch')
        self.service.state['strategy_digest'] = ident

    def save_strategy(self, files):
        validate_files(files)
        old = self.snapshot()
        self.storage.check(sum(len(s.encode()) for s in files.values()))
        try:
            for name, content in files.items():
                (self.work / name).write_text(content)
            new = self.snapshot()
        except BaseException:
            self.restore(old)
            raise
        return {'strategy_digest': new}

    # -- execution -----------------------------------------------------------------------
    def preflight(self, forbidden):
        """Prove isolation before any agent code runs: the given paths, network and subprocesses are denied."""
        probes = [str(Path(p).resolve()) for p in forbidden]
        answer = self._run(PREFLIGHT.replace('PROBES', repr(probes)), preflight=True)['result']
        if not (
            answer and all(answer['blocked']) and all(answer['metadata_blocked']) and answer['network_blocked'] and answer['spawn_blocked']
        ):
            raise Rejected('Native sandbox isolation preflight failed: ' + json.dumps(answer))
        self.ready = True
        return {
            'filesystem_denied': len(probes),
            'metadata_denied': len(probes),
            'network_denied': True,
            'subprocess_denied': True,
            'policy_digest': digest(self.policy),
            'worker_digest': digest({p.name: file_hash(p) for p in CLIENT.glob('*.py')}),
        }

    def execute(self, code):
        """The ``compute`` tool: run code; on any failure the workspace returns to its prior version."""
        if not self.ready:
            raise Rejected('Native sandbox has not passed isolation checks')
        if not isinstance(code, str) or len(code.encode()) > 1024**2:
            raise Rejected('Code too large')
        try:
            compile(code, '<agent>', 'exec')
        except SyntaxError as exc:
            raise JobFailed('Code syntax check failed', {'line': exc.lineno, 'offset': exc.offset}) from exc
        old = self.snapshot()
        try:
            reply = self._run(code)
            reply['strategy_digest'] = self.snapshot()
            return reply
        except BaseException as exc:
            self.restore(old)
            self.service.persist()
            if isinstance(exc, BudgetExceeded) and str(exc).startswith(('Code execution', 'Code RSS', 'Code output', 'IPC response')):
                raise JobFailed(str(exc), {'strategy_restored': True}) from exc
            raise

    def predict(self, context):
        """Framework-run prediction: call the saved ``predict``; files must not change."""
        old = self.snapshot()
        try:
            code = (
                "exec(compile(open('strategy.py').read(), 'strategy.py', 'exec'))\n"
                "if not callable(globals().get('predict')): raise TypeError('predict must be callable')\n"
                'result = predict(sdk, ' + repr(context) + ')'
            )
            result = self.service.call('compute', {'code': code})['result']
            if self.files_digest() != old:
                raise JobFailed('Prediction code must not change strategy files; update during reflection', {'strategy_restored': True})
            return result
        finally:
            self.restore(old)
            self.service.persist()

    def _reap(self, proc, flags):
        if proc.returncode is not None:
            return proc.returncode
        pid, status, usage = os.wait4(proc.pid, flags)
        if pid:
            proc.returncode = os.waitstatus_to_exitcode(status)
            self.actual_cpu = usage.ru_utime + usage.ru_stime
        return proc.returncode

    def _cpu_guard(self, proc, preflight):
        if not preflight and self.service.state.get('strategy_job') and self.config.limits.strategy_cpu_seconds is not None:
            result = subprocess.run(['/bin/ps', '-o', 'time=', '-p', str(proc.pid)], capture_output=True, text=True)
            if result.stdout.strip() and cpu_time(result.stdout) >= self.config.limits.strategy_cpu_seconds:
                raise BudgetExceeded('Strategy CPU safety limit reached')

    def _run(self, code, preflight=False):
        """Run one job and bill its CPU. Interrupted jobs keep their reservation, never a zero charge."""
        self.actual_cpu = None
        self.process_started = False
        if preflight:
            return self._spawn(code, preflight=True)
        state = self.service.state
        jobs = state.setdefault('compute_jobs', [])
        ident = 'cpu-' + str(len(jobs))
        limits = self.config.limits
        reserved = (
            limits.strategy_cpu_seconds
            if state.get('strategy_job')
            else (None if limits.job_seconds is None else limits.job_seconds * limits.numerical_threads)
        )
        # Without a ceiling, no finite amount can stand in for interrupted CPU.
        # Persist the unknown reservation and exclude unsettled jobs from comparison.
        reserved_cost = self.service.charge('cpu_yen', reserved) if reserved is not None else 0
        job = {
            'id': ident,
            'status': 'reserved',
            'reserved_cpu_seconds': reserved,
            'phase': state['phase'],
            'index': state['index'],
            'restore_digest': state['strategy_digest'],
            'sdk_misuse': [],
        }
        jobs.append(job)
        book(
            state,
            ident + ':reserve',
            'compute',
            reserved_cost,
            basis='unbounded_cpu_pending' if reserved is None else 'interruption_reservation_not_measured',
        )
        self.service.persist()
        try:
            result = self._spawn(code)
            job['sdk_misuse'] = result.get('sdk_misuse', [])
            if (
                state.get('strategy_job')
                and limits.strategy_cpu_seconds is not None
                and self.actual_cpu is not None
                and self.actual_cpu >= limits.strategy_cpu_seconds
            ):
                raise BudgetExceeded('Strategy CPU safety limit reached')
            return result
        except JobFailed as exc:
            job['sdk_misuse'] = exc.details.get('sdk_misuse', [])
            raise
        finally:
            if not self.process_started:
                self.actual_cpu = 0.0
            if self.actual_cpu is not None:
                # CPU and waiting are independent: only this isolated process is charged.
                adjustment = self.service.charge('cpu_yen', self.actual_cpu) - reserved_cost
                book(state, ident + ':resolve', 'compute', adjustment, basis='wait4_process_cpu')
                job.update(status='settled', cpu_seconds=self.actual_cpu)
                state['usage']['cpu_seconds'] = state['usage'].get('cpu_seconds', 0) + self.actual_cpu
            else:
                job['status'] = 'interrupted_cpu_unknown'
            self.service.persist()

    def _spawn(self, code, preflight=False):
        """Start the worker, broker its SDK requests and enforce the job ceilings."""
        self.process_started = False
        limits = self.config.limits
        strategy_job = bool(self.service.state.get('strategy_job'))
        seconds_key = 'strategy_seconds' if strategy_job else 'seconds'
        self.service.state['usage'].setdefault(seconds_key, 0)
        if (
            not preflight
            and not strategy_job
            and limits.batch_seconds is not None
            and self.service.state['usage']['seconds'] >= limits.batch_seconds
        ):
            raise JobFailed('Batch compute time exhausted')
        self.storage.check()
        rq_r, rq_w = os.pipe()
        rp_r, rp_w = os.pipe()
        env = {
            'PATH': '/usr/bin:/bin',
            'HOME': str(self.work),
            'TMPDIR': str(self.work),
            'PYTHONDONTWRITEBYTECODE': '1',
            'JRDB_REQUEST_FD': str(rq_w),
            'JRDB_RESPONSE_FD': str(rp_r),
            **execution_env(limits),
        }
        policy = profile(self.config.python, self.base, self.work, readonly=True) if strategy_job else self.policy
        command = ['/usr/bin/sandbox-exec', '-p', policy, self.config.python, '-B', str(CLIENT / 'worker.py')]

        def child_limits():
            file_caps = [v for v in (limits.storage_bytes, limits.artifact_bytes) if v is not None]
            if file_caps:
                resource.setrlimit(resource.RLIMIT_FSIZE, (min(file_caps),) * 2)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            if limits.open_files is not None:
                resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_files,) * 2)

        start = time.monotonic()
        proc = None
        selector = selectors.DefaultSelector()
        buffers = {'request': bytearray(), 'stdout': bytearray(), 'stderr': bytearray()}
        outgoing = bytearray()
        deadlines = [
            v
            for v in (
                limits.job_seconds,
                None
                if strategy_job or limits.batch_seconds is None
                else max(0, limits.batch_seconds - self.service.state['usage']['seconds']),
            )
            if v is not None
        ]
        deadline = min(deadlines) if deadlines else None
        if preflight:
            deadline = 60  # Isolation diagnostics have their own watchdog.
        reserved = deadline or 0
        if not preflight:
            # Reserve first. Abrupt host termination cannot grant free compute on resume.
            self.service.state['usage'][seconds_key] += reserved
            self.service.persist()
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=self.work,
                pass_fds=(rq_w, rp_r),
                start_new_session=True,
                preexec_fn=child_limits,
            )
            self.process_started = True
            os.close(rq_w)
            rq_w = -1
            os.close(rp_r)
            rp_r = -1
            os.set_blocking(rp_w, False)
            proc.stdin.write(packed({'code': code}) + b'\n')
            proc.stdin.close()
            for stream, name in ((rq_r, 'request'), (proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                if deadline is not None and time.monotonic() - start > deadline:
                    raise BudgetExceeded('Code execution timed out')
                self.storage.check()
                if self._reap(proc, os.WNOHANG) is None:
                    if limits.rss_bytes is not None:
                        ps = subprocess.run(['/bin/ps', '-o', 'rss=', '-p', str(proc.pid)], capture_output=True, text=True)
                        if ps.stdout.strip() and int(ps.stdout.strip()) * 1024 >= limits.rss_bytes:
                            raise BudgetExceeded('Code RSS stop threshold reached')
                    self._cpu_guard(proc, preflight)
                for event, _ in selector.select(0.05):
                    fd = event.fd
                    if event.data == 'reply':
                        try:
                            sent = os.write(fd, outgoing)
                            del outgoing[:sent]
                        except BlockingIOError:
                            pass
                        if not outgoing:
                            selector.unregister(fd)
                        continue
                    data = os.read(fd, 65536)
                    if not data:
                        selector.unregister(event.fileobj)
                        continue
                    name = event.data
                    buffers[name].extend(data)
                    if len(buffers[name]) > limits.response_bytes:
                        raise BudgetExceeded('Code output or IPC limit exceeded')
                    if name == 'request':
                        while b'\n' in buffers[name]:
                            line, _, rest = buffers[name].partition(b'\n')
                            buffers[name] = bytearray(rest)
                            outgoing.extend(packed(self._serve(line, preflight)) + b'\n')
                            if len(outgoing) > limits.response_bytes + 4096:
                                raise BudgetExceeded('IPC response queue exhausted')
                            if rp_w not in selector.get_map():
                                selector.register(rp_w, selectors.EVENT_WRITE, 'reply')
            self._reap(proc, 0)
            if proc.returncode:
                try:
                    details = json.loads(buffers['stdout'])
                except (ValueError, UnicodeDecodeError):
                    details = None
                if details and 'error' in details:
                    raise JobFailed('Isolated Python raised ' + str(details['error']), details)
                raise Rejected('Isolated code failed; sandbox unavailable or code rejected')
            reply = json.loads(buffers['stdout'])
            if 'error' in reply:
                raise Rejected('Isolated code failed')
            return reply
        finally:
            if proc is not None and self._reap(proc, os.WNOHANG) is None:
                os.killpg(proc.pid, signal.SIGKILL)
                self._reap(proc, 0)
            selector.close()
            for fd in (rq_r, rq_w, rp_r, rp_w):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            if proc is not None:
                for stream in (proc.stdout, proc.stderr):
                    stream.close()
            if not preflight:
                actual = time.monotonic() - start
                if deadline is not None:
                    actual = min(actual, reserved)
                self.service.state['usage'][seconds_key] -= reserved - actual
                self.service.persist()

    def _serve(self, line, preflight):
        """One SDK request from the worker, answered through the metered broker."""
        try:
            request = json.loads(line)
            if preflight:
                raise Rejected('SDK disabled during isolation preflight')
            return {'result': self.service.call(request['tool'], request.get('arguments'), from_sdk=True)}
        except BudgetExceeded:
            raise
        except Exception as exc:
            return self.service.error_response(exc)
