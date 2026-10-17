"""Strategy files and the macOS sandbox. Tests marked ``native`` start real ``sandbox-exec`` jobs."""

import json
from dataclasses import replace

import pytest

from conftest import native
from jockeybench.errors import BudgetExceeded, JobFailed, Rejected
from jockeybench.ledger import ledger, reconcile
from jockeybench.sandbox.executor import Executor, cpu_time, profile
from jockeybench.storage import Storage

UNIFORM = (
    "def predict(sdk, context):\n"
    "    return {'ranking': context['runner_numbers'], 'probabilities': {n: .2 for n in context['runner_numbers']}}\n"
)


def test_invalid_strategy_files_are_refused_and_leave_the_version_unchanged(executor):
    initial = executor.snapshot()
    for files in [
        {'strategy.py': 'def predict(:'},
        {'strategy.py': 'def nope(): pass'},
        {'../outside.py': 'pass'},
        {'.hidden.py': 'pass'},
        {'data.bin': ''},
        {'train.json': '[{"runners": []}]'},
    ]:
        with pytest.raises(Rejected):
            executor.save_strategy(files)
    assert executor.snapshot() == initial
    executor.save_strategy({'parameters.json': '{"weights":[0.2,-0.1],"intercept":0.3}'})


def test_compute_requires_the_isolation_preflight(executor):
    with pytest.raises(Rejected, match='isolation checks'):
        executor.execute('result = 1')


def test_storage_is_atomic_locked_and_bounded(config):
    a = Storage(config.managed_root, 'a', 2000)
    a.save({'x': 1})
    assert a.load()['x'] == 1
    (a.root / 'other-file').write_bytes(b'x' * 2000)
    with pytest.raises(BudgetExceeded):
        a.save({'x': 2})
    assert a.load()['x'] == 1
    with pytest.raises(Rejected, match='active'):
        Storage(config.managed_root, 'b', 2000)
    a.close()
    with pytest.raises(Rejected, match='Invalid run name'):
        Storage(config.managed_root, '../escape', None)


def test_cpu_time_parsing():
    assert cpu_time('0:01.50') == 1.5 and cpu_time('1:02:03') == 3723


@native
@pytest.mark.native
def test_isolation_denies_private_files_network_and_subprocesses(executor, private_file, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp('outside') / 'secret.txt'
    elsewhere.write_text('not for agents')
    result = executor.preflight([private_file, elsewhere])
    assert result['network_denied'] and result['subprocess_denied'] and result['filesystem_denied'] == 2
    code = 'import os\ntry:\n    os.stat(' + repr(str(private_file)) + ')\n    result=False\nexcept PermissionError:\n    result=True'
    assert executor.execute(code)['result'] is True
    output = executor.execute("import numpy as np\nresult = {'n': int(np.array([1,2]).sum()), 'p': sdk.call('describe')['projection']}")
    assert output['result'] == {'n': 3, 'p': 'objective-v5'}


@native
@pytest.mark.native
def test_child_processes_escapes_and_external_network_are_denied(executor, private_file):
    executor.preflight([private_file])
    code = '''import os, socket
result = {'fork_denied': False, 'write_denied': False, 'external_denied': False}
try:
    pid = os.fork()
except PermissionError:
    result['fork_denied'] = True
else:
    if pid == 0: os._exit(0)
    os.waitpid(pid, 0)
try:
    open('../escape.txt','w').write('escape')
except PermissionError:
    result['write_denied'] = True
try:
    with socket.socket() as s:
        s.settimeout(1)
        s.connect(('1.1.1.1', 443))
except PermissionError:
    result['external_denied'] = True
'''
    assert all(executor.execute(code)['result'].values())


@native
@pytest.mark.native
def test_failed_jobs_roll_back_their_files(executor, private_file):
    executor.preflight([private_file])
    initial = executor.snapshot()
    with pytest.raises(Rejected, match='sample caches'):
        executor.execute("open('train.json','w').write('[{\"runners\":[]}]')\nresult=True")
    executor.config = replace(executor.config, limits=replace(executor.config.limits, job_seconds=0.5))
    with pytest.raises(JobFailed, match='timed out'):
        executor.execute("import time\nopen('notes.txt','w').write('partial')\ntime.sleep(3)")
    assert executor.snapshot() == initial and not (executor.work / 'notes.txt').exists()


@native
@pytest.mark.native
def test_memory_and_ipc_ceilings_stop_the_job(executor, private_file):
    executor.preflight([private_file])
    executor.config = replace(executor.config, limits=replace(executor.config.limits, rss_bytes=64 * 1024**2, job_seconds=0.5))
    with pytest.raises(JobFailed):
        executor.execute('import time\na=bytearray(128*1024**2)\ntime.sleep(3)')
    flood = "import json, time\nfor i in range(100):\n    sdk.request.write(json.dumps({'tool':'describe'}) + '\\n')\nsdk.request.flush()\ntime.sleep(3)"
    with pytest.raises(JobFailed):
        executor.execute(flood)


@native
@pytest.mark.native
def test_sdk_calls_are_metered_and_errors_are_explained(executor, private_file):
    executor.preflight([private_file])
    service, race = executor.service, executor.service.state['schedule'][0]['races'][0]
    code = (
        "a=sdk.call('card',{'race_id':" + repr(race) + "})\nb=sdk.call('card',race_id=" + repr(race) + ")\n"
        "import sdk as module\nc=module.SDK().call('card',race_id=" + repr(race) + ")\n"
        "result={'equal':a['race_id']==b['race_id']==c['race_id'],'cells':a['_cost']['evidence_cells'],'repeat':b['_cost']['repeat']}"
    )
    answer = service.call('compute', {'code': code})
    assert answer['result'] == {'equal': True, 'cells': answer['result']['cells'], 'repeat': True}
    assert service.state['usage']['calls'] == 4 and service.state['usage']['evidence_cells'] == answer['result']['cells']
    with pytest.raises(JobFailed) as caught:
        service.call('compute', {'code': "open('notes.txt','w').write('partial')\nsdk.card('x')"}, operation_id='bad')
    assert caught.value.details['error'] == 'AttributeError' and caught.value.details['frames'][-1]['line'] == 2
    assert caught.value.details['sdk_misuse'] == [{'attribute': 'card', 'count': 1}]
    with pytest.raises(JobFailed) as caught:
        service.call('compute', {'code': "result=sdk.call('history', {'limit':1}, limit=2)"})
    assert 'Duplicate' in caught.value.details['tool_error']['message']


@native
@pytest.mark.native
def test_cpu_is_billed_from_the_process_not_the_wall_clock(executor, private_file):
    executor.preflight([private_file])
    service, race = executor.service, executor.service.state['schedule'][0]['races'][0]
    result = service.call(
        'compute', {'code': "import time\ntime.sleep(.5)\nresult=sdk.call('card', race_id=" + repr(race) + ")['race_id']"}
    )
    job = service.state['compute_jobs'][-1]
    assert result['result'] == race and job['status'] == 'settled' and 0 < job['cpu_seconds'] < 0.5
    assert result['_cost']['cpu_micro'] > 0
    assert sum(x['_cost']['total_micro'] for x in service.state['audit']) == reconcile(service.state)['cost_micro']


@native
@pytest.mark.native
def test_strategy_cpu_cap_stops_a_busy_prediction(executor, private_file):
    executor.preflight([private_file])
    executor.config = replace(executor.config, limits=replace(executor.config.limits, strategy_cpu_seconds=0.05))
    executor.service.state['strategy_job'] = 'busy'
    with pytest.raises(BudgetExceeded, match='CPU'):
        executor.service.call('compute', {'code': 'while True: pass'})
    assert executor.service.state['compute_jobs'][-1]['cpu_seconds'] >= 0.05
    assert ledger(executor.service.state)['cost_micro'] > 0


@native
@pytest.mark.native
def test_prediction_runs_read_only(executor, private_file):
    executor.preflight([private_file])
    service, race = executor.service, executor.service.state['schedule'][0]['races'][0]
    context = {'race_id': race, 'runner_numbers': ['01', '02', '03', '04', '05'], 'seed': 0}
    executor.save_strategy({'strategy.py': UNIFORM})
    service.state['strategy_job'] = race
    service.observation_limit = 2048  # a nearly full model context does not constrain framework jobs
    assert executor.predict(context)['ranking'] == context['runner_numbers']
    executor.save_strategy({'strategy.py': UNIFORM.replace('    return', "    open('notes.txt','w').write('x')\n    return")})
    with pytest.raises(JobFailed, match='PermissionError|must not change'):
        executor.predict(context)
    assert not (executor.work / 'notes.txt').exists()


def test_notes_that_are_not_utf8_are_shown_instead_of_crashing_every_session(executor):
    (executor.work / 'NOTES.md').write_bytes(b'\xff notes\r\n')
    assert executor.session_files()['notes'] == '\ufffd notes\n'


def test_profile_names_canonical_symlink_hops_and_keeps_non_ascii_paths(tmp_path):
    root = tmp_path.resolve()
    real = root / '研究' / 'venv'
    (real / 'bin').mkdir(parents=True)
    (real / 'bin' / 'python').write_text('')
    (root / 'base').mkdir()
    (root / 'base' / 'linked').symlink_to(real, target_is_directory=True)
    (root / 'alias').symlink_to(root / 'base', target_is_directory=True)  # a symlinked ancestor, like /tmp
    policy = profile(str(root / 'alias' / 'linked' / 'bin' / 'python'), str(real), str(root / '研究' / 'ws'))
    assert '研究' in policy and '\\u' not in policy  # sandbox-exec does not decode JSON escapes
    assert f'(allow file-read-metadata (literal {json.dumps(str(root / "base" / "linked"), ensure_ascii=False)}))' in policy


@native
@pytest.mark.native
def test_a_workspace_under_a_non_ascii_path_is_writable(config, service, private_file, tmp_path):
    storage = Storage(str(tmp_path / '实验 runs'), 'unit', config.limits.storage_bytes)
    executor = Executor(replace(config, managed_root=str(tmp_path / '实验 runs')), storage, service)
    service.executor = executor
    executor.snapshot()
    executor.preflight([private_file])
    assert executor.execute("open('x.txt', 'w').write('ok')\nresult = open('x.txt').read()")['result'] == 'ok'
    storage.close()
