"""Whole trajectories with the sandbox stubbed out: resume, arms, fallbacks and session limits."""

import copy
import io
import json

import pytest

from jockeybench.agents.compatible import CompatibleAgent
from jockeybench.agents.reference import ProgramReference
from jockeybench.audit.replay import replay
from jockeybench.errors import BudgetExceeded, JobFailed, Pending, Rejected
from jockeybench.sandbox.executor import Executor
from jockeybench.scoring import uniform
from jockeybench.storage import QUOTA_EXHAUSTED, SYMLINK_REFUSED

SMALL = {'smoke_days': 2, 'races_per_day': 2, 'schedule_mode': 'smoke'}  # 2024-01-06 (two races) and 2024-01-07 (one)


def test_reference_run_resumes_without_resealing_and_replays(make_run):
    run = make_run('A', ProgramReference('R0'), **SMALL)
    run.run(stop_after=1)
    sealed = copy.deepcopy(run.state['submissions'])
    assert len(sealed) == 1 and not run.state['scores']
    run.close()
    run = make_run('A', ProgramReference('R0'), **SMALL)
    report = run.run()
    assert report['completed'] and report['planned_races'] == report['scored_races'] == 3
    assert all(run.state['submissions'][k] == v for k, v in sealed.items())
    assert report['delivery_totals']['no_strategy'] == 3 and report['net_A_yen'] == 0 and not report['clean_trajectory']
    assert run.run() == report  # a completed run only reports
    assert replay(run.config, run.state, run.source)['verified_score_revenues'] == 3
    run.close()


def test_a_changed_configuration_cannot_resume_a_checkpoint(make_run):
    make_run('A', ProgramReference('R0'), **SMALL).close()
    with pytest.raises(Rejected, match='differs from checkpoint'):
        make_run('A', ProgramReference('R0'), smoke_days=1, races_per_day=2, schedule_mode='smoke')


class Notes(ProgramReference):
    """Saves a note in warmup and another after every day."""

    def warmup(self, service):
        service.call('save_strategy', {'files': {'notes.txt': 'initial', 'strategy.py': 'def predict(sdk, context): return {}'}})

    def reflect(self, service, feedback):
        service.call('save_strategy', {'files': {'notes.txt': 'updated'}})


def test_arms_share_the_initial_program_and_only_S_stays_frozen(make_run, monkeypatch):
    seen = []

    def predict(self, ctx):
        seen.append((self.config.arm, self.service.state['index'], (self.work / 'notes.txt').read_text()))
        return uniform(ctx['race_id'], ctx['runner_numbers'], 0)

    monkeypatch.setattr(Executor, 'predict', predict)
    common = make_run('common', Notes(), **SMALL)
    common.run(warmup_only=True)
    initial, initial_cost = common.state['initial_strategy'], common.state['ledger']['cost_micro']
    common.close()
    for arm in ['S', 'A']:
        run = make_run('arm-' + arm, Notes(), initial_from='common', arm=arm, **SMALL)
        assert run.state['initial_strategy'] == initial and run.state['ledger']['cost_micro'] == initial_cost
        run.run()
        assert next(note for a, day, note in seen if a == arm and day == 1) == ('initial' if arm == 'S' else 'updated')
        run.close()


def test_prediction_falls_back_to_the_previous_program_then_uniform(make_run, monkeypatch):
    run = make_run('fallback', ProgramReference(), **SMALL)
    old = run.executor.save_strategy({'strategy.py': 'def predict(sdk, context): return None'})['strategy_digest']
    new = run.executor.save_strategy({'strategy.py': 'def predict(sdk, context): return {}'})['strategy_digest']
    run.state.update(phase='predict', initial_strategy=old, validated_strategy=new, valid_strategies=[old, new])
    called = []

    def fail(ctx):
        called.append(run.state['strategy_digest'])
        raise JobFailed('Test strategy failed')

    monkeypatch.setattr(run.executor, 'predict', fail)
    report = run.run()
    assert report['completed'] and report['uniform_fallback_races'] == report['planned_races']
    assert called[:2] == [new, old] and report['delivery_totals']['execution_fallback'] == 3
    run.close()


class SavesAProgram(ProgramReference):
    def warmup(self, service):
        service.call('save_strategy', {'files': {'strategy.py': 'def predict(sdk, context): return {}'}}, operation_id='test-save')


@pytest.mark.parametrize('site', ['validation', 'prediction'])
@pytest.mark.parametrize(
    'error,stops',
    [
        (BudgetExceeded(QUOTA_EXHAUSTED), True),
        (Rejected(SYMLINK_REFUSED), True),
        (JobFailed('Isolated Python raised StorageError'), False),  # the agent's own exception class
    ],
)
def test_only_framework_storage_failures_stop_the_run(make_run, monkeypatch, site, error, stops):
    run = make_run('storage-' + site, SavesAProgram() if site == 'validation' else ProgramReference(), **SMALL)
    if site == 'prediction':
        version = run.executor.save_strategy({'strategy.py': 'def predict(sdk, context): return {}'})['strategy_digest']
        run.state.update(phase='predict', initial_strategy=version, validated_strategy=version, valid_strategies=[version])

    def fail(ctx):
        raise error

    monkeypatch.setattr(run.executor, 'predict', fail)
    if stops:
        with pytest.raises(Rejected, match=str(error)):
            run.run()
    else:
        report = run.run()
        expected = 'no_strategy' if site == 'validation' else 'execution_fallback'
        assert report['completed'] and report['delivery_totals'][expected] == 3
    run.close()


def test_a_session_limit_ends_the_session_but_a_network_stop_ends_the_run(make_run):
    class Limited(ProgramReference):
        def warmup(self, service):
            raise BudgetExceeded('Provider call budget exhausted')

        def reflect(self, service, feedback):
            raise BudgetExceeded('Provider call budget exhausted')

    run = make_run('limited', Limited(), **SMALL)
    report = run.run()
    assert report['completed'] and len(report['limit_events']) == 3
    run.close()

    class Network(ProgramReference):
        def warmup(self, service):
            raise Pending('Network pending')

    run = make_run('pending', Network(), **SMALL)
    with pytest.raises(Pending):
        run.run()
    assert not run.state['submissions']
    run.close()


@pytest.mark.parametrize('arm,requests', [('S', 1), ('A', 3)])
def test_model_sessions_happen_in_warmup_and_after_each_day_of_arm_A(make_run, monkeypatch, arm, requests):
    bodies = []

    def provider(request, **kwargs):
        bodies.append(json.loads(request.data))
        return io.BytesIO(
            json.dumps(
                {'choices': [{'message': {'role': 'assistant', 'content': 'Use uniform.'}}], 'usage': {'estimated_cost': 0.001}}
            ).encode()
        )

    monkeypatch.setattr('urllib.request.urlopen', provider)
    run = make_run('model-' + arm, CompatibleAgent({'model': 'mock', 'base_url': 'https://example.invalid'}), arm=arm, **SMALL)
    report = run.run()
    assert report['completed'] and len(bodies) == requests and report['prediction_model_requests'] == 0
    assert report['net_B_yen'] == pytest.approx(-0.001 * requests * 150)
    assert all(b['messages'][0]['content'].startswith('Task\n1. Create and improve strategy.py') for b in bodies)
    assert all('submit' not in [t['function']['name'] for t in b['tools']] for b in bodies)
    run.close()


def test_a_crash_after_a_model_reply_resumes_without_a_second_request(service, monkeypatch):
    sent = []

    def provider(*args, **kwargs):
        sent.append(1)
        return io.BytesIO(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'done'}}]}).encode())

    monkeypatch.setattr('urllib.request.urlopen', provider)
    service.state['phase'] = 'warmup'
    agent = CompatibleAgent({'model': 'unit', 'base_url': 'https://example.invalid'})

    class Crash(BaseException):
        pass

    durable, crashed = None, False

    def persist():
        nonlocal durable, crashed
        durable = copy.deepcopy(service.state)
        if service.state['model_outputs'] and not crashed:
            crashed = True
            raise Crash()

    service.persist = persist
    with pytest.raises(Crash):
        agent.warmup(service)
    service.state = durable
    agent.warmup(service)
    assert len(sent) == 1 and service.state['model_context']['done']
